"""Public student signup against migrated, isolated test databases only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Barrier
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from xueban_backend.account_auth import set_password_account
from xueban_backend.account_models import (
    LoginLimit, PasswordAccount, RegistrationCounter, StudentRegistration,
)
from xueban_backend.app import create_app
from xueban_backend.asset_store import LocalAssetStore
from xueban_backend.device_models import DeviceSession
from xueban_backend.governance import purge_student
from xueban_backend.learning_models import (
    CurriculumGraph, KnowledgeEdge, KnowledgeNode, StudentLearningProfile,
)
from xueban_backend.models import Principal, PrincipalRole, Student, StudentGrant
from xueban_backend.passwords import verify_password


PREFIX = '荣耀学员'
PASSWORD = 'Stu123'
DISABLED = {'enabled': False, 'username_preview': None, 'grades': []}


@pytest.fixture
def registration_settings(settings, signing, tmp_path):
    private = tmp_path / 'registration-issuer.pem'
    private.write_bytes(signing.private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    private.chmod(0o600)
    return replace(
        settings, signing_private_key_file=str(private),
        self_registration_enabled=True,
        self_registration_tenant_id='tenant_a',
        self_registration_campus_id='campus_a',
    )


@pytest.fixture
def registration(registration_settings):
    with TestClient(create_app(registration_settings)) as client:
        yield client


def body(**changes):
    return {'password': PASSWORD, 'confirm_password': PASSWORD, 'grade': 5, **changes}


def register(client, payload=None, key=None):
    return client.post('/v1/auth/register', json=body() if payload is None else payload,
                       headers={'Idempotency-Key': key or 'registration-' + uuid4().hex})


def login(client, name, password=PASSWORD):
    return client.post('/v1/auth/login', json={'username': name, 'password': password},
                       headers={'Idempotency-Key': 'login-' + uuid4().hex})


def created(response):
    assert response.status_code == 201, response.text
    value = response.json()
    assert set(value) == {'username', 'student_id', 'grade'}
    assert value['student_id'].startswith('student_')
    assert 'set-cookie' not in response.headers
    return value


def preview(client, name):
    response = client.get('/v1/auth/registration')
    assert response.status_code == 200, response.text
    assert response.json() == {'enabled': True, 'username_preview': name, 'grades': [5, 6, 7, 8]}


def assert_no_accounts(database):
    with Session(database.engine) as session:
        for model in (PasswordAccount, StudentRegistration, StudentLearningProfile, DeviceSession):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.scalar(select(func.count()).select_from(Principal)) == 2
        assert session.scalar(select(func.count()).select_from(Student)) == 3


def test_registration_is_disabled_by_default_and_public(settings, database):
    assert settings.self_registration_enabled is False
    assert settings.self_registration_tenant_id == ''
    assert settings.self_registration_campus_id == ''
    with TestClient(create_app(settings)) as client:
        response = client.get('/v1/auth/registration')
        assert response.status_code == 200
        assert response.json() == DISABLED
        assert register(client).status_code == 403
    assert_no_accounts(database)


@pytest.mark.parametrize('changes', [
    {'self_registration_enabled': False},
    {'self_registration_tenant_id': ''},
    {'self_registration_campus_id': ''},
    {'signing_private_key_file': ''},
    {'self_registration_tenant_id': 'tenant_b'},
    {'self_registration_campus_id': 'campus_missing'},
])
def test_registration_requires_explicit_matching_server_configuration(registration_settings, database, changes):
    with TestClient(create_app(replace(registration_settings, **changes))) as client:
        options = client.get('/v1/auth/registration')
        assert options.status_code == 200
        assert options.json() == DISABLED
        assert register(client).status_code == 403
    assert_no_accounts(database)


def test_preview_does_not_reserve_and_names_persist_across_app_instances(registration, registration_settings, database):
    preview(registration, '荣耀学员001')
    preview(registration, '荣耀学员001')
    with Session(database.engine) as session:
        assert session.get(RegistrationCounter, PREFIX) is None
    first = created(register(registration))
    assert first['username'] == '荣耀学员001'
    assert first['grade'] == 5
    assert registration.get('/v1/me').status_code == 401
    assert registration.get('/v1/auth/account').status_code == 401
    preview(registration, '荣耀学员002')
    with TestClient(create_app(registration_settings)) as other:
        preview(other, '荣耀学员002')
        second = created(register(other, body(grade=8)))
        assert second['username'] == '荣耀学员002'
        assert second['student_id'] != first['student_id']
    preview(registration, '荣耀学员003')
    with Session(database.engine) as session:
        assert session.get(RegistrationCounter, PREFIX).last_number == 2
        assert session.scalar(select(func.count()).select_from(PasswordAccount)) == 2
        assert session.scalar(select(func.count()).select_from(StudentRegistration)) == 2
        assert session.scalar(select(func.count()).select_from(DeviceSession)) == 0


@pytest.mark.parametrize(('existing', 'following'), [
    ('荣耀学员042', '荣耀学员043'), ('荣耀学员999', '荣耀学员1000'),
])
def test_existing_higher_suffix_overrides_stale_counter(registration, registration_settings, database, existing, following):
    with Session(database.engine) as session, session.begin():
        set_password_account(
            session, registration_settings, principal_id='actor_a', student_id='student_a',
            username_value=existing, password_value='synthetic existing password', must_change=False,
        )
        session.add(RegistrationCounter(name=PREFIX, last_number=2))
    preview(registration, following)
    assert created(register(registration))['username'] == following
    with Session(database.engine) as session:
        assert session.get(PasswordAccount, existing).student_id == 'student_a'
        assert session.get(RegistrationCounter, PREFIX).last_number == int(following[len(PREFIX):])


def test_registration_creates_only_server_owned_student_identity(registration, database, caplog):
    key = 'registration-secret-storage-check'
    value = created(register(registration, key=key))
    with Session(database.engine) as session:
        account = session.get(PasswordAccount, value['username'])
        student = session.get(Student, value['student_id'])
        principal = session.get(Principal, account.principal_id)
        assert (account.tenant_id, student.tenant_id, principal.tenant_id) == ('tenant_a',) * 3
        assert student.campus_id == 'campus_a'
        assert student.display_name == value['username']
        assert student.active and principal.active and account.active
        assert account.student_id == student.id
        assert account.must_change_password is False
        assert set(session.scalars(select(PrincipalRole.role).where(
            PrincipalRole.principal_id == principal.id))) == {'student'}
        grants = session.scalars(select(StudentGrant).where(StudentGrant.principal_id == principal.id)).all()
        assert len(grants) == 1
        assert grants[0].student_id == student.id and grants[0].tenant_id == 'tenant_a' and grants[0].active
        profile = session.get(StudentLearningProfile, student.id)
        assert profile.document['grade'] == 5
        assert profile.document['textbook_revision'] == 'publisher_latest'
        assert profile.tenant_id == 'tenant_a' and profile.updated_by == principal.id
        assert account.password_hash.startswith('scrypt$v1$131072$8$1$')
        assert verify_password(PASSWORD, account.password_hash)
        operation = session.get(StudentRegistration, hashlib.sha256(key.encode('ascii')).hexdigest())
        assert operation.username == value['username'] and operation.grade == 5
        assert len(operation.request_salt) == 32 and len(operation.request_hash) == 64
        assert operation.request_hash != hashlib.sha256(PASSWORD.encode()).hexdigest()
        stored = json.dumps({column.name: str(getattr(operation, column.name))
                             for column in StudentRegistration.__table__.columns})
        assert PASSWORD not in stored and key not in stored
        assert PASSWORD not in account.password_hash
        assert session.scalar(select(func.count()).select_from(DeviceSession)) == 0
    assert PASSWORD not in caplog.text and key not in caplog.text


def test_idempotent_recovery_is_persistent_and_changed_payload_conflicts(registration, registration_settings, database):
    key = 'registration-lost-response'
    original = created(register(registration, key=key))
    assert created(register(registration, key=key)) == original
    with TestClient(create_app(registration_settings)) as other:
        assert created(register(other, key=key)) == original
        for changed in (body(grade=6), body(password='Next456', confirm_password='Next456')):
            response = register(other, changed, key)
            assert response.status_code == 409, response.text
            assert response.json()['code'] == 'idempotency_conflict'
    with Session(database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PasswordAccount)) == 1
        assert session.scalar(select(func.count()).select_from(StudentRegistration)) == 1
        assert session.get(RegistrationCounter, PREFIX).last_number == 1
    preview(registration, '荣耀学员002')
    assert login(registration, original['username']).status_code == 200


def test_idempotency_key_cannot_be_reused_for_another_server_selected_campus(registration, registration_settings, database):
    key = 'registration-global-key'
    created(register(registration, key=key))
    other_settings = replace(registration_settings, self_registration_tenant_id='tenant_b',
                             self_registration_campus_id='campus_b')
    with TestClient(create_app(other_settings)) as other:
        response = register(other, key=key)
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'idempotency_conflict'
        assert created(register(other))['username'] == '荣耀学员002'
    with Session(database.engine) as session:
        assert session.get(PasswordAccount, '荣耀学员001').tenant_id == 'tenant_a'
        assert session.get(PasswordAccount, '荣耀学员002').tenant_id == 'tenant_b'
        records = session.scalars(select(StudentRegistration)).all()
        assert len({record.request_salt for record in records}) == 2
        assert len({record.request_hash for record in records}) == 2


@pytest.mark.parametrize('password', ['abc', '123', 'aB3', 'aB3' * 5])
def test_valid_password_boundaries_and_chinese_username_login(registration, password):
    account = created(register(registration, body(password=password, confirm_password=password)))
    response = login(registration, account['username'], password)
    assert response.status_code == 200, response.text
    assert response.json()['must_change_password'] is False
    headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
    assert registration.get('/v1/auth/account', headers=headers).json() == {
        'username': '荣耀学员001', 'must_change_password': False, 'student_id': account['student_id'],
    }
    assert registration.get('/v1/me', headers=headers).status_code == 200


@pytest.mark.parametrize('password', [
    '', 'ab', 'a' * 16, 'ab!', 'a b', 'ab\n', 'abc\t', 'é12', '１２３', '中文3',
    None, 123, True,
])
def test_invalid_passwords_do_not_create_accounts_or_consume_a_number(registration, database, password):
    response = register(registration, body(password=password, confirm_password=password))
    assert response.status_code == 422, response.text
    if isinstance(password, str) and password:
        assert password not in response.text
    assert_no_accounts(database)
    preview(registration, '荣耀学员001')


def test_confirmation_must_match_exactly_and_failed_request_can_be_corrected(registration, database):
    key = 'registration-confirmation-retry'
    response = register(registration, body(confirm_password='stu123'), key)
    assert response.status_code == 422, response.text
    assert PASSWORD not in response.text and 'stu123' not in response.text
    assert_no_accounts(database)
    assert created(register(registration, key=key))['username'] == '荣耀学员001'


@pytest.mark.parametrize('field', ['password', 'confirm_password', 'grade'])
def test_all_registration_fields_are_required(registration, database, field):
    payload = body()
    del payload[field]
    assert register(registration, payload).status_code == 422
    assert_no_accounts(database)


@pytest.mark.parametrize('grade', [0, 4, 9, 12, -1, None, '5', 5.0, 5.5, True])
def test_grade_is_a_required_integer_from_five_through_eight(registration, database, grade):
    response = register(registration, body(grade=grade))
    assert response.status_code == 422, response.text
    assert_no_accounts(database)


@pytest.mark.parametrize(('field', 'value'), [
    ('username', '荣耀学员999'), ('student_id', 'student_b'), ('principal_id', 'actor_b'),
    ('tenant_id', 'tenant_b'), ('campus_id', 'campus_b'), ('role', 'campus_admin'),
    ('roles', ['campus_admin']), ('student_name', '客户端指定学员'),
])
def test_client_cannot_choose_account_ownership_role_or_username(registration, database, field, value):
    response = register(registration, body(**{field: value}))
    assert response.status_code == 422, response.text
    assert_no_accounts(database)


@pytest.mark.parametrize('key', [None, 'short', 'invalid key', 'a' * 129])
def test_registration_requires_a_valid_idempotency_key(registration, database, key):
    headers = {} if key is None else {'Idempotency-Key': key}
    response = registration.post('/v1/auth/register', json=body(), headers=headers)
    assert response.status_code == 422, response.text
    assert_no_accounts(database)


@pytest.fixture
def grade_scoped_graph(database, monkeypatch):
    """Four synthetic math nodes make accidental cross-grade access observable."""
    from xueban_backend import curriculum_catalog

    version = 'registration-grade-scope-test'
    publishers = {5: '人民教育出版社', 6: '人民教育出版社', 7: '北京师范大学出版社', 8: '北京师范大学出版社'}
    monkeypatch.setattr(curriculum_catalog, '_official_catalog', lambda: {
        'verified_at': '2026-10-28T00:00:00+00:00',
        'requested_scopes': [{'grade': grade, 'subject': '数学', 'publisher': publisher, 'status': 'available'}
                             for grade, publisher in publishers.items()],
    })
    monkeypatch.setattr(curriculum_catalog, '_official_books', lambda: ())
    with Session(database.engine) as session, session.begin():
        session.add(CurriculumGraph(
            version=version, subject='数学', title='合成注册年级范围测试', status='published',
            review_status='publisher_latest_reviewed', source_note='仅限隔离测试数据库。',
            published_at=datetime.now(timezone.utc),
        ))
        session.flush()
        for grade, publisher in publishers.items():
            session.add(KnowledgeNode(
                graph_version=version, id=f'registration_grade_{grade}', node_type='skill',
                parent_id=None, name=f'合成{grade}年级知识点', definition=None, observable_objective=None,
                curriculum_refs=[{'grade': grade, 'subject': '数学', 'publisher': publisher,
                                  'stage': 'primary' if grade <= 6 else 'junior_middle',
                                  'term': 'upper', 'textbook_revision': 'publisher_latest'}],
                coverage_facets=[], misconception_codes=[], sort_order=grade,
            ))
        session.add(KnowledgeEdge(
            graph_version=version, id='registration_cross_grade', from_id='registration_grade_5',
            to_id='registration_grade_8', relation_type='prerequisite_of', scope=None, rationale=None,
        ))
    return version


@pytest.mark.parametrize('grade', [5, 6, 7, 8])
def test_selected_grade_controls_profile_knowledge_and_curriculum(registration, grade_scoped_graph, payload, grade):
    account = created(register(registration, body(grade=grade)))
    assert account['grade'] == grade
    signed_in = login(registration, account['username'])
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()['must_change_password'] is False
    headers = {'Authorization': 'Bearer ' + signed_in.json()['access_token']}
    student_id = account['student_id']
    identity = registration.get('/v1/me', headers=headers)
    assert identity.status_code == 200, identity.text
    assert identity.json()['roles'] == ['student']
    assert identity.json()['authorized_students'] == [{
        'student_id': student_id, 'display_name': account['username'], 'campus_id': 'campus_a',
    }]
    profile = registration.get(f'/v1/students/{student_id}/learning-profile', headers=headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()['grade'] == grade
    knowledge = registration.get(f'/v1/students/{student_id}/knowledge', headers=headers)
    assert knowledge.status_code == 200, knowledge.text
    assert knowledge.json()['grade'] == grade
    assert knowledge.json()['graph_version'] == grade_scoped_graph
    assert [node['id'] for node in knowledge.json()['nodes']] == [f'registration_grade_{grade}']
    assert knowledge.json()['edges'] == []
    assert all(subject['grade'] == grade for subject in knowledge.json()['subjects'])
    other_grade = 8 if grade == 5 else 5
    assert registration.get(f'/v1/students/{student_id}/knowledge/registration_grade_{other_grade}',
                            headers=headers).status_code == 404
    curriculum = registration.get(f'/v1/students/{student_id}/curriculum', headers=headers)
    assert curriculum.status_code == 200, curriculum.text
    course = curriculum.json()
    stage = 'primary' if grade <= 6 else 'junior_middle'
    assert course['grade'] == grade and course['stage'] == stage
    assert course['subjects'] and all(subject['grade'] == grade and subject['stage'] == stage
                                    for subject in course['subjects'])
    math = next(subject for subject in course['subjects'] if subject['subject'] == '数学')
    assert math['publisher_code'] == ('PEP' if grade <= 6 else 'BNUP')
    for other in ('student_a', 'student_ungranted', 'student_b'):
        for resource in ('learning-profile', 'knowledge', 'curriculum'):
            assert registration.get(f'/v1/students/{other}/{resource}', headers=headers).status_code == 404
    assert registration.get('/v1/admin/student-accounts', headers=headers).status_code == 403
    homework = registration.post(f'/v1/students/{student_id}/homeworks', json=payload,
                                 headers={**headers, 'Idempotency-Key': 'registration-grade-homework'})
    assert homework.status_code == 201, homework.text
    assert homework.json()['curriculum']['grade'] == grade
    assert homework.json()['curriculum']['stage'] == stage
    wrong_grade = {**payload, 'curriculum': {
        **payload['curriculum'], 'grade': other_grade,
        'stage': 'primary' if other_grade <= 6 else 'junior_middle',
    }}
    rejected = registration.post(f'/v1/students/{student_id}/homeworks', json=wrong_grade,
                                 headers={**headers, 'Idempotency-Key': 'registration-wrong-grade'})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['code'] == 'course_unknown'


@pytest.mark.parametrize('identical_key', [False, True])
def test_concurrent_requests_across_app_instances_allocate_exactly_once(registration_settings, database, identical_key):
    barrier = Barrier(2)
    with TestClient(create_app(registration_settings)) as first, TestClient(create_app(registration_settings)) as second:
        def submit(item):
            index, client = item
            barrier.wait(timeout=10)
            return register(client, key='registration-concurrent' if identical_key else f'registration-concurrent-{index}')

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, enumerate((first, second))))
        values = [created(response) for response in results]
        if identical_key:
            assert values[0] == values[1]
            assert values[0]['username'] == '荣耀学员001'
        else:
            assert {value['username'] for value in values} == {'荣耀学员001', '荣耀学员002'}
            assert len({value['student_id'] for value in values}) == 2
    expected = 1 if identical_key else 2
    with Session(database.engine) as session:
        assert session.get(RegistrationCounter, PREFIX).last_number == expected
        for model in (PasswordAccount, StudentRegistration, StudentLearningProfile):
            assert session.scalar(select(func.count()).select_from(model)) == expected
        assert session.scalar(select(func.count()).select_from(DeviceSession)) == 0


def test_registration_rate_limit_persists_but_original_replay_still_recovers(registration, registration_settings, database):
    key = 'registration-before-rate-limit'
    original = created(register(registration, key=key))
    with Session(database.engine) as session, session.begin():
        limiter = session.scalars(select(LoginLimit)).one()
        registration_limit_key = limiter.key
        assert limiter.count == 1
        # Reach the documented limit without 19 redundant scrypt hashes.
        limiter.count = 20
    rejected = register(registration)
    assert rejected.status_code == 429, rejected.text
    assert rejected.json()['code'] == 'login_rate_limited'
    with TestClient(create_app(registration_settings)) as other:
        assert register(other).status_code == 429
        assert created(register(other, key=key)) == original
    with Session(database.engine) as session, session.begin():
        assert session.get(RegistrationCounter, PREFIX).last_number == 1
        assert session.scalar(select(func.count()).select_from(PasswordAccount)) == 1
        assert session.scalar(select(func.count()).select_from(StudentRegistration)) == 1
        limiter = session.get(LoginLimit, registration_limit_key)
        assert limiter.locked_until is not None
        recovery = session.scalars(select(LoginLimit).where(LoginLimit.key != registration_limit_key)).one()
        assert recovery.count == 1 and recovery.locked_until is None
        limiter.window_started = datetime.now(timezone.utc) - timedelta(seconds=61)
        limiter.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert created(register(registration))['username'] == '荣耀学员002'


@pytest.mark.parametrize('matching_payload', [False, True])
def test_recovery_has_its_own_persistent_limit_for_wrong_and_correct_payloads(
        registration, registration_settings, database, matching_payload):
    key = 'registration-recovery-rate-limit'
    original = created(register(registration, key=key))
    replay_body = body() if matching_payload else body(password='Wrong123', confirm_password='Wrong123')
    with Session(database.engine) as session:
        registration_limit_key = session.scalars(select(LoginLimit.key)).one()
    for _ in range(20):
        response = register(registration, replay_body, key)
        if matching_payload:
            assert created(response) == original
        else:
            assert response.status_code == 409, response.text
            assert response.json()['code'] == 'idempotency_conflict'
    with Session(database.engine) as session:
        recovery = session.scalars(select(LoginLimit).where(LoginLimit.key != registration_limit_key)).one()
        recovery_limit_key = recovery.key
        assert recovery.count == 20 and recovery.locked_until is None
        assert session.get(LoginLimit, registration_limit_key).count == 1
    # A new application instance must see both committed 409 failures and 429 lockout.
    with TestClient(create_app(registration_settings)) as other:
        for candidate in (replay_body, body()):
            blocked = register(other, candidate, key)
            assert blocked.status_code == 429, blocked.text
            assert blocked.json()['code'] == 'login_rate_limited'
    with Session(database.engine) as session, session.begin():
        recovery = session.get(LoginLimit, recovery_limit_key)
        assert recovery.count == 21 and recovery.locked_until is not None
        assert session.get(LoginLimit, registration_limit_key).count == 1
        assert session.get(RegistrationCounter, PREFIX).last_number == 1
        for model in (PasswordAccount, StudentRegistration, StudentLearningProfile):
            assert session.scalar(select(func.count()).select_from(model)) == 1
        assert session.scalar(select(func.count()).select_from(DeviceSession)) == 0
        recovery.window_started = datetime.now(timezone.utc) - timedelta(seconds=61)
        recovery.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert created(register(registration, key=key)) == original
    with Session(database.engine) as session:
        assert session.get(LoginLimit, recovery_limit_key).count == 1
        assert session.get(RegistrationCounter, PREFIX).last_number == 1


@pytest.mark.parametrize('missing_account', [False, True])
def test_recovery_returns_stale_input_if_account_binding_is_unavailable(registration, database, monkeypatch, missing_account):
    key = 'registration-stale-binding'
    original = created(register(registration, key=key))
    if missing_account:
        # The FK normally prevents an orphan; simulate an unavailable account lookup.
        original_get = Session.get

        def unavailable_account(session, entity, identity, *args, **kwargs):
            if entity is PasswordAccount and identity == original['username']:
                return None
            return original_get(session, entity, identity, *args, **kwargs)

        monkeypatch.setattr(Session, 'get', unavailable_account)
    else:
        with Session(database.engine) as session, session.begin():
            session.get(PasswordAccount, original['username']).student_id = None
    response = register(registration, key=key)
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'stale_input'
    with Session(database.engine) as session:
        assert session.get(RegistrationCounter, PREFIX).last_number == 1
        assert session.scalar(select(func.count()).select_from(PasswordAccount)) == 1
        assert session.scalar(select(func.count()).select_from(StudentRegistration)) == 1


def test_student_deletion_cascades_registration_record_without_reusing_number(registration, registration_settings, database, tmp_path):
    key = 'registration-before-deletion'
    original = created(register(registration, key=key))
    signed_in = login(registration, original['username'])
    assert signed_in.status_code == 200, signed_in.text
    token = signed_in.json()['access_token']
    key_hash = hashlib.sha256(key.encode('ascii')).hexdigest()
    with Session(database.engine) as session, session.begin():
        assert session.get(StudentRegistration, key_hash) is not None
        purge_student(session, LocalAssetStore(tmp_path / 'registration-private-assets'),
                      'tenant_a', original['student_id'])
    with Session(database.engine) as session:
        assert session.get(PasswordAccount, original['username']) is None
        assert session.get(StudentRegistration, key_hash) is None
        assert session.get(StudentLearningProfile, original['student_id']) is None
        assert session.get(Student, original['student_id']).active is False
        assert session.get(RegistrationCounter, PREFIX).last_number == 1
    revoked = registration.get('/v1/me', headers={'Authorization': 'Bearer ' + token})
    assert revoked.status_code == 403, revoked.text
    assert revoked.json()['code'] == 'access_denied'
    assert login(registration, original['username']).status_code == 401
    with TestClient(create_app(registration_settings)) as other:
        preview(other, '荣耀学员002')
        following = created(register(other))
        assert following['username'] == '荣耀学员002'
        assert following['student_id'] != original['student_id']
