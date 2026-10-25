"""Long-lived accounts; passwords and bearer credentials never enter audit JSON."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from uuid import uuid4
from cryptography.hazmat.primitives import serialization
from fastapi import Header, Request
import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .account_models import AuthOperation, LoginLimit, PasswordAccount, PrincipalCampusGrant
from .auth import TokenVerifier
from .device_auth import signing_key, utc
from .device_models import DeviceSession
from .errors import Problem
from .models import Campus, Principal, PrincipalRole, Student, StudentGrant
from .passwords import DUMMY_HASH, hash_password, validate_password, validate_student_password, verify_password

KEY = Header(alias='Idempotency-Key', min_length=8, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')
ID_PATTERN = r'^[a-z][a-z0-9_-]{2,63}$'


def username(value):
    normalized = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,63}', normalized):
        raise Problem(422, 'validation', 'invalid_request', '用户名须为3至64位英文字母、数字、点、下划线或连字符。')
    return normalized


def password(value: SecretStr):
    value = value.get_secret_value()
    try:
        validate_password(value)
    except ValueError:
        raise Problem(422, 'validation', 'invalid_request', '密码须为15至128个字符，UTF-8编码不超过512字节。') from None
    return value


def login_password(value: SecretStr | None):
    if value is None:
        return None
    raw = value.get_secret_value()
    if not isinstance(raw, str) or not 3 <= len(raw) <= 128:
        raise Problem(422, 'validation', 'invalid_request', '密码须为3至128个字符。')
    try:
        if len(raw.encode('utf-8')) > 512:
            raise ValueError
    except UnicodeError:
        raise Problem(422, 'validation', 'invalid_request', '密码编码无效。') from None
    return raw


def new_student_password(value: SecretStr):
    raw = value.get_secret_value()
    try:
        validate_student_password(raw)
    except ValueError:
        raise Problem(422, 'validation', 'invalid_request', '新密码须为3至15位英文字母或数字。') from None
    return raw


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Strict):
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr


class ChangePassword(Strict):
    current_password: SecretStr | None = None
    new_password: SecretStr


class CreateStudentAccount(Strict):
    username: str = Field(min_length=3, max_length=64)
    student_name: str = Field(min_length=1, max_length=80)
    campus_id: str = Field(pattern=ID_PATTERN)
    initial_password: SecretStr


class ResetPassword(Strict):
    new_password: SecretStr


def denied():
    return Problem(401, 'authorization', 'invalid_credentials', '用户名或密码不正确，或账号不可用。')


def session_ended():
    return Problem(401, 'authorization', 'authentication_required', '登录已失效，请重新登录。')


def throttled():
    return Problem(429, 'authorization', 'login_rate_limited', '登录尝试过于频繁，请稍后再试。')


def mac_key(key):
    material = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return hmac.new(material, b'xueban-auth-request-fingerprint-v1', hashlib.sha256).digest()


def fingerprint(secret, salt, payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hmac.new(secret, bytes.fromhex(salt) + raw, hashlib.sha256).hexdigest()


def operation(session, principal_id, name, key, body, secret):
    key_hash = hashlib.sha256(key.encode('ascii')).hexdigest()
    row = session.scalar(select(AuthOperation).where(AuthOperation.principal_id == principal_id, AuthOperation.operation == name, AuthOperation.key_hash == key_hash))
    if row is not None and not hmac.compare_digest(row.request_hash, fingerprint(secret, row.request_salt, body)):
        raise Problem(409, 'conflict', 'idempotency_conflict', '此请求键已用于不同内容，请先恢复原操作。')
    return row


def record_operation(session, principal_id, name, key, body, secret, **values):
    salt = secrets.token_hex(16)
    row = AuthOperation(id='authop_' + uuid4().hex, principal_id=principal_id, operation=name,
                        key_hash=hashlib.sha256(key.encode('ascii')).hexdigest(), request_salt=salt,
                        request_hash=fingerprint(secret, salt, body), created_at=datetime.now(timezone.utc), **values)
    session.add(row)
    return row


def mint(session, cfg, principal, now):
    device = DeviceSession(id='device_' + uuid4().hex, principal_id=principal.id, issued_at=now,
                           expires_at=now + timedelta(seconds=cfg.device_session_ttl_seconds), revoked=False)
    session.add(device); session.flush()
    return device


def session_result(key, cfg, principal, account, device):
    if device is None or device.revoked or utc(device.expires_at) <= datetime.now(timezone.utc):
        raise denied()
    token = jwt.encode({'iss': cfg.oidc_issuer, 'aud': cfg.oidc_audience, 'sub': principal.subject,
                        'iat': utc(device.issued_at), 'exp': utc(device.expires_at), 'device_session_id': device.id}, key, algorithm='RS256')
    return {'access_token': token, 'expires_at': utc(device.expires_at).isoformat().replace('+00:00', 'Z'), 'must_change_password': account.must_change_password}


def revoke_sessions(session, principal_id):
    session.execute(update(DeviceSession).where(DeviceSession.principal_id == principal_id).values(revoked=True))
    from .correction_models import Asset
    session.execute(update(Asset).where(Asset.upload_actor_id == principal_id).values(upload_token_hash=None))
    session.execute(update(Asset).where(Asset.read_actor_id == principal_id).values(read_token_hash=None))


def limit(session, secret, label, now, maximum, seconds):
    key = hmac.new(secret, label.encode('utf-8'), hashlib.sha256).hexdigest()
    dialect = session.get_bind().dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    session.execute(insert(LoginLimit).values(key=key, window_started=now, count=0).on_conflict_do_nothing(index_elements=['key']))
    row = session.scalar(select(LoginLimit).where(LoginLimit.key == key).with_for_update())
    if row.locked_until and utc(row.locked_until) > now:
        return False
    if now - utc(row.window_started) >= timedelta(seconds=seconds):
        row.window_started = now; row.count = 0; row.locked_until = None
    row.count += 1
    if row.count > maximum:
        row.locked_until = now + timedelta(seconds=seconds)
        return False
    return True


def account_failure(account, now):
    account.failed_attempts += 1
    if account.failed_attempts >= 5:
        account.locked_until = now + timedelta(minutes=15)


def usable(session, cfg, principal, account):
    if principal is None or not principal.active or principal.issuer != cfg.oidc_issuer or not account.active:
        return False
    roles = set(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal.id)))
    if not roles.intersection({'student', 'sitting_teacher', 'campus_admin'}):
        return False
    if account.student_id is not None:
        student = session.get(Student, account.student_id)
        grant = session.get(StudentGrant, (principal.id, account.student_id))
        return bool(student and student.active and student.tenant_id == principal.tenant_id and grant and grant.active)
    return bool(roles.intersection({'campus_admin', 'sitting_teacher'}))


def set_password_account(session, cfg, *, principal_id, username_value, password_value, student_id=None, must_change=True):
    name = username(username_value)
    principal = session.scalar(select(Principal).where(Principal.id == principal_id).with_for_update())
    if principal is None or not principal.active or principal.issuer != cfg.oidc_issuer:
        raise ValueError('An explicitly enrolled active principal is required')
    roles = set(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal_id)))
    if student_id is not None:
        grants = list(session.scalars(select(StudentGrant.student_id).where(StudentGrant.principal_id == principal_id, StudentGrant.active.is_(True))))
        student = session.get(Student, student_id)
        if roles != {'student'} or grants != [student_id] or student is None or not student.active or student.tenant_id != principal.tenant_id:
            raise ValueError('Student account requires one existing matching student grant and only the student role')
    elif not roles.intersection({'campus_admin', 'sitting_teacher'}) or 'student' in roles:
        raise ValueError('A staff account requires an explicitly enrolled staff role')
    existing = session.scalar(select(PasswordAccount).where(PasswordAccount.principal_id == principal_id))
    occupied = session.get(PasswordAccount, name)
    if occupied is not None and occupied.principal_id != principal_id:
        raise ValueError('Username already exists')
    if existing is not None and (existing.username != name or existing.student_id != student_id):
        raise ValueError('Existing account binding cannot be renamed or reassigned')
    now = datetime.now(timezone.utc)
    if existing is None:
        existing = PasswordAccount(username=name, principal_id=principal_id, student_id=student_id, tenant_id=principal.tenant_id,
                                   password_hash=hash_password(password_value), password_version=1, must_change_password=must_change,
                                   active=True, failed_attempts=0, created_at=now, updated_at=now)
        session.add(existing)
    else:
        existing.password_hash = hash_password(password_value); existing.password_version += 1
        existing.must_change_password = must_change; existing.failed_attempts = 0; existing.locked_until = None; existing.updated_at = now
    revoke_sessions(session, principal_id)
    session.flush()
    return existing


def grant_campus(session, principal_id, campus_id):
    principal = session.get(Principal, principal_id); campus = session.get(Campus, campus_id)
    roles = set(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal_id)))
    if principal is None or not principal.active or campus is None or campus.tenant_id != principal.tenant_id or 'campus_admin' not in roles:
        raise ValueError('An existing campus administrator and same-tenant campus are required')
    grant = session.get(PrincipalCampusGrant, (principal_id, campus_id))
    if grant is None:
        session.add(PrincipalCampusGrant(principal_id=principal_id, campus_id=campus_id, tenant_id=principal.tenant_id, active=True))
    elif not grant.active:
        raise ValueError('Existing campus grant is revoked; explicit restoration is required')


def install_account_auth(app, engine, cfg):
    verifier = TokenVerifier(cfg)

    def bearer(authorization):
        if not authorization or not authorization.startswith('Bearer '):
            raise denied()
        return authorization[7:]

    def context(session, authorization, *, recovery=False):
        token = bearer(authorization); claims = verifier._claims(token)
        principal = session.scalar(select(Principal).where(Principal.issuer == cfg.oidc_issuer, Principal.subject == claims['sub'], Principal.active.is_(True)))
        if principal is None:
            raise session_ended()
        account = session.scalar(select(PasswordAccount).where(PasswordAccount.principal_id == principal.id).with_for_update())
        device_id = claims.get('device_session_id')
        device = session.get(DeviceSession, device_id) if isinstance(device_id, str) else None
        if account is None or not usable(session, cfg, principal, account) or device is None or device.principal_id != principal.id or utc(device.expires_at) <= datetime.now(timezone.utc) or (device.revoked and not recovery):
            raise session_ended()
        return principal, account, device

    def campus_scope(session, authorization):
        actor = verifier.actor(bearer(authorization), session)
        if 'campus_admin' not in actor.roles:
            raise Problem(403, 'authorization', 'access_denied', '仅获授权的校区管理员可管理学员账号。')
        # Serialize administrator writes/idempotent retries across processes.
        session.scalar(select(Principal).where(Principal.id == actor.id).with_for_update())
        campuses = list(session.scalars(select(PrincipalCampusGrant.campus_id).where(PrincipalCampusGrant.principal_id == actor.id, PrincipalCampusGrant.tenant_id == actor.tenant_id, PrincipalCampusGrant.active.is_(True)).order_by(PrincipalCampusGrant.campus_id)))
        return actor, campuses

    def item(account, student):
        return {'student_id': student.id, 'student_name': student.display_name, 'username': account.username,
                'active': account.active and student.active, 'must_change_password': account.must_change_password,
                'campus_id': student.campus_id, 'principal_id': account.principal_id}

    @app.post('/v1/auth/login')
    def login(payload: Login, request: Request, idempotency_key: str = KEY):
        key = signing_key(cfg); secret = mac_key(key); name = username(payload.username); supplied = login_password(payload.password)
        body = {'username': name, 'password': supplied}; now = datetime.now(timezone.utc).replace(microsecond=0)
        error = None; result = None
        with Session(engine) as session, session.begin():
            if not limit(session, secret, 'ip:' + (request.client.host if request.client else 'unknown'), now, 50, 60):
                error = throttled()
            else:
                account = session.scalar(select(PasswordAccount).where(PasswordAccount.username == name).with_for_update())
                if account is None:
                    permitted = limit(session, secret, 'unknown:' + name, now, 5, 900)
                    if permitted: verify_password(supplied, DUMMY_HASH)
                    error = denied() if permitted else throttled()
                elif account.locked_until and utc(account.locked_until) > now:
                    error = throttled()
                else:
                    if account.locked_until: account.failed_attempts = 0; account.locked_until = None
                    principal = session.get(Principal, account.principal_id)
                    if not verify_password(supplied, account.password_hash) or not usable(session, cfg, principal, account):
                        account_failure(account, now); error = denied()
                    else:
                        account.failed_attempts = 0; account.locked_until = None
                        old = operation(session, principal.id, 'login', idempotency_key, body, secret)
                        if old:
                            if old.credential_version != account.password_version: raise denied()
                            device = session.get(DeviceSession, old.session_id)
                        else:
                            device = mint(session, cfg, principal, now)
                            record_operation(session, principal.id, 'login', idempotency_key, body, secret, session_id=device.id, credential_version=account.password_version)
                        result = session_result(key, cfg, principal, account, device)
        if error: raise error
        return result

    @app.get('/v1/auth/account')
    def get_account(authorization: str | None = Header(default=None)):
        with Session(engine) as session:
            _, account, _ = context(session, authorization)
            return {'username': account.username, 'must_change_password': account.must_change_password, 'student_id': account.student_id}

    @app.post('/v1/auth/change-password')
    def change_password(payload: ChangePassword, authorization: str | None = Header(default=None), idempotency_key: str = KEY):
        key = signing_key(cfg); secret = mac_key(key); current = login_password(payload.current_password); new = new_student_password(payload.new_password)
        body = {'current_password': current, 'new_password': new}; now = datetime.now(timezone.utc).replace(microsecond=0)
        error = None; result = None
        with Session(engine) as session, session.begin():
            principal, account, previous = context(session, authorization, recovery=True)
            old = operation(session, principal.id, 'change-password', idempotency_key, body, secret)
            if old:
                if old.previous_session_id != previous.id or old.credential_version != account.password_version: raise denied()
                result = session_result(key, cfg, principal, account, session.get(DeviceSession, old.session_id))
            elif previous.revoked:
                raise session_ended()
            elif account.locked_until and utc(account.locked_until) > now:
                error = throttled()
            elif not account.must_change_password and current is None:
                raise Problem(422, 'validation', 'invalid_request', '普通改密需要填写当前密码。')
            elif current is not None and not verify_password(current, account.password_hash):
                if account.locked_until: account.failed_attempts = 0; account.locked_until = None
                account_failure(account, now); error = denied()
            else:
                if current is not None and hmac.compare_digest(current.encode('utf-8'), new.encode('utf-8')):
                    raise Problem(422, 'validation', 'invalid_request', '新密码必须与当前密码不同。')
                account.password_hash = hash_password(new, validator=validate_student_password); account.password_version += 1; account.must_change_password = False
                account.failed_attempts = 0; account.locked_until = None; account.updated_at = now
                revoke_sessions(session, principal.id); device = mint(session, cfg, principal, now)
                record_operation(session, principal.id, 'change-password', idempotency_key, body, secret, session_id=device.id, previous_session_id=previous.id, credential_version=account.password_version)
                result = session_result(key, cfg, principal, account, device)
        if error: raise error
        return result

    @app.post('/v1/auth/logout')
    def logout(payload: Strict, authorization: str | None = Header(default=None), idempotency_key: str = KEY):
        with Session(engine) as session, session.begin():
            _, _, device = context(session, authorization, recovery=True); device.revoked = True
        return {'logged_out': True}

    @app.get('/v1/admin/student-accounts')
    def list_accounts(authorization: str | None = Header(default=None)):
        with Session(engine) as session:
            actor, campuses = campus_scope(session, authorization)
            rows = session.execute(select(PasswordAccount, Student).join(Student, Student.id == PasswordAccount.student_id).where(Student.tenant_id == actor.tenant_id, Student.campus_id.in_(campuses)).order_by(PasswordAccount.username)).all()
            return {'items': [item(account, student) for account, student in rows], 'campuses': [{'campus_id': c} for c in campuses]}

    @app.post('/v1/admin/student-accounts', status_code=201)
    def create_student(payload: CreateStudentAccount, authorization: str | None = Header(default=None), idempotency_key: str = KEY):
        secret = mac_key(signing_key(cfg)); name = username(payload.username); initial = password(payload.initial_password)
        body = {'username': name, 'student_name': payload.student_name, 'campus_id': payload.campus_id, 'initial_password': initial}
        if not payload.student_name.strip(): raise Problem(422, 'validation', 'invalid_request', '学员姓名不能为空。')
        try:
            with Session(engine) as session, session.begin():
                actor, campuses = campus_scope(session, authorization)
                if payload.campus_id not in campuses: raise Problem(403, 'authorization', 'access_denied', '该校区不在当前管理员授权范围。')
                old = operation(session, actor.id, 'create-student', idempotency_key, body, secret)
                if old:
                    account = session.get(PasswordAccount, name); student = session.get(Student, old.response_json['student_id'])
                    if account is None or student is None or student.campus_id not in campuses: raise Problem(404, 'not_found', 'not_found', '记录不可访问。')
                    if old.credential_version != account.password_version:
                        raise Problem(409, 'conflict', 'stale_input', '账号密码已变化，请刷新后核实，不能继续交付原初始密码。')
                    return item(account, student)
                if session.get(PasswordAccount, name) is not None: raise Problem(409, 'conflict', 'username_conflict', '该用户名已存在。')
                now = datetime.now(timezone.utc); student_id = 'student_' + uuid4().hex; principal_id = 'actor_' + uuid4().hex
                session.add(Principal(id=principal_id, tenant_id=actor.tenant_id, issuer=cfg.oidc_issuer, subject='account:' + uuid4().hex, active=True))
                session.add(Student(id=student_id, tenant_id=actor.tenant_id, campus_id=payload.campus_id, display_name=payload.student_name.strip(), active=True)); session.flush()
                session.add(PrincipalRole(principal_id=principal_id, role='student')); session.add(StudentGrant(principal_id=principal_id, student_id=student_id, tenant_id=actor.tenant_id, active=True)); session.flush()
                account = PasswordAccount(username=name, principal_id=principal_id, student_id=student_id, tenant_id=actor.tenant_id,
                    password_hash=hash_password(initial), password_version=1, must_change_password=True, active=True, failed_attempts=0, created_at=now, updated_at=now)
                session.add(account); session.flush(); result = item(account, session.get(Student, student_id))
                record_operation(session, actor.id, 'create-student', idempotency_key, body, secret, credential_version=account.password_version, response_json={'student_id': student_id})
                return result
        except IntegrityError:
            raise Problem(409, 'conflict', 'username_conflict', '用户名已存在或账号绑定冲突，请刷新列表核实。') from None

    @app.post('/v1/admin/student-accounts/{student_id}/reset-password')
    def reset_password(student_id: str, payload: ResetPassword, authorization: str | None = Header(default=None), idempotency_key: str = KEY):
        secret = mac_key(signing_key(cfg)); new = password(payload.new_password); body = {'student_id': student_id, 'new_password': new}
        with Session(engine) as session, session.begin():
            actor, campuses = campus_scope(session, authorization)
            student = session.scalar(select(Student).where(Student.id == student_id, Student.tenant_id == actor.tenant_id, Student.campus_id.in_(campuses)))
            account = session.scalar(select(PasswordAccount).where(PasswordAccount.student_id == student_id).with_for_update()) if student else None
            if account is None: raise Problem(404, 'not_found', 'not_found', '学员账号不存在或无权管理。')
            old = operation(session, actor.id, 'reset-password:' + student_id, idempotency_key, body, secret)
            if old:
                if old.credential_version != account.password_version: raise Problem(409, 'conflict', 'stale_input', '此账号密码状态已变化，请刷新后核实。')
                return old.response_json
            account.password_hash = hash_password(new); account.password_version += 1; account.must_change_password = True
            account.failed_attempts = 0; account.locked_until = None; account.updated_at = datetime.now(timezone.utc)
            revoke_sessions(session, account.principal_id)
            result = {'student_id': student_id, 'must_change_password': True}
            record_operation(session, actor.id, 'reset-password:' + student_id, idempotency_key, body, secret, credential_version=account.password_version, response_json=result)
            return result
