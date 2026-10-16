"""Liveness/readiness probes for the reverse proxy, orchestrator and runbooks.

Readiness checks the dependencies a request actually needs: database reachable
at the expected migration head and the private asset directory writable. Bodies
contain fixed status words only, never configuration values.
"""
from pathlib import Path
import os
import tempfile

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.responses import JSONResponse
from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[1]


def expected_head() -> str:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def install_ops(app, engine, cfg):
    head = expected_head()

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        checks = {}
        try:
            with engine.connect() as connection:
                current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            checks["database"] = "ok" if current == head else "migration_pending"
        except Exception:
            checks["database"] = "unavailable"
        if cfg.asset_storage_dir is None:
            checks["asset_storage"] = "unconfigured"
        else:
            try:
                root = Path(cfg.asset_storage_dir)
                root.mkdir(mode=0o700, parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=root, prefix=".probe-") as probe:
                    probe.write(b"ok")
                    probe.flush()
                    os.fsync(probe.fileno())
                checks["asset_storage"] = "ok"
            except OSError:
                checks["asset_storage"] = "unavailable"
        checks["deletion_ledger"] = "ok" if cfg.deletion_ledger_path else "unconfigured"
        ready = checks["database"] == "ok" and checks["asset_storage"] == "ok"
        return JSONResponse({"status": "ready" if ready else "not_ready", "checks": checks}, status_code=200 if ready else 503)
