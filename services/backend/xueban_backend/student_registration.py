"""Temporary, explicitly enabled student signup. Ownership is server-selected."""
from datetime import datetime, timezone
import hashlib
import hmac
import re
import secrets
from uuid import uuid4

from fastapi import Request
from pydantic import Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_auth import KEY, Strict, fingerprint, limit, mac_key, password
from .account_models import PasswordAccount, RegistrationCounter, StudentRegistration
from .device_auth import signing_key
from .errors import Problem
from .learning_models import StudentLearningProfile
from .models import Campus, Principal, PrincipalRole, Student, StudentGrant
from .passwords import hash_password, validate_student_password

PREFIX = '荣耀学员'
GRADES = [5, 6, 7, 8]


class RegisterStudent(Strict):
    password: SecretStr = Field(max_length=128)
    confirm_password: SecretStr = Field(max_length=128)
    grade: int = Field(strict=True, ge=5, le=8)


def registration_configured(cfg):
    return bool(cfg.self_registration_enabled and cfg.self_registration_tenant_id
                and cfg.self_registration_campus_id and cfg.signing_private_key_file and cfg.auth_configured)


def registration_campus(session, cfg):
    campus = session.get(Campus, cfg.self_registration_campus_id)
    return campus if campus and campus.tenant_id == cfg.self_registration_tenant_id else None


def next_number(session, counter):
    # Existing accounts may predate signup or have been imported by an admin.
    # Use the largest suffix, not row count (gaps/deletions are not recycled).
    highest = counter.last_number if counter else 0
    for name in session.scalars(select(PasswordAccount.username).where(PasswordAccount.username.like(PREFIX + '%'))):
        match = re.fullmatch(PREFIX + r'([0-9]{3,60})', name)
        if match:
            highest = max(highest, int(match[1]))
    # SQL INTEGER is shared with PostgreSQL. Never silently wrap the counter.
    if highest >= 2_147_483_647:
        raise Problem(503, 'service', 'service_unavailable', '自动账号编号暂不可用，请联系老师。')
    return highest + 1


def lock_counter(session):
    if session.get_bind().dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    # First SQL statement is a write: this also serializes SQLite writers,
    # avoiding a deferred read snapshot that cannot be upgraded under WAL.
    session.execute(insert(RegistrationCounter).values(name=PREFIX, last_number=0)
                    .on_conflict_do_nothing(index_elements=['name']))
    return session.scalar(select(RegistrationCounter).where(RegistrationCounter.name == PREFIX).with_for_update())


def install_student_registration(app, engine, cfg):
    @app.get('/v1/auth/registration')
    def registration_options():
        disabled = {'enabled': False, 'username_preview': None, 'grades': []}
        if not registration_configured(cfg):
            return disabled
        with Session(engine) as session:
            if registration_campus(session, cfg) is None:
                return disabled
            number = next_number(session, session.get(RegistrationCounter, PREFIX))
            return {'enabled': True, 'username_preview': f'{PREFIX}{number:03d}', 'grades': GRADES}

    @app.post('/v1/auth/register', status_code=201)
    def register_student(payload: RegisterStudent, request: Request, idempotency_key: str = KEY):
        if not registration_configured(cfg):
            raise Problem(403, 'authorization', 'access_denied', '临时注册入口暂未开放，请联系老师。')
        supplied = password(payload.password, validator=validate_student_password)
        if supplied != payload.confirm_password.get_secret_value():
            raise Problem(422, 'validation', 'invalid_request', '两次密码不一致，请再核对一下。')
        secret = mac_key(signing_key(cfg))
        body = {'password': supplied, 'grade': payload.grade,
                'tenant_id': cfg.self_registration_tenant_id, 'campus_id': cfg.self_registration_campus_id}
        key_hash = hashlib.sha256(idempotency_key.encode('ascii')).hexdigest()
        now = datetime.now(timezone.utc)
        error = None
        result = None
        try:
            with Session(engine) as session, session.begin():
                counter = lock_counter(session)
                campus = registration_campus(session, cfg)
                if campus is None:
                    raise Problem(403, 'authorization', 'access_denied', '注册校区暂不可用，请联系老师。')
                previous = session.get(StudentRegistration, key_hash)
                if previous:
                    # A leaked request key must not become an unlimited oracle
                    # for the short initial password. Persist failures as well.
                    if not limit(session, secret, 'registration-recovery:' + key_hash, now, 20, 60):
                        error = Problem(429, 'authorization', 'login_rate_limited', '注册结果查询过于频繁，请稍后使用原内容重试。')
                    elif not hmac.compare_digest(previous.request_hash, fingerprint(secret, previous.request_salt, body)):
                        error = Problem(409, 'conflict', 'idempotency_conflict', '此注册请求已用于其他内容，请使用原内容恢复注册结果。')
                    else:
                        account = session.get(PasswordAccount, previous.username)
                        if account is None or account.student_id is None:
                            error = Problem(409, 'conflict', 'stale_input', '该注册账号已不可用，请联系老师核实。')
                        else:
                            result = {'username': previous.username, 'student_id': account.student_id, 'grade': previous.grade}
                elif not limit(session, secret, 'registration-ip:' + (request.client.host if request.client else 'unknown'), now, 20, 60):
                    # Commit the limiter update; raising inside would roll it back.
                    error = Problem(429, 'authorization', 'login_rate_limited', '注册过于频繁，请稍后再试。')
                else:
                    number = next_number(session, counter)
                    name = f'{PREFIX}{number:03d}'
                    principal_id, student_id = 'actor_' + uuid4().hex, 'student_' + uuid4().hex
                    session.add(Principal(id=principal_id, tenant_id=campus.tenant_id, issuer=cfg.oidc_issuer,
                                          subject='account:' + uuid4().hex, active=True))
                    session.add(Student(id=student_id, tenant_id=campus.tenant_id, campus_id=campus.id,
                                        display_name=name, active=True))
                    session.flush()
                    session.add(PrincipalRole(principal_id=principal_id, role='student'))
                    session.add(StudentGrant(principal_id=principal_id, student_id=student_id, tenant_id=campus.tenant_id, active=True))
                    session.add(PasswordAccount(username=name, principal_id=principal_id, student_id=student_id,
                        tenant_id=campus.tenant_id, password_hash=hash_password(supplied, validator=validate_student_password),
                        password_version=1, must_change_password=False, active=True, failed_attempts=0,
                        created_at=now, updated_at=now))
                    session.add(StudentLearningProfile(student_id=student_id, tenant_id=campus.tenant_id, revision=1,
                        document={'grade': payload.grade, 'term': None, 'textbook_revision': 'publisher_latest',
                                  'current_knowledge_ids': [], 'note': None},
                        updated_by=principal_id, updated_at=now))
                    session.flush()
                    salt = secrets.token_hex(16)
                    session.add(StudentRegistration(key_hash=key_hash, request_salt=salt,
                        request_hash=fingerprint(secret, salt, body), username=name, grade=payload.grade, created_at=now))
                    counter.last_number = number
                    result = {'username': name, 'student_id': student_id, 'grade': payload.grade}
        except IntegrityError:
            raise Problem(409, 'conflict', 'username_conflict', '账号分配暂时冲突，请用相同内容重试。') from None
        if error:
            raise error
        return result
