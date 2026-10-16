from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    jwt_public_key_file: str = ""
    allow_sqlite_for_tests: bool = False
    cors_allowed_origins: tuple[str, ...] = ()
    contracts_dir: Path = Path(__file__).resolve().parents[3] / "contracts" / "phase1"

    @classmethod
    def from_environment(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required; no automatic demo database")
        options = {
            "database_url": database_url,
            "oidc_issuer": os.environ.get("OIDC_ISSUER", ""),
            "oidc_audience": os.environ.get("OIDC_AUDIENCE", ""),
            "oidc_jwks_url": os.environ.get("OIDC_JWKS_URL", ""),
            "jwt_public_key_file": os.environ.get("JWT_PUBLIC_KEY_FILE", ""),
            "allow_sqlite_for_tests": os.environ.get("ALLOW_SQLITE_FOR_TESTS") == "1",
            "cors_allowed_origins": tuple(value.strip() for value in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if value.strip()),
        }
        if os.environ.get("CONTRACTS_DIR"):
            options["contracts_dir"] = Path(os.environ["CONTRACTS_DIR"])
        return cls(**options)

    @property
    def auth_configured(self) -> bool:
        issuer = urlsplit(self.oidc_issuer)
        if issuer.scheme != "https" or not issuer.netloc or not self.oidc_audience:
            return False
        if bool(self.oidc_jwks_url) == bool(self.jwt_public_key_file):
            return False
        if self.oidc_jwks_url:
            url = urlsplit(self.oidc_jwks_url)
            return url.scheme == "https" and bool(url.netloc)
        return True
