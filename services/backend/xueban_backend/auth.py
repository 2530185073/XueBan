from dataclasses import dataclass
from pathlib import Path

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError, PyJWKClientConnectionError, PyJWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .errors import Problem
from .models import Principal, PrincipalRole


@dataclass(frozen=True)
class Actor:
    id: str
    tenant_id: str
    roles: tuple[str, ...]
    bound_student_id: str | None = None


class TokenVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jwks = (
            PyJWKClient(settings.oidc_jwks_url, cache_keys=False, lifespan=60, timeout=5)
            if settings.auth_configured and settings.oidc_jwks_url
            else None
        )

    def _claims(self, token: str | None) -> dict:
        cfg = self.settings
        if not cfg.auth_configured:
            raise Problem(503, "service", "service_unavailable", "真实身份验证尚未配置，当前拒绝访问。")
        if not token or len(token) > 16384:
            raise Problem(401, "authorization", "authentication_required", "需要有效的 Bearer 访问凭证。")
        try:
            # The allowlist comes from server code, never the untrusted JWT header.
            if jwt.get_unverified_header(token).get("alg") != "RS256":
                raise InvalidTokenError("Unsupported algorithm")
            key = self.jwks.get_signing_key_from_jwt(token).key if self.jwks else Path(cfg.jwt_public_key_file).read_bytes()
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=cfg.oidc_issuer,
                audience=cfg.oidc_audience,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                leeway=0,
            )
            subject = claims["sub"]
            if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
                raise InvalidTokenError("Invalid subject")
            return claims
        except PyJWKClientConnectionError:
            raise Problem(503, "service", "service_unavailable", "身份密钥服务暂不可用。") from None
        except (InvalidTokenError, PyJWKClientError):
            raise Problem(401, "authorization", "authentication_required", "访问凭证无效或已过期。") from None
        except (OSError, ValueError, PyJWTError):
            raise Problem(503, "service", "service_unavailable", "身份验证配置不可用。") from None

    def subject(self, token: str | None) -> str:
        return self._claims(token)["sub"]

    def actor(self, token: str | None, session: Session, *, allow_password_change: bool = False) -> Actor:
        claims = self._claims(token)
        subject = claims["sub"]
        principal = session.scalar(select(Principal).where(Principal.issuer == self.settings.oidc_issuer, Principal.subject == subject, Principal.active.is_(True)))
        if principal is None:
            raise Problem(403, "authorization", "access_denied", "该身份尚未获得项目授权或授权已撤回。")
        from .account_models import PasswordAccount
        account = session.scalar(select(PasswordAccount).where(PasswordAccount.principal_id == principal.id))
        if account is not None:
            if not account.active or "device_session_id" not in claims:
                raise Problem(401, "authorization", "authentication_required", "账号会话不可用，请重新登录。")
            if account.student_id is not None:
                from .models import Student, StudentGrant
                bound = session.scalar(select(Student.id).join(StudentGrant, StudentGrant.student_id == Student.id).where(Student.id == account.student_id, Student.active.is_(True), StudentGrant.principal_id == principal.id, StudentGrant.active.is_(True), Student.tenant_id == principal.tenant_id))
                if bound is None:
                    raise Problem(403, "authorization", "access_denied", "学员授权已停用。")
        if "device_session_id" in claims:
            from datetime import datetime, timezone
            from .device_models import DeviceSession
            session_id = claims["device_session_id"]
            device = session.get(DeviceSession, session_id) if isinstance(session_id, str) and len(session_id) <= 64 else None
            expires = device.expires_at if device is not None else None
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if device is None or device.principal_id != principal.id or device.revoked or expires <= datetime.now(timezone.utc):
                raise Problem(401, "authorization", "authentication_required", "会话已过期或撤回，请重新登录。")
        if account is not None and account.must_change_password and not allow_password_change:
            raise Problem(403, "authorization", "password_change_required", "请先修改初始密码再进入作业。")
        roles = tuple(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal.id).order_by(PrincipalRole.role)))
        if not set(roles).intersection({"student", "sitting_teacher", "campus_admin", "data_steward"}):
            raise Problem(403, "authorization", "access_denied", "该身份没有此接口权限。")
        return Actor(principal.id, principal.tenant_id, roles, account.student_id if account else None)
