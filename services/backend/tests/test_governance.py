"""Student data deletion: steward authorization, blocking, purge, ledger replay."""
from dataclasses import replace
from datetime import timedelta
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from xueban_backend.account_models import PrincipalCampusGrant
from xueban_backend.correction_models import Asset, CorrectionEvent, Entity, EntitySnapshot, Task
from xueban_backend.governance import DeletionExecutor
from xueban_backend.governance_models import DeletionRequest
from xueban_backend.models import Homework, IdempotencyRecord, Principal, PrincipalRole, Student, StudentGrant

from test_correction_flow import flow  # noqa: F401  (pytest fixture reuse)

BODY = {"reason": "监护人书面申请删除（合成）", "confirmation": "delete_authorized_student_data"}


def add_steward(flow, subject="steward-a"):
    with Session(flow.database.engine) as session:
        session.add(Principal(id="steward_a", tenant_id="tenant_a", issuer="https://identity.example.invalid/real-provider", subject=subject, active=True))
        session.flush()
        session.add_all([PrincipalRole(principal_id="steward_a", role="data_steward"), PrincipalRole(principal_id="steward_a", role="campus_admin"), PrincipalRole(principal_id="steward_a", role="sitting_teacher")])
        session.add(PrincipalCampusGrant(principal_id="steward_a", campus_id="campus_a", tenant_id="tenant_a", active=True))
        session.add(StudentGrant(principal_id="steward_a", student_id="student_a", tenant_id="tenant_a", active=True))
        session.commit()
    return {"Authorization": "Bearer " + flow.signing.token(subject)}


def configured(flow, tmp_path, **changes):
    cfg = replace(flow.cfg, deletion_ledger_path=tmp_path / "governance" / "deletions.jsonl", **changes)
    from xueban_backend.app import create_app
    from fastapi.testclient import TestClient
    return cfg, TestClient(create_app(cfg))


def counts(flow):
    with Session(flow.database.engine) as session:
        return {model.__name__: session.scalar(select(func.count()).select_from(model)) for model in (Homework, Asset, Entity, EntitySnapshot, CorrectionEvent, Task)}


def test_deletion_requires_steward_blocks_reads_and_purges(flow, tmp_path):
    case = flow.ready()
    asset_id = flow.homework()["asset_ids"][0]
    cfg, client = configured(flow, tmp_path)
    student_headers = dict(flow.client.headers)

    denied = client.post("/v1/students/student_a/deletion-requests", json=BODY, headers={**student_headers, "Idempotency-Key": "delete-by-student-1"})
    assert denied.status_code == 403

    headers = add_steward(flow)
    response = client.post("/v1/students/student_a/deletion-requests", json=BODY, headers={**headers, "Idempotency-Key": "delete-student-a-1"})
    assert response.status_code == 202, response.text
    request = response.json()
    assert request["status"] == "accepted"
    assert request["targets"]["original_assets"] == "pending"
    replay = client.post("/v1/students/student_a/deletion-requests", json=BODY, headers={**headers, "Idempotency-Key": "delete-student-a-1"})
    assert replay.json()["id"] == request["id"]
    duplicate = client.post("/v1/students/student_a/deletion-requests", json=BODY, headers={**headers, "Idempotency-Key": "delete-student-a-2"})
    assert duplicate.status_code == 409

    # Accepted deletion blocks online reads and processing immediately.
    assert flow.client.get(f"/v1/cases/{case['id']}").status_code in {403, 404}
    assert client.get(f"/v1/homeworks/{flow.homework_id}", headers=headers).status_code == 404

    executor = DeletionExecutor(flow.database.engine, cfg)
    assert executor.run_once()
    status = client.get(f"/v1/deletion-requests/{request['id']}", headers=headers).json()
    assert status["status"] == "waiting_devices"
    assert status["targets"] == {"original_assets": "done", "derived_assets": "not_applicable", "sensitive_records": "done", "device_caches": "pending", "backup_tombstone": "done"}
    assert status["completed_at"] is None
    assert not (cfg.asset_storage_dir / asset_id).exists()
    assert counts(flow) == {"Homework": 0, "Asset": 0, "Entity": 0, "EntitySnapshot": 0, "CorrectionEvent": 0, "Task": 0}
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.student_id == "student_a")) == 0
        student = session.get(Student, "student_a")
        assert student.display_name == "已删除学员" and not student.active
    ledger = [json.loads(line) for line in cfg.deletion_ledger_path.read_text().splitlines()]
    assert ledger[0]["deletion_id"] == request["id"] and ledger[0]["asset_ids"] == [asset_id]
    assert "监护人" not in cfg.deletion_ledger_path.read_text()

    # Device caches are only declared clean after the retention window.
    assert not executor.run_once()
    with Session(flow.database.engine) as session:
        row = session.get(DeletionRequest, request["id"])
        row.server_completed_at = row.server_completed_at - timedelta(days=8)
        session.commit()
    executor.run_once()
    status = client.get(f"/v1/deletion-requests/{request['id']}", headers=headers).json()
    assert status["status"] == "completed" and status["completed_at"] is not None
    client.close()


def test_restored_backup_is_purged_again_from_ledger(flow, tmp_path):
    flow.ready()
    asset_id = flow.homework()["asset_ids"][0]
    cfg, client = configured(flow, tmp_path)
    headers = add_steward(flow)
    assert client.post("/v1/students/student_a/deletion-requests", json=BODY, headers={**headers, "Idempotency-Key": "delete-student-a-3"}).status_code == 202
    content = (cfg.asset_storage_dir / asset_id).read_bytes()
    executor = DeletionExecutor(flow.database.engine, cfg)
    executor.run_once()

    # Simulate a backup restored from before the deletion: rows and file return.
    with Session(flow.database.engine) as session:
        session.get(Student, "student_a").active = True
        session.add(Homework(id="homework_restored", tenant_id="tenant_a", campus_id="campus_a", student_id="student_a", revision=1, curriculum={}, title=None, collection_scope="partial", created_at=flow_now(), occurred_at=flow_now(), processing_blocked=False))
        session.commit()
    (cfg.asset_storage_dir / asset_id).write_bytes(content)
    assert executor.reapply_ledger() == 1
    assert not (cfg.asset_storage_dir / asset_id).exists()
    with Session(flow.database.engine) as session:
        assert session.get(Homework, "homework_restored") is None
        assert not session.get(Student, "student_a").active
    client.close()


def test_device_retention_wait_does_not_block_later_deletions(flow, tmp_path):
    flow.ready()
    cfg, client = configured(flow, tmp_path)
    headers = add_steward(flow)
    first = client.post("/v1/students/student_a/deletion-requests", json=BODY, headers={**headers, "Idempotency-Key": "delete-student-a-5"}).json()
    executor = DeletionExecutor(flow.database.engine, cfg)
    assert executor.run_once()
    with Session(flow.database.engine) as session:
        session.add(Student(id="student_later", tenant_id="tenant_a", campus_id="campus_a", display_name="合成学员乙", active=True))
        session.flush()
        session.add(StudentGrant(principal_id="steward_a", student_id="student_later", tenant_id="tenant_a", active=True))
        session.commit()
    later = client.post("/v1/students/student_later/deletion-requests", json=BODY, headers={**headers, "Idempotency-Key": "delete-student-later-1"})
    assert later.status_code == 202, later.text

    # The older request is still inside its device-cache window; the newer one must not wait behind it.
    assert executor.run_once()
    statuses = {r["id"]: client.get(f"/v1/deletion-requests/{r['id']}", headers=headers).json()["status"] for r in (first, later.json())}
    assert statuses == {first["id"]: "waiting_devices", later.json()["id"]: "waiting_devices"}
    assert not executor.run_once()
    client.close()


def test_deletion_refused_without_ledger(flow):
    headers = add_steward(flow)
    response = flow.client.post("/v1/students/student_a/deletion-requests", json=BODY, headers={**headers, "Idempotency-Key": "delete-student-a-4"})
    assert response.status_code == 503


def flow_now():
    from xueban_backend.correction_core import now
    return now()
