"""Student data deletion: acceptance, execution and post-restore re-application.

Acceptance blocks new processing and online reads in the same transaction. The
executor first appends the request to a ledger kept *outside* the database, so a
restored database backup can be purged again (`--reapply-ledger`). It then deletes
private originals and every student-scoped record. Device caches cannot be
observed by the server: sessions are revoked immediately and clients purge on
authentication failure or after the draft retention period, so the request stays
`waiting_devices` until that retention window has passed.
"""
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from fastapi import Depends, Header
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session
from typing import Annotated

from .account_models import AuthOperation, PasswordAccount, PrincipalCampusGrant
from .asset_store import LocalAssetStore
from .auth import Actor
from .config import Settings
from .contracts import Contracts
from .correction_core import iso, new_id, now
from .correction_models import Asset, CorrectionEvent, Entity, EntitySnapshot, Task
from .db import create_database_engine
from .device_models import DevicePairing, DeviceSession
from .errors import Problem
from .governance_models import DeletionRequest
from .models import Homework, IdempotencyRecord, Principal, PrincipalRole, Student, StudentGrant

TARGETS = ("original_assets", "derived_assets", "sensitive_records", "device_caches", "backup_tombstone")
DELETED_NAME = "已删除学员"


def document(row: DeletionRequest) -> dict:
    return {"id": row.id, "revision": row.revision, "scope": {"tenant_id": row.tenant_id, "campus_id": row.campus_id, "student_id": row.student_id}, "status": row.status, "targets": dict(row.targets), "requested_at": iso(row.requested_at), "completed_at": iso(row.completed_at) if row.completed_at else None}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def steward(session: Session, actor: Actor, student: Student) -> None:
    if "data_steward" not in actor.roles:
        raise Problem(403, "authorization", "forbidden", "此操作需要数据治理负责人权限。")
    granted = session.scalar(select(PrincipalCampusGrant).where(PrincipalCampusGrant.principal_id == actor.id, PrincipalCampusGrant.campus_id == student.campus_id, PrincipalCampusGrant.tenant_id == actor.tenant_id, PrincipalCampusGrant.active.is_(True)))
    if granted is None:
        raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")


def install_governance(app, engine, cfg, database_session, current_actor):
    contracts = Contracts(cfg.contracts_dir)
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]
    KeyDep = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]

    @app.post("/v1/students/{student_id}/deletion-requests", status_code=202, operation_id="requestStudentDeletion")
    def request_deletion(student_id: str, payload: dict, actor: ActorDep, session: SessionDep, idempotency_key: KeyDep):
        contracts.validate("RequestDeletion", payload, kind="requests")
        student = session.scalar(select(Student).where(Student.id == student_id, Student.tenant_id == actor.tenant_id).with_for_update())
        if student is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        steward(session, actor, student)
        if cfg.deletion_ledger_path is None:
            raise Problem(503, "service", "deletion_ledger_unconfigured", "删除清单存储尚未配置，暂不能受理删除。")
        key_hash = _digest(actor.id + "\x00" + idempotency_key)
        existing = session.scalar(select(DeletionRequest).where(DeletionRequest.student_id == student.id, DeletionRequest.status != "failed").order_by(DeletionRequest.requested_at.desc()))
        if existing is not None:
            if existing.idempotency_key_hash == key_hash:
                return contracts_checked(contracts, document(existing))
            raise Problem(409, "conflict", "deletion_already_requested", "该学员已有删除请求，请读取其执行状态。", existing.revision)
        timestamp = now()
        homeworks = list(session.scalars(select(Homework).where(Homework.student_id == student.id, Homework.tenant_id == student.tenant_id).with_for_update()))
        from .practice_photo import practice_photo_asset_ids
        has_assets = (bool(homeworks) and session.scalar(select(Asset.id).where(Asset.homework_id.in_([h.id for h in homeworks])).limit(1)) is not None
                      or bool(practice_photo_asset_ids(session, student.tenant_id, student.id)))
        row = DeletionRequest(id=new_id("deletion"), tenant_id=student.tenant_id, campus_id=student.campus_id, student_id=student.id, revision=1, status="accepted",
            targets={"original_assets": "pending" if has_assets else "not_applicable", "derived_assets": "not_applicable", "sensitive_records": "pending", "device_caches": "pending", "backup_tombstone": "pending"},
            requested_by=actor.id, reason_hash=_digest(payload["reason"]), idempotency_key_hash=key_hash, requested_at=timestamp)
        session.add(row)
        # Block new processing and every online read path in the same transaction.
        for homework in homeworks:
            homework.processing_blocked = True
        student.active = False
        for task in session.scalars(select(Task).where(Task.homework_id.in_([h.id for h in homeworks]), Task.status.in_(["queued", "retryable_failed"]))):
            task.status = "cancelled"
            task.document = {**task.document, "status": "cancelled", "updated_at": iso(timestamp), "retry_after_seconds": None, "failure": None, "result": None}
        for principal_id in _bound_principals(session, student):
            session.execute(update(DeviceSession).where(DeviceSession.principal_id == principal_id).values(revoked=True))
        result = contracts_checked(contracts, document(row))
        session.commit()
        return result

    @app.get("/v1/deletion-requests/{deletion_id}", operation_id="getDeletionRequest")
    def get_deletion(deletion_id: str, actor: ActorDep, session: SessionDep):
        row = session.get(DeletionRequest, deletion_id)
        if row is None or row.tenant_id != actor.tenant_id:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        student = session.get(Student, row.student_id)
        steward(session, actor, student)
        return contracts_checked(contracts, document(row))


def contracts_checked(contracts, value):
    contracts.validate("DeletionRequest", value)
    return value


def _bound_principals(session, student):
    return list(session.scalars(select(PasswordAccount.principal_id).where(PasswordAccount.student_id == student.id)))


def purge_student(session: Session, store: LocalAssetStore, tenant_id: str, student_id: str, extra_asset_ids=()) -> list[str]:
    """Idempotently delete every student-scoped record and private original.

    Returns the asset IDs whose files were addressed. Shared curriculum data and
    staff identities are untouched; the student row remains as a nameless anchor
    for the deletion record.
    """
    homework_ids = list(session.scalars(select(Homework.id).where(Homework.student_id == student_id, Homework.tenant_id == tenant_id)))
    from .practice_photo import practice_photo_asset_ids
    asset_ids = set(extra_asset_ids) | set(practice_photo_asset_ids(session, tenant_id, student_id))
    if homework_ids:
        asset_ids |= set(session.scalars(select(Asset.id).where(Asset.homework_id.in_(homework_ids))))
    for asset_id in sorted(asset_ids):
        store.delete(asset_id)
    if homework_ids:
        entity_ids = select(Entity.id).where(Entity.homework_id.in_(homework_ids))
        session.execute(delete(EntitySnapshot).where(EntitySnapshot.entity_id.in_(entity_ids)))
        session.execute(delete(Entity).where(Entity.homework_id.in_(homework_ids)))
        session.execute(delete(CorrectionEvent).where(CorrectionEvent.homework_id.in_(homework_ids)))
        session.execute(delete(Task).where(Task.homework_id.in_(homework_ids)))
        session.execute(delete(Asset).where(Asset.homework_id.in_(homework_ids)))
    _purge_downstream(session, tenant_id, student_id)
    session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.student_id == student_id))
    if homework_ids:
        session.execute(delete(Homework).where(Homework.id.in_(homework_ids)))
    student = session.get(Student, student_id)
    if student is not None:
        principals = _bound_principals(session, student)
        for principal_id in principals:
            sessions = select(DeviceSession.id).where(DeviceSession.principal_id == principal_id)
            session.execute(delete(AuthOperation).where(or_(AuthOperation.principal_id == principal_id, AuthOperation.session_id.in_(sessions))))
            session.execute(delete(DevicePairing).where(DevicePairing.principal_id == principal_id))
            session.execute(delete(DeviceSession).where(DeviceSession.principal_id == principal_id))
            session.execute(delete(PrincipalRole).where(PrincipalRole.principal_id == principal_id))
            session.execute(update(Principal).where(Principal.id == principal_id).values(active=False))
        session.execute(delete(PasswordAccount).where(PasswordAccount.student_id == student_id))
        session.execute(delete(StudentGrant).where(StudentGrant.student_id == student_id))
        student.display_name = DELETED_NAME
        student.active = False
    return sorted(asset_ids)


DOWNSTREAM_PURGERS = []


def register_downstream_purger(function):
    """Later projections (graph, memory, reports, practice) register here."""
    DOWNSTREAM_PURGERS.append(function)
    return function


def _purge_downstream(session, tenant_id, student_id):
    from . import learning_jobs  # noqa: F401  registers every learning purger
    for function in DOWNSTREAM_PURGERS:
        function(session, tenant_id, student_id)


class DeletionLedger:
    """Append-only JSON lines outside the database and outside DB backups."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()

    def entries(self):
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def contains(self, deletion_id):
        return any(entry["deletion_id"] == deletion_id for entry in self.entries())

    def append(self, entry):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class DeletionExecutor:
    def __init__(self, engine, settings: Settings):
        self.engine = engine
        self.settings = settings
        self.store = LocalAssetStore(settings.asset_storage_dir)
        self.ledger = DeletionLedger(settings.deletion_ledger_path) if settings.deletion_ledger_path else None

    def _retention(self):
        return timedelta(seconds=max(self.settings.device_cache_retention_seconds, self.settings.device_session_ttl_seconds))

    def _claim(self):
        timestamp = now()
        with Session(self.engine, expire_on_commit=False) as session:
            # A request still inside its device window must not hold back newer server-side deletions.
            devices_due = (DeletionRequest.status == "waiting_devices") & (DeletionRequest.server_completed_at <= timestamp - self._retention())
            eligible = or_(DeletionRequest.status == "accepted", (DeletionRequest.status == "running") & (DeletionRequest.lease_expires_at <= timestamp), devices_due)
            row = session.scalar(select(DeletionRequest).where(eligible).order_by(DeletionRequest.requested_at).with_for_update(skip_locked=True).limit(1))
            if row is None:
                return None
            if row.status == "waiting_devices":
                return self._finish_devices(session, row, timestamp)
            token = new_id("lease")
            result = session.execute(update(DeletionRequest).where(DeletionRequest.id == row.id, DeletionRequest.revision == row.revision).values(status="running", revision=row.revision + 1, lease_token=token, lease_expires_at=timestamp + timedelta(minutes=10)).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                session.rollback()
                return None
            session.commit()
            return row.id, token

    def _finish_devices(self, session, row, timestamp):
        server_done = row.server_completed_at.replace(tzinfo=row.server_completed_at.tzinfo or timestamp.tzinfo)
        if timestamp < server_done + self._retention():
            session.rollback()
            return None
        row.targets = {**row.targets, "device_caches": "done"}
        row.status = "completed"
        row.completed_at = timestamp
        row.revision += 1
        session.commit()
        return None

    def run_once(self) -> bool:
        if self.ledger is None:
            return False
        claim = self._claim()
        if claim is None:
            return False
        identifier, token = claim
        try:
            with Session(self.engine) as session:
                row = session.get(DeletionRequest, identifier)
                if row.lease_token != token:
                    return True
                homework_ids = list(session.scalars(select(Homework.id).where(Homework.student_id == row.student_id)))
                from .practice_photo import practice_photo_asset_ids
                asset_ids = set(session.scalars(select(Asset.id).where(Asset.homework_id.in_(homework_ids)))) if homework_ids else set()
                asset_ids = sorted(asset_ids | set(practice_photo_asset_ids(session, row.tenant_id, row.student_id)))
                if not self.ledger.contains(row.id):
                    self.ledger.append({"deletion_id": row.id, "tenant_id": row.tenant_id, "student_id": row.student_id, "asset_ids": asset_ids, "requested_at": iso(row.requested_at)})
                row.targets = {**row.targets, "backup_tombstone": "done"}
                session.commit()
            with Session(self.engine) as session:
                row = session.get(DeletionRequest, identifier)
                purge_student(session, self.store, row.tenant_id, row.student_id)
                targets = dict(row.targets)
                if targets["original_assets"] == "pending":
                    targets["original_assets"] = "done"
                targets["sensitive_records"] = "done"
                row.targets = targets
                row.status = "waiting_devices"
                row.server_completed_at = now()
                row.revision += 1
                row.lease_token = None
                row.lease_expires_at = None
                session.commit()
        except Exception:
            # Never log record contents; the lease expires and the job is retried.
            with Session(self.engine) as session:
                row = session.get(DeletionRequest, identifier)
                if row is not None and row.lease_token == token:
                    row.failure_code = "execution_interrupted"
                    session.commit()
        return True

    def reapply_ledger(self) -> int:
        """Re-run deletions after restoring a database/object backup."""
        if self.ledger is None:
            raise RuntimeError("DELETION_LEDGER_PATH is required")
        count = 0
        for entry in self.ledger.entries():
            with Session(self.engine) as session:
                purge_student(session, self.store, entry["tenant_id"], entry["student_id"], entry.get("asset_ids", ()))
                session.commit()
            count += 1
        return count


def main():
    parser = argparse.ArgumentParser(description="Execute or re-apply student data deletions")
    parser.add_argument("--once", action="store_true", help="Process at most one pending deletion step")
    parser.add_argument("--reapply-ledger", action="store_true", help="Purge every ledger entry again, e.g. after a backup restore")
    args = parser.parse_args()
    settings = Settings.from_environment()
    engine = create_database_engine(settings.database_url, allow_sqlite=settings.allow_sqlite_for_tests)
    try:
        executor = DeletionExecutor(engine, settings)
        if args.reapply_ledger:
            print(f"reapplied_deletions={executor.reapply_ledger()}")
            return
        while True:
            worked = executor.run_once()
            if args.once:
                break
            if not worked:
                time.sleep(5)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        print("deletion_executor_stopped: configuration or service unavailable", file=sys.stderr)
        raise SystemExit(1) from None
