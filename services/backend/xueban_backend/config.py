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
    signing_private_key_file: str = ""
    device_session_ttl_seconds: int = 28800
    asset_storage_dir: Path | None = None
    public_base_url: str = ""
    upload_ticket_ttl_seconds: int = 900
    worker_lease_seconds: int = 300
    allow_sqlite_for_tests: bool = False
    cors_allowed_origins: tuple[str, ...] = ()
    student_web_dist: Path | None = None
    deletion_ledger_path: Path | None = None
    device_cache_retention_seconds: int = 604800
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
            "signing_private_key_file": os.environ.get("SIGNING_PRIVATE_KEY_FILE", ""),
            "device_session_ttl_seconds": int(os.environ.get("DEVICE_SESSION_TTL_SECONDS", "28800")),
            "asset_storage_dir": Path(os.environ["ASSET_STORAGE_DIR"]) if os.environ.get("ASSET_STORAGE_DIR") else None,
            "public_base_url": os.environ.get("PUBLIC_BASE_URL", ""),
            "upload_ticket_ttl_seconds": int(os.environ.get("UPLOAD_TICKET_TTL_SECONDS", "900")),
            "worker_lease_seconds": int(os.environ.get("WORKER_LEASE_SECONDS", "300")),
            "allow_sqlite_for_tests": os.environ.get("ALLOW_SQLITE_FOR_TESTS") == "1",
            "cors_allowed_origins": tuple(value.strip() for value in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if value.strip()),
            "student_web_dist": Path(os.environ["STUDENT_WEB_DIST"]) if os.environ.get("STUDENT_WEB_DIST") else None,
            "deletion_ledger_path": Path(os.environ["DELETION_LEDGER_PATH"]) if os.environ.get("DELETION_LEDGER_PATH") else None,
            "device_cache_retention_seconds": int(os.environ.get("DEVICE_CACHE_RETENTION_SECONDS", "604800")),
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
