from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import text

from xueban_backend.app import create_app


def test_health_and_readiness(settings, database, tmp_path):
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["checks"]["asset_storage"] == "unconfigured"
    ready_settings = replace(settings, asset_storage_dir=tmp_path / "assets", deletion_ledger_path=tmp_path / "ledger.jsonl")
    with TestClient(create_app(ready_settings)) as client:
        response = client.get("/readyz")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ready", "checks": {"database": "ok", "asset_storage": "ok", "deletion_ledger": "ok"}}
        with database.engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0001_identity_homeworks'"))
        assert client.get("/readyz").json()["checks"]["database"] == "migration_pending"
