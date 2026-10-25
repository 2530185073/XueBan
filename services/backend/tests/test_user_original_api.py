"""Do not queue unsupported image-recovery work after switching providers."""
from copy import deepcopy
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_recognition_recheck import request_body
from xueban_backend.app import create_app
from xueban_backend.config import Settings
from xueban_backend.correction_models import Entity, Task
from xueban_backend.models import IdempotencyRecord


@pytest.mark.parametrize("mode", ["user_original", "two_model", ""])
def test_recovery_capability_is_explicit_and_blocked_before_mutation(flow, mode):
    case = flow.ready()
    path = f"/v1/cases/{case['id']}/recognition-rechecks"
    body = request_body(flow, case)
    with Session(flow.database.engine) as session:
        before = deepcopy(session.get(Entity, case["id"]).annotations)
        tasks = session.scalar(select(func.count()).select_from(Task))
        keys = session.scalar(select(func.count()).select_from(IdempotencyRecord))
    app = create_app(replace(flow.cfg, correction_mode=mode))
    try:
        with TestClient(app) as client:
            client.headers.update(flow.client.headers)
            status = client.get(path + "/status")
            response = client.post(path, json=body, headers={"Idempotency-Key": "unsupported-recovery"})
            if mode != "user_original":
                assert status.status_code == 200
                assert response.status_code == 202, response.text
                return
            for result in (status, response):
                assert result.status_code == 409, result.text
                assert result.json()["code"] == "optional_gate_closed"
                flow.worker.contracts.validate("Problem", result.json())
            # Repeated stale-tab requests must not reserve a key or alter the
            # recovery epoch, verdict, source photo or current case revision.
            assert client.post(path, json=body, headers={"Idempotency-Key": "unsupported-recovery"}).status_code == 409
            assert client.get(f"/v1/cases/{case['id']}").json() == case
        with Session(flow.database.engine) as session:
            assert session.get(Entity, case["id"]).annotations == before
            assert session.scalar(select(func.count()).select_from(Task)) == tasks
            assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == keys
    finally:
        app.state.engine.dispose()


def test_api_settings_reads_mode_without_model_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///synthetic.db")
    monkeypatch.setenv("CORRECTION_MODE", "user_original")
    settings = Settings.from_environment()
    assert settings.correction_mode == "user_original"
    assert not any("key" in name and name.startswith(("ark", "zyai")) for name in vars(settings))
