"""Password accounts use real migrations, RSA signatures and runtime-only credentials."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from conftest import migrate
from xueban_backend.account_auth import grant_campus, set_password_account
from xueban_backend.account_models import AuthOperation, LoginLimit, PasswordAccount, PrincipalCampusGrant
from xueban_backend.admin import bootstrap_admin
from xueban_backend.app import create_app
from xueban_backend.device_auth import issue_pairing
from xueban_backend.device_models import DeviceSession
from xueban_backend.models import Campus, Homework, Principal, PrincipalRole, Student, StudentGrant

PASSWORD = 'original synthetic password'
NEW_PASSWORD = 'changed synthetic password'
RESET_PASSWORD = 'reset synthetic password'


@pytest.fixture
def accounts(settings, signing, tmp_path, database):
    private = tmp_path / 'account-issuer.pem'
    private.write_bytes(signing.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    cfg = replace(settings, signing_private_key_file=str(private))
    with Session(database.engine) as session, session.begin():
        old_pair = issue_pairing(session, cfg, 'actor_a')
        set_password_account(session, cfg, principal_id='actor_a', student_id='student_a', username_value='student.alpha', password_value=PASSWORD)
        session.add(Principal(id='admin_a', tenant_id='tenant_a', issuer=cfg.oidc_issuer, subject='admin-subject', active=True)); session.flush()
        session.add(PrincipalRole(principal_id='admin_a', role='campus_admin')); session.flush()
        grant_campus(session, 'admin_a', 'campus_a')
        set_password_account(session, cfg, principal_id='admin_a', username_value='admin.alpha', password_value=PASSWORD, must_change=False)
    with TestClient(create_app(cfg)) as client:
        yield client, cfg, old_pair


def post(client, path, payload, token=None, key=None):
    headers = {'Idempotency-Key': key or 'test-' + uuid4().hex}
    if token: headers['Authorization'] = 'Bearer ' + token
    return client.post(path, json=payload, headers=headers)


def login(client, username='student.alpha', password=PASSWORD, key=None):
    return post(client, '/v1/auth/login', {'username': username, 'password': password}, key=key)


def bearer(token):
    return {'Authorization': 'Bearer ' + token}


def permanent(client):
    old = login(client).json()['access_token']
    response = post(client, '/v1/auth/change-password', {'current_password': PASSWORD, 'new_password': NEW_PASSWORD}, old)
    assert response.status_code == 200, response.text
    return response.json()['access_token']


def test_forced_change_preserves_identity_and_revokes_old_token(accounts, database, signing, payload):
    client, cfg, old_pair = accounts
    first = login(client, ' Student.Alpha ')
    assert first.status_code == 200, first.text
    initial = first.json(); token = initial['access_token']
    assert initial['must_change_password'] is True
    assert client.get('/v1/auth/account', headers=bearer(token)).json() == {'username': 'student.alpha', 'must_change_password': True, 'student_id': 'student_a'}
    assert client.get('/v1/me', headers=bearer(token)).json()['code'] == 'password_change_required'
    assert post(client, '/v1/students/student_a/homeworks', payload, token).status_code == 403
    assert client.get('/v1/me', headers=bearer(signing.token())).status_code == 401
    assert post(client, '/v1/device-sessions', {'pairing_code': old_pair}).status_code == 401
    with Session(database.engine) as session:
        with pytest.raises(ValueError, match='pairing is disabled'):
            issue_pairing(session, cfg, 'actor_a')
    key = 'change-once-unknown-response'
    body = {'current_password': PASSWORD, 'new_password': NEW_PASSWORD}
    changed = post(client, '/v1/auth/change-password', body, token, key)
    assert changed.status_code == 200, changed.text
    new = changed.json()
    assert new['must_change_password'] is False
    assert post(client, '/v1/auth/change-password', body, token, key).json() == new
    assert post(client, '/v1/auth/change-password', body, token, 'new-key-after-revocation').status_code == 401
    assert post(client, '/v1/auth/change-password', {**body, 'new_password': RESET_PASSWORD}, token, key).status_code == 409
    assert client.get('/v1/me', headers=bearer(token)).status_code == 401
    identity = client.get('/v1/me', headers=bearer(new['access_token'])).json()
    assert identity['actor_id'] == 'actor_a'
    assert [s['student_id'] for s in identity['authorized_students']] == ['student_a']
    assert login(client, password=PASSWORD).status_code == 401
    assert login(client, password=NEW_PASSWORD).json()['must_change_password'] is False
    created = post(client, '/v1/students/student_a/homeworks', payload, new['access_token'])
    assert created.status_code == 201, created.text
    assert created.json()['scope']['student_id'] == 'student_a'


def test_login_idempotency_persistence_and_no_credential_storage(accounts, database, caplog):
    client, cfg, _ = accounts
    key = 'lost-login-response'
    first = login(client, key=key)
    assert first.status_code == 200
    with TestClient(create_app(cfg)) as restarted:
        assert login(restarted, key=key).json() == first.json()
    with Session(database.engine) as session:
        operations = session.scalars(select(AuthOperation)).all()
        assert len(operations) == 1
        raw = json.dumps([{c.name: str(getattr(row, c.name)) for c in AuthOperation.__table__.columns} for row in operations])
        assert PASSWORD not in raw and first.json()['access_token'] not in raw and key not in raw
        assert operations[0].request_hash != __import__('hashlib').sha256(PASSWORD.encode()).hexdigest()
        assert session.scalar(select(func.count()).select_from(DeviceSession).where(DeviceSession.principal_id == 'actor_a')) == 1
        stored = session.get(PasswordAccount, 'student.alpha')
        assert stored.password_hash.startswith('scrypt$v1$131072$8$1$')
        assert PASSWORD not in stored.password_hash
    assert PASSWORD not in caplog.text and first.json()['access_token'] not in caplog.text


def test_student_binding_overrides_accidental_extra_grant(accounts, database, payload):
    client, _, _ = accounts
    token = permanent(client)
    with Session(database.engine) as session, session.begin():
        session.add(StudentGrant(principal_id='actor_a', student_id='student_ungranted', tenant_id='tenant_a', active=True))
    me = client.get('/v1/me', headers=bearer(token)).json()
    assert [s['student_id'] for s in me['authorized_students']] == ['student_a']
    assert client.get('/v1/students/student_ungranted/homeworks', headers=bearer(token)).status_code == 404
    assert post(client, '/v1/students/student_ungranted/homeworks', payload, token).status_code == 404
    assert post(client, '/v1/students/student_b/homeworks', payload, token).status_code == 404
    assert client.get('/v1/admin/student-accounts', headers=bearer(token)).status_code == 403


def test_disabled_account_or_revoked_student_cannot_use_session(accounts, database):
    client, _, _ = accounts
    token = permanent(client)
    with Session(database.engine) as session, session.begin():
        session.get(StudentGrant, ('actor_a', 'student_a')).active = False
    assert client.get('/v1/auth/account', headers=bearer(token)).status_code == 401
    assert client.get('/v1/me', headers=bearer(token)).status_code == 403
    assert login(client, password=NEW_PASSWORD).status_code == 401
    with Session(database.engine) as session, session.begin():
        session.get(StudentGrant, ('actor_a', 'student_a')).active = True
        session.get(PasswordAccount, 'student.alpha').active = False
    assert login(client, password=NEW_PASSWORD).status_code == 401
    assert client.get('/v1/me', headers=bearer(token)).status_code == 401


def test_password_lockout_is_database_persistent(accounts, database):
    client, cfg, _ = accounts
    for _ in range(5):
        assert login(client, password='wrong synthetic password').status_code == 401
    assert login(client).status_code == 429
    with TestClient(create_app(cfg)) as restarted:
        assert login(restarted).json()['code'] == 'login_rate_limited'
    with Session(database.engine) as session, session.begin():
        row = session.get(PasswordAccount, 'student.alpha')
        assert row.failed_attempts == 5
        row.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert login(client).status_code == 200
    with Session(database.engine) as session:
        assert session.get(PasswordAccount, 'student.alpha').failed_attempts == 0


def test_unknown_username_has_generic_error_and_persistent_throttle(accounts):
    client, cfg, _ = accounts
    for _ in range(5):
        r = login(client, username='unknown.synthetic')
        assert r.status_code == 401 and r.json()['code'] == 'invalid_credentials'
    with TestClient(create_app(cfg)) as restarted:
        assert login(restarted, username='unknown.synthetic').status_code == 429


def test_logout_invalidates_session_and_replays(accounts):
    client, _, _ = accounts
    token = login(client).json()['access_token']
    assert post(client, '/v1/auth/logout', {}, token, 'logout-request-one').json() == {'logged_out': True}
    assert post(client, '/v1/auth/logout', {}, token, 'logout-request-one').json() == {'logged_out': True}
    assert client.get('/v1/auth/account', headers=bearer(token)).status_code == 401
    assert post(client, '/v1/auth/change-password', {'current_password': PASSWORD, 'new_password': NEW_PASSWORD}, token).status_code == 401


def test_admin_create_is_idempotent_scoped_and_forces_change(accounts, database):
    client, _, _ = accounts
    admin = login(client, 'admin.alpha').json()['access_token']
    body = {'username': 'new.student', 'student_name': '  合成新学员  ', 'campus_id': 'campus_a', 'initial_password': PASSWORD}
    created = post(client, '/v1/admin/student-accounts', body, admin, 'new-student-request')
    assert created.status_code == 201, created.text
    item = created.json()
    assert item['student_name'] == '合成新学员' and item['must_change_password'] and item['active']
    assert post(client, '/v1/admin/student-accounts', body, admin, 'new-student-request').json() == item
    assert post(client, '/v1/admin/student-accounts', body, admin).json()['code'] == 'username_conflict'
    assert post(client, '/v1/admin/student-accounts', {**body, 'student_name': '另一个'}, admin, 'new-student-request').json()['code'] == 'idempotency_conflict'
    listing = client.get('/v1/admin/student-accounts', headers=bearer(admin)).json()
    assert listing['campuses'] == [{'campus_id': 'campus_a'}]
    assert item in listing['items']
    fresh = login(client, 'new.student').json()
    assert fresh['must_change_password']
    assert client.get('/v1/auth/account', headers=bearer(fresh['access_token'])).json()['student_id'] == item['student_id']
    with Session(database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PasswordAccount).where(PasswordAccount.username == 'new.student')) == 1
        assert session.get(StudentGrant, ('admin_a', item['student_id'])) is None
        assert session.get(StudentGrant, (item['principal_id'], item['student_id'])).active


def test_admin_campus_and_tenant_boundaries(accounts, database, signing):
    client, cfg, _ = accounts
    admin = login(client, 'admin.alpha').json()['access_token']
    with Session(database.engine) as session, session.begin():
        session.add(Campus(id='campus_other', tenant_id='tenant_a'))
        # A same-tenant campus without explicit grant must also be rejected.
        session.add(PrincipalRole(principal_id='actor_b', role='campus_admin'))
    base = {'username': 'no.student', 'student_name': '合成学员', 'initial_password': PASSWORD}
    for campus in ('campus_b', 'campus_other'):
        assert post(client, '/v1/admin/student-accounts', {**base, 'campus_id': campus}, admin).status_code == 403
    assert post(client, '/v1/admin/student-accounts/student_b/reset-password', {'new_password': NEW_PASSWORD}, admin).status_code == 404
    # A role plus an existing student grant never implies campus management.
    other = signing.token('subject-b')
    assert client.get('/v1/admin/student-accounts', headers=bearer(other)).json() == {'items': [], 'campuses': []}
    assert post(client, '/v1/admin/student-accounts', {**base, 'campus_id': 'campus_b'}, other).status_code == 403
    with Session(database.engine) as session:
        with pytest.raises(ValueError, match='same-tenant'):
            grant_campus(session, 'admin_a', 'campus_b')


def test_admin_reset_revokes_sessions_replays_and_preserves_history(accounts, database, payload):
    client, _, _ = accounts
    token = permanent(client)
    homework = post(client, '/v1/students/student_a/homeworks', payload, token).json()
    admin = login(client, 'admin.alpha').json()['access_token']
    body = {'new_password': RESET_PASSWORD}; path = '/v1/admin/student-accounts/student_a/reset-password'
    reset = post(client, path, body, admin, 'reset-original-key')
    assert reset.json() == {'student_id': 'student_a', 'must_change_password': True}
    assert post(client, path, body, admin, 'reset-original-key').json() == reset.json()
    assert client.get('/v1/auth/account', headers=bearer(token)).status_code == 401
    assert client.get('/v1/me', headers=bearer(token)).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 401
    interim = login(client, password=RESET_PASSWORD).json()['access_token']
    assert client.get('/v1/me', headers=bearer(interim)).json()['code'] == 'password_change_required'
    changed = post(client, '/v1/auth/change-password', {'current_password': RESET_PASSWORD, 'new_password': NEW_PASSWORD}, interim).json()['access_token']
    assert client.get('/v1/homeworks/' + homework['id'], headers=bearer(changed)).json()['id'] == homework['id']
    assert post(client, path, body, admin, 'reset-original-key').json()['code'] == 'stale_input'
    with Session(database.engine) as session:
        assert session.get(Homework, homework['id']).student_id == 'student_a'
        assert session.get(PasswordAccount, 'student.alpha').principal_id == 'actor_a'


@pytest.mark.parametrize('body', [
    {'username': 'ab', 'password': PASSWORD},
    {'username': 'bad!name', 'password': PASSWORD},
    {'username': 'student.alpha', 'password': 'short'},
    {'username': 'student.alpha', 'password': 'a'*129},
    {'username': 'student.alpha', 'password': PASSWORD, 'role': 'campus_admin'},
])
def test_login_validation_never_echoes_password(accounts, body):
    client, _, _ = accounts
    response = post(client, '/v1/auth/login', body)
    assert response.status_code == 422
    assert body['password'] not in response.text


def test_missing_signing_service_and_idempotency_key_fail_closed(accounts):
    client, cfg, _ = accounts
    assert client.post('/v1/auth/login', json={'username': 'student.alpha', 'password': PASSWORD}).status_code == 422
    with TestClient(create_app(replace(cfg, signing_private_key_file=None))) as missing:
        assert login(missing).status_code == 503


def test_bootstrap_admin_needs_no_students_then_creates_first(accounts, database, tmp_path):
    client, cfg, _ = accounts
    password_file = tmp_path / 'bootstrap-password.txt'; password_file.write_text(PASSWORD); password_file.chmod(0o600)
    values = dict(tenant_id='tenant_empty', campus_id='campus_empty', principal_id='admin_empty', subject='empty-admin-subject', username='admin.empty', password_file=password_file)
    with Session(database.engine) as session, session.begin():
        bootstrap_admin(session, cfg, **values)
    with Session(database.engine) as session:
        assert session.scalar(select(func.count()).select_from(Student).where(Student.tenant_id == 'tenant_empty')) == 0
        assert session.scalar(select(func.count()).select_from(StudentGrant).where(StudentGrant.principal_id == 'admin_empty')) == 0
        assert session.get(PrincipalCampusGrant, ('admin_empty', 'campus_empty')).active
        with pytest.raises(ValueError, match='Account already exists'):
            bootstrap_admin(session, cfg, **values)
    initial = login(client, 'admin.empty').json()['access_token']
    assert client.get('/v1/admin/student-accounts', headers=bearer(initial)).json()['code'] == 'password_change_required'
    admin = post(client, '/v1/auth/change-password', {'current_password': PASSWORD, 'new_password': NEW_PASSWORD}, initial).json()['access_token']
    assert client.get('/v1/admin/student-accounts', headers=bearer(admin)).json() == {'items': [], 'campuses': [{'campus_id': 'campus_empty'}]}
    first = post(client, '/v1/admin/student-accounts', {'username': 'first.student', 'student_name': '首位合成学员', 'campus_id': 'campus_empty', 'initial_password': PASSWORD}, admin)
    assert first.status_code == 201, first.text
    assert first.json()['campus_id'] == 'campus_empty'
    with Session(database.engine) as session:
        with pytest.raises(ValueError, match='role'):
            bootstrap_admin(session, cfg, **{**values, 'tenant_id': 'tenant_a', 'campus_id': 'campus_a', 'principal_id': 'actor_a', 'subject': 'subject-a'})


def test_pg_concurrent_login_and_admin_create_are_single_operations(accounts, database):
    if database.dialect != 'postgresql': pytest.skip('Real PostgreSQL row locks required')
    client, _, _ = accounts
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: login(client, key='concurrent-login-key'), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json() == results[1].json()
    admin = login(client, 'admin.alpha').json()['access_token']
    body = {'username': 'concurrent.student', 'student_name': '合成并发学生', 'campus_id': 'campus_a', 'initial_password': PASSWORD}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: post(client, '/v1/admin/student-accounts', body, admin, 'concurrent-create-key'), range(2)))
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json() == results[1].json()


def test_migration_can_upgrade_twice_with_accounts(accounts, database):
    client, _, _ = accounts
    initial = login(client).json()
    migrate(database.url); migrate(database.url)
    assert client.get('/v1/auth/account', headers=bearer(initial['access_token'])).status_code == 200


def test_unknown_create_replay_after_password_reset_is_stale(accounts):
    client, _, _ = accounts
    admin = login(client, 'admin.alpha').json()['access_token']
    body = {'username': 'lost.created', 'student_name': '合成学生', 'campus_id': 'campus_a', 'initial_password': PASSWORD}
    first = post(client, '/v1/admin/student-accounts', body, admin, 'lost-create-password-key').json()
    reset = post(client, '/v1/admin/student-accounts/' + first['student_id'] + '/reset-password', {'new_password': RESET_PASSWORD}, admin)
    assert reset.status_code == 200
    replay = post(client, '/v1/admin/student-accounts', body, admin, 'lost-create-password-key')
    assert replay.status_code == 409 and replay.json()['code'] == 'stale_input'
    assert login(client, 'lost.created', RESET_PASSWORD).status_code == 200
    assert login(client, 'lost.created', PASSWORD).status_code == 401


def test_reset_invalidates_already_issued_private_asset_tickets(accounts, database, tmp_path, payload):
    from test_correction_flow import jpeg
    import hashlib
    client, cfg, _ = accounts
    token = permanent(client)
    admin = login(client, 'admin.alpha').json()['access_token']
    with TestClient(create_app(replace(cfg, asset_storage_dir=tmp_path / 'images', public_base_url='http://testserver'))) as images:
        homework = post(images, '/v1/students/student_a/homeworks', payload, token).json()
        content = jpeg()
        ticket = post(images, '/v1/homeworks/' + homework['id'] + '/uploads', {'expected_revision': homework['revision'], 'page_index': 0, 'sha256': hashlib.sha256(content).hexdigest(), 'byte_size': len(content), 'mime_type': 'image/jpeg', 'width': 120, 'height': 80}, token).json()
        assert images.put(ticket['put_url'], content=content, headers=ticket['required_headers']).status_code == 204
        asset = ticket['asset']
        completed = post(images, '/v1/assets/' + asset['id'] + '/complete', {'expected_revision': asset['revision'], 'sha256': asset['sha256'], 'byte_size': asset['byte_size']}, token)
        assert completed.status_code == 200
        read = images.get('/v1/assets/' + asset['id'] + '/access', headers=bearer(token)).json()
        assert images.get(read['read_url']).status_code == 200
        assert post(images, '/v1/admin/student-accounts/student_a/reset-password', {'new_password': RESET_PASSWORD}, admin).status_code == 200
        assert images.get(read['read_url']).status_code == 401
        interim = login(images, password=RESET_PASSWORD).json()['access_token']
        assert images.get('/v1/assets/' + asset['id'] + '/access', headers=bearer(interim)).json()['code'] == 'password_change_required'


def test_account_binding_cannot_reassign_or_elevate_existing_principal(accounts, database):
    _, cfg, _ = accounts
    with Session(database.engine) as session:
        with pytest.raises(ValueError, match='binding'):
            set_password_account(session, cfg, principal_id='actor_a', student_id='student_a', username_value='renamed.student', password_value=PASSWORD)
        session.rollback()
        with pytest.raises(ValueError, match='matching student grant'):
            set_password_account(session, cfg, principal_id='actor_a', student_id='student_ungranted', username_value='student.alpha', password_value=PASSWORD)
        session.rollback()
        with pytest.raises(ValueError, match='staff role'):
            set_password_account(session, cfg, principal_id='actor_a', username_value='student.alpha', password_value=PASSWORD)
