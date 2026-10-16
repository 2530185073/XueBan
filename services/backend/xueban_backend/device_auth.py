"""First-party pairing, enabled only with an explicitly configured signing key."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import secrets
import stat
from threading import Lock
import time
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import Header, Request
import jwt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .device_models import DevicePairing, DeviceSession
from .errors import Problem
from .models import Principal, PrincipalRole, StudentGrant


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def normalize_code(value: str) -> str:
    code = value.strip().upper().replace("-", "").replace(" ", "")
    if not re.fullmatch(r"[A-Z2-7]{16}", code):
        raise Problem(401, "authorization", "authentication_required", "配对码无效、已使用或已过期。")
    return code


def issue_pairing(session: Session, cfg: Settings, principal_id: str, *, lifetime_seconds: int = 900) -> str:
    """Only called by the administrator CLI, never exposed as an unauthenticated API."""
    if not 60 <= lifetime_seconds <= 3600:
        raise ValueError("Pairing lifetime must be between 60 and 3600 seconds")
    principal = session.get(Principal, principal_id)
    if principal is None or not principal.active or principal.issuer != cfg.oidc_issuer:
        raise ValueError("An active principal under the configured issuer is required")
    from .account_models import PasswordAccount
    if session.scalar(select(PasswordAccount.username).where(PasswordAccount.principal_id == principal_id)) is not None:
        raise ValueError("Password accounts use login; pairing is disabled")
    if session.scalar(select(StudentGrant.student_id).where(StudentGrant.principal_id == principal_id, StudentGrant.active.is_(True)).limit(1)) is None:
        raise ValueError("The principal needs an explicit active student grant")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    code = "".join(secrets.choice(alphabet) for _ in range(16))
    now = datetime.now(timezone.utc)
    session.add(DevicePairing(code_hash=digest(code), principal_id=principal_id, created_at=now,
                              expires_at=now + timedelta(seconds=lifetime_seconds)))
    session.flush()
    return "-".join(code[i:i+4] for i in range(0, 16, 4))


def signing_key(cfg: Settings):
    if not cfg.auth_configured or not cfg.signing_private_key_file or cfg.oidc_jwks_url:
        raise Problem(503, "service", "service_unavailable", "账号签名服务尚未配置。")
    if not 300 <= cfg.device_session_ttl_seconds <= 86400:
        raise Problem(503, "service", "service_unavailable", "账号签名服务配置无效。")
    try:
        path = Path(cfg.signing_private_key_file)
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("Private key permissions")
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("RSA key required")
        public = serialization.load_pem_public_key(Path(cfg.jwt_public_key_file).read_bytes())
        if key.public_key().public_numbers() != public.public_numbers():
            raise ValueError("Signing and verification keys differ")
        return key
    except (OSError, ValueError, TypeError, AttributeError):
        raise Problem(503, "service", "service_unavailable", "账号签名服务配置不可用。") from None


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairing_code: str = Field(min_length=16, max_length=40)


class PairingLimiter:
    """Bounded per-process throttle; use an edge rate limiter for public deployments."""
    def __init__(self):
        self.entries: OrderedDict[str, list[float]] = OrderedDict()
        self.lock = Lock()

    def check(self, address: str):
        now = time.monotonic()
        with self.lock:
            attempts = [t for t in self.entries.pop(address, []) if now - t < 60]
            self.entries[address] = attempts
            while len(self.entries) > 2048:
                self.entries.popitem(last=False)
            if len(attempts) >= 10:
                raise Problem(429, "service", "rate_limited", "配对尝试过于频繁，请稍后重试。")
            attempts.append(now)


def install_device_auth(app, engine, cfg: Settings):
    limiter = PairingLimiter()

    @app.post("/v1/device-sessions", operation_id="pairDevice")
    def pair_device(payload: PairingRequest, request: Request,
                    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")):
        limiter.check(request.client.host if request.client else "unknown")
        key = signing_key(cfg)
        code_hash = digest(normalize_code(payload.pairing_code))
        key_hash = digest(idempotency_key)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        denied = Problem(401, "authorization", "authentication_required", "配对码无效、已使用或已过期。")
        with Session(engine) as session, session.begin():
            pairing = session.scalar(select(DevicePairing).where(DevicePairing.code_hash == code_hash).with_for_update())
            if pairing is None:
                raise denied
            principal = session.scalar(select(Principal).where(Principal.id == pairing.principal_id).with_for_update())
            if principal is None or not principal.active or principal.issuer != cfg.oidc_issuer:
                raise denied
            from .account_models import PasswordAccount
            if session.scalar(select(PasswordAccount.username).where(PasswordAccount.principal_id == principal.id)) is not None:
                raise denied
            roles = set(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal.id)))
            if not roles.intersection({"student", "sitting_teacher", "campus_admin"}):
                raise denied
            if session.scalar(select(StudentGrant.student_id).where(StudentGrant.principal_id == principal.id, StudentGrant.active.is_(True)).limit(1)) is None:
                raise denied
            if pairing.redemption_key_hash:
                if not secrets.compare_digest(pairing.redemption_key_hash, key_hash):
                    raise denied
                device = session.get(DeviceSession, pairing.session_id)
                if device is None or device.revoked or utc(device.expires_at) <= now:
                    raise denied
            else:
                if utc(pairing.expires_at) <= now:
                    raise denied
                device = DeviceSession(id="device_" + uuid4().hex, principal_id=principal.id, issued_at=now,
                                       expires_at=now + timedelta(seconds=cfg.device_session_ttl_seconds), revoked=False)
                # CAS also prevents double redemption on database implementations without row locks.
                changed = session.execute(update(DevicePairing).where(DevicePairing.code_hash == code_hash,
                    DevicePairing.redemption_key_hash.is_(None)).values(redemption_key_hash=key_hash, session_id=device.id))
                if changed.rowcount != 1:
                    raise denied
                session.add(device)
            token = jwt.encode({"iss": cfg.oidc_issuer, "aud": cfg.oidc_audience, "sub": principal.subject,
                                "iat": utc(device.issued_at), "exp": utc(device.expires_at),
                                "device_session_id": device.id}, key, algorithm="RS256")
            result = {"access_token": token, "expires_at": utc(device.expires_at).isoformat().replace("+00:00", "Z")}
        return result
