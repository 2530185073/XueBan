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


class TokenVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jwks = (
            PyJWKClient(settings.oidc_jwks_url, cache_keys=False, lifespan=60, timeout=5)
            if settings.auth_configured and settings.oidc_jwks_url
            else None
        )

    def subject(self, token: str | None) -> str:
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
            return subject
        except PyJWKClientConnectionError:
            raise Problem(503, "service", "service_unavailable", "身份密钥服务暂不可用。") from None
        except (InvalidTokenError, PyJWKClientError):
            raise Problem(401, "authorization", "authentication_required", "访问凭证无效或已过期。") from None
        except (OSError, ValueError, PyJWTError):
            raise Problem(503, "service", "service_unavailable", "身份验证配置不可用。") from None

    def actor(self, token: str | None, session: Session) -> Actor:
        subject = self.subject(token)
        principal = session.scalar(select(Principal).where(Principal.issuer == self.settings.oidc_issuer, Principal.subject == subject, Principal.active.is_(True)))
        if principal is None:
            raise Problem(403, "authorization", "access_denied", "该身份尚未获得项目授权或授权已撤回。")
        roles = tuple(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal.id).order_by(PrincipalRole.role)))
        if not set(roles).intersection({"student", "sitting_teacher", "campus_admin"}):
            raise Problem(403, "authorization", "access_denied", "该身份没有此接口权限。")
        return Actor(principal.id, principal.tenant_id, roles)
