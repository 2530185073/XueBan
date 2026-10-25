"""Offline worker recovery: synthetic jobs only, with no provider or real database."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from xueban_backend import learning_projection as projection
from xueban_backend.db import create_database_engine
from xueban_backend.learning_models import LearningOutbox


def database_error():
    return OperationalError("SELECT private_sql", {"student": "private_student"},
                            sqlite3.OperationalError("private_api_key"))


@contextmanager
def listen(target, name, callback):
    event.listen(target, name, callback)
    try:
        yield
    finally:
        event.remove(target, name, callback)


def setup_runtime(engine, monkeypatch):
    LearningOutbox.__table__.create(engine)
    clock = [datetime(2026, 10, 25, tzinfo=timezone.utc)]
    elapsed = [100.0]
    calls = []

    def handler(session, job, worker):
        calls.append(job.id)
        return {"status": "synthetic_success"}

    monkeypatch.setattr(projection, "now", lambda: clock[0])
    monkeypatch.setattr(projection, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(projection, "dirty_students", lambda session: [])
    monkeypatch.setattr(projection, "PERIODIC", [])
    monkeypatch.setattr(projection, "PROJECTION_FOLLOWUPS", [])
    monkeypatch.setattr(projection, "JOB_HANDLERS", {"synthetic_paid": handler})
    with Session(engine) as session, session.begin():
        identifier = projection.enqueue(session, "synthetic_tenant", "synthetic_student",
                                        "synthetic_paid", {}, "synthetic_dedupe")
    return SimpleNamespace(engine=engine, worker=projection.LearningWorker(engine),
                           clock=clock, elapsed=elapsed, calls=calls, identifier=identifier)


@pytest.fixture
def runtime(monkeypatch):
    engine = create_database_engine("sqlite:///:memory:", allow_sqlite=True)
    try:
        yield setup_runtime(engine, monkeypatch)
    finally:
        engine.dispose()


def job_state(runtime):
    with Session(runtime.engine, expire_on_commit=False) as session:
        return session.get(LearningOutbox, runtime.identifier)


def assert_safe_logs(caplog):
    records = [record for record in caplog.records if record.name == projection.__name__]
    assert records
    for record in records:
        assert record.exc_info is None
        assert record.getMessage().split(": ")[-1] in {
            "database_busy", "database_locked", "database_contention",
            "database_integrity_error", "database_error", "job_error",
        }
    assert "private_" not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.parametrize("phase", ["scan", "claim"])
@pytest.mark.parametrize("failure_point", ["read", "commit"])
def test_scan_and_claim_database_errors_roll_back_and_recover(runtime, monkeypatch, caplog, phase, failure_point):
    if phase == "scan":
        def dirty(session):
            session.scalar(select(LearningOutbox.id))
            return [("synthetic_tenant", "new_synthetic_student")]
        monkeypatch.setattr(projection, "dirty_students", dirty)

    def fail(*args):
        raise database_error()

    target, name = ((runtime.engine, "before_cursor_execute") if failure_point == "read"
                    else (runtime.engine, "commit"))
    with listen(target, name, fail):
        assert runtime.worker.run_once() is False
    job = job_state(runtime)
    assert (job.status, job.tries, job.lease_token, job.payload) == ("queued", 0, None, {})
    with Session(runtime.engine) as session:
        assert list(session.scalars(select(LearningOutbox.id))) == [runtime.identifier]
    assert runtime.calls == []
    assert f"learning worker {phase} failed: database_error" in caplog.text
    assert_safe_logs(caplog)
    monkeypatch.setattr(projection, "dirty_students", lambda session: [])
    runtime.elapsed[0] += 1
    assert runtime.worker.run_once() is True
    assert job_state(runtime).status == "succeeded"


@pytest.mark.parametrize("failure_point", ["read", "flush", "commit"])
def test_handler_database_failure_is_recorded_after_rollback(runtime, monkeypatch, caplog, failure_point):
    def handler(session, job, worker):
        runtime.calls.append(job.id)
        job.payload = {"must_rollback": True}
        if failure_point == "read":
            session.execute(text("SELECT * FROM synthetic_missing_table"))
        if failure_point == "flush":
            session.flush()
        return {"status": "done"}

    def fail(session, *args):
        if any(isinstance(row, LearningOutbox) and row.payload.get("must_rollback")
               for row in session.dirty):
            raise database_error()

    monkeypatch.setitem(projection.JOB_HANDLERS, "synthetic_paid", handler)
    with listen(Session, "before_commit" if failure_point == "commit" else "before_flush", fail):
        assert runtime.worker.run_once() is True
    job = job_state(runtime)
    assert (job.status, job.tries, job.payload, job.last_error) == ("queued", 1, {}, "database_error")
    assert job.lease_token is None and job.lease_expires_at is None
    assert job.available_at.replace(tzinfo=timezone.utc) == runtime.clock[0] + timedelta(seconds=10)
    assert runtime.worker.run_once() is False
    assert len(runtime.calls) == 1
    assert_safe_logs(caplog)


@pytest.mark.parametrize("failure_point", ["read", "flush", "commit"])
def test_failure_record_error_keeps_lease_until_expiry(runtime, monkeypatch, caplog, failure_point):
    claimed = []

    def handler(session, job, worker):
        runtime.calls.append(job.id)
        claimed.append((job.lease_token, job.lease_expires_at))
        if len(runtime.calls) == 1:
            job.payload = {"must_rollback": True}
            raise database_error()
        return {"status": "recovered"}

    def fail(*args):
        if runtime.calls:
            raise database_error()

    monkeypatch.setitem(projection.JOB_HANDLERS, "synthetic_paid", handler)
    target, name = ((runtime.engine, "before_cursor_execute") if failure_point == "read"
                    else (Session, "before_flush" if failure_point == "flush" else "before_commit"))
    with listen(target, name, fail):
        assert runtime.worker.run_once() is True
    job = job_state(runtime)
    assert (job.status, job.tries, job.last_error, job.payload) == ("running", 1, None, {})
    assert (job.lease_token, job.lease_expires_at) == claimed[0]
    assert "learning worker failure_record failed: database_error" in caplog.text
    assert_safe_logs(caplog)
    assert runtime.worker.run_once() is False
    assert len(runtime.calls) == 1
    runtime.elapsed[0] += 1
    assert runtime.worker.run_once() is False  # Cooldown ended; the live lease still fences execution.
    runtime.clock[0] += projection.LEASE + timedelta(microseconds=1)
    assert runtime.worker.run_once() is True
    assert len(runtime.calls) == 2
    assert job_state(runtime).status == "succeeded"
    assert claimed[0][0] != claimed[1][0]


def test_recorded_failures_keep_bounded_retry_and_mapping_audit(runtime, monkeypatch, caplog):
    with Session(runtime.engine) as session, session.begin():
        session.get(LearningOutbox, runtime.identifier).kind = "map_model_batch"

    def handler(session, job, worker):
        runtime.calls.append(job.id)
        job.payload = {"must_rollback": True}
        error = RuntimeError("private_api_key")
        error.code = {"private_student": "private_sql"}
        error.learning_audit = {"status": "synthetic_failure"}
        raise error

    monkeypatch.setitem(projection.JOB_HANDLERS, "map_model_batch", handler)
    for attempt in range(1, projection.MAX_TRIES + 1):
        assert runtime.worker.run_once() is True
        job = job_state(runtime)
        assert job.tries == attempt and job.last_error == "job_error"
        assert job.status == ("dead" if attempt == projection.MAX_TRIES else "queued")
        assert job.lease_token is None and job.lease_expires_at is None
        assert job.payload == {"failed_executions": [{"status": "synthetic_failure"}] * attempt}
        assert runtime.worker.run_once() is False
        delay = min(300, 5 * 2 ** attempt)
        assert job.available_at.replace(tzinfo=timezone.utc) == runtime.clock[0] + timedelta(seconds=delay)
        runtime.clock[0] += timedelta(seconds=delay)
    assert runtime.worker.run_once() is False
    assert len(runtime.calls) == projection.MAX_TRIES
    assert_safe_logs(caplog)


@pytest.mark.parametrize("during_failure", [False, True])
def test_stale_token_cannot_run_or_requeue_job(runtime, monkeypatch, during_failure):
    def replace_token():
        with Session(runtime.engine) as session, session.begin():
            session.get(LearningOutbox, runtime.identifier).lease_token = "new_owner"

    if during_failure:
        def handler(session, job, worker):
            runtime.calls.append(job.id)
            job.payload = {"must_rollback": True}
            session.rollback()
            replace_token()
            raise database_error()
        monkeypatch.setitem(projection.JOB_HANDLERS, "synthetic_paid", handler)
    else:
        claim = runtime.worker.claim
        def stale_claim():
            result = claim()
            replace_token()
            return result
        monkeypatch.setattr(runtime.worker, "claim", stale_claim)
    assert runtime.worker.run_once() is True
    job = job_state(runtime)
    assert (job.status, job.tries, job.last_error, job.payload, job.lease_token) == ("running", 1, None, {}, "new_owner")
    assert len(runtime.calls) == int(during_failure)


@pytest.mark.parametrize("phase", ["scan", "claim", "handler", "failure_record", "followup"])
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_propagate(runtime, monkeypatch, phase, interrupt):
    def stop(*args, **kwargs):
        raise interrupt()

    if phase in {"scan", "claim"}:
        monkeypatch.setattr(runtime.worker, phase, stop)
    elif phase == "handler":
        monkeypatch.setitem(projection.JOB_HANDLERS, "synthetic_paid", stop)
    elif phase == "followup":
        with Session(runtime.engine) as session, session.begin():
            session.get(LearningOutbox, runtime.identifier).kind = "project_student"
        monkeypatch.setitem(projection.JOB_HANDLERS, "project_student", lambda *args: {})
        monkeypatch.setattr(projection, "PROJECTION_FOLLOWUPS", [stop])
    else:
        def handler(session, job, worker):
            runtime.calls.append(job.id)
            raise database_error()
        monkeypatch.setitem(projection.JOB_HANDLERS, "synthetic_paid", handler)

    def interrupt_recovery(*args):
        if phase == "failure_record" and runtime.calls:
            stop()

    with listen(runtime.engine, "before_cursor_execute", interrupt_recovery):
        with pytest.raises(interrupt):
            runtime.worker.run_once()


def test_followup_failure_does_not_repeat_succeeded_handler(runtime, monkeypatch, caplog):
    with Session(runtime.engine) as session, session.begin():
        session.get(LearningOutbox, runtime.identifier).kind = "project_student"
    monkeypatch.setitem(projection.JOB_HANDLERS, "project_student", projection.JOB_HANDLERS["synthetic_paid"])

    def followup(session, *args):
        projection.enqueue(session, "synthetic_tenant", "synthetic_student", "synthetic_paid", {}, "followup")
        session.flush()
        raise database_error()

    monkeypatch.setattr(projection, "PROJECTION_FOLLOWUPS", [followup])
    assert runtime.worker.run_once() is True
    assert job_state(runtime).status == "succeeded"
    assert runtime.worker.run_once() is False
    assert len(runtime.calls) == 1
    assert "learning worker followup failed: database_error" in caplog.text
    assert_safe_logs(caplog)


def test_drain_scan_error_is_contained(runtime, monkeypatch, caplog):
    calls = []
    def fail(session):
        calls.append(True)
        raise database_error()
    monkeypatch.setattr(projection, "dirty_students", fail)
    assert runtime.worker.drain() == 0
    assert runtime.worker.drain() == 0
    assert len(calls) == 1
    assert job_state(runtime).tries == 0
    assert_safe_logs(caplog)


@pytest.mark.parametrize("blocker_kind", ["writer", "reader"])
def test_real_sqlite_lock_during_handler_and_failure_record_is_contained(tmp_path, monkeypatch, caplog, blocker_kind):
    # Intentionally reproduce the original DELETE-journal failure independently
    # of the application's WAL setting. Only this synthetic temporary file opens.
    path = tmp_path / "isolated_recovery.sqlite"
    engine = create_engine(f"sqlite:///{path}", connect_args={"autocommit": False, "timeout": 0.01})
    blocker = None
    try:
        runtime = setup_runtime(engine, monkeypatch)
        with engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "delete"
            connection.exec_driver_sql("CREATE TABLE lock_holder (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO lock_holder VALUES (0)")
        blocker = sqlite3.connect(path, autocommit=False)

        def handler(session, job, worker):
            runtime.calls.append(job.id)
            if blocker_kind == "writer":
                blocker.execute("UPDATE lock_holder SET value=1")
            else:
                blocker.execute("SELECT * FROM lock_holder").fetchall()
            job.payload = {"must_rollback": True}
            session.flush()
            return {"status": "done"}

        monkeypatch.setitem(projection.JOB_HANDLERS, "synthetic_paid", handler)
        assert runtime.worker.run_once() is True
        blocker.rollback()
        job = job_state(runtime)
        assert (job.status, job.tries, job.last_error, job.payload) == ("running", 1, None, {})
        assert job.lease_token is not None and job.lease_expires_at is not None
        assert runtime.worker.run_once() is False
        runtime.elapsed[0] += 1
        assert runtime.worker.run_once() is False
        assert len(runtime.calls) == 1
        assert "learning worker handler failed: database_busy" in caplog.text
        assert "learning worker failure_record failed: database_busy" in caplog.text
        assert_safe_logs(caplog)
    finally:
        if blocker is not None:
            blocker.rollback()
            blocker.close()
        engine.dispose()


@pytest.mark.parametrize("phase", ["scan", "claim", "failure_record"])
def test_internal_faults_back_off_without_db_access_and_reset_on_success(runtime, monkeypatch, caplog, phase):
    failing = [True]
    failures, scans, queries = [], [], []
    scan, claim = runtime.worker.scan, runtime.worker.claim
    original_handler = projection.JOB_HANDLERS["synthetic_paid"]

    def scan_with_fault(force=False):
        scans.append(True)
        if phase == "scan" and failing[0]:
            failures.append("scan")
            raise database_error()
        return scan(force=force)

    def claim_with_fault():
        if phase == "claim" and failing[0]:
            failures.append("claim")
            raise database_error()
        return claim()

    def handler(session, job, worker):
        if phase == "failure_record" and failing[0]:
            failures.append(job.id)
            raise database_error()
        return original_handler(session, job, worker)

    def fail_record(session, *args):
        if phase == "failure_record" and failing[0] and any(
                isinstance(row, LearningOutbox) and row.status == "queued" and row.last_error
                for row in session.dirty):
            raise database_error()

    monkeypatch.setattr(runtime.worker, "scan", scan_with_fault)
    monkeypatch.setattr(runtime.worker, "claim", claim_with_fault)
    monkeypatch.setitem(projection.JOB_HANDLERS, "synthetic_paid", handler)
    if phase == "failure_record":
        # Distinct jobs let successive recovery-write failures accumulate without
        # touching any live lease or rerunning a possibly billed handler.
        with Session(runtime.engine) as session, session.begin():
            for index in range(8):
                projection.enqueue(session, "synthetic_tenant", "synthetic_student",
                                   "synthetic_paid", {}, f"extra:{index}")

    with listen(Session, "before_flush", fail_record), listen(
            runtime.engine, "before_cursor_execute", lambda *args: queries.append(True)):
        for attempt, delay in enumerate((1, 2, 4, 8, 16, 30, 30), start=1):
            assert runtime.worker.run_once() is (phase == "failure_record")
            assert len(failures) == attempt
            snapshot = (len(scans), len(queries), len(caplog.records))
            for _ in range(5):
                assert runtime.worker.run_once() is False
            runtime.elapsed[0] += delay - 0.125
            assert runtime.worker.run_once() is False
            assert (len(scans), len(queries), len(caplog.records)) == snapshot
            runtime.elapsed[0] += 0.125

        failing[0] = False
        assert runtime.worker.run_once() is True
        assert len(runtime.calls) == 1
        if phase == "failure_record":
            assert len(set(failures)) == len(failures)
            with Session(runtime.engine) as session:
                rows = list(session.scalars(select(LearningOutbox).where(LearningOutbox.id.in_(failures))))
                assert all(row.status == "running" and row.tries == 1 and row.lease_token
                           and row.last_error is None for row in rows)
                assert all(row.lease_expires_at.replace(tzinfo=timezone.utc) == runtime.clock[0] + projection.LEASE
                           for row in rows)
        failing[0] = True
        runtime.worker.run_once()
        assert len(failures) == 8
        # A success resets the next failure to one second, not the previous cap.
        snapshot = len(scans)
        runtime.elapsed[0] += 0.875
        assert runtime.worker.run_once() is False
        assert len(scans) == snapshot
        runtime.elapsed[0] += 0.125
        runtime.worker.run_once()
        assert len(scans) == snapshot + 1
    assert_safe_logs(caplog)


def test_wall_clock_jump_cannot_bypass_local_cooldown(runtime, monkeypatch):
    claim = runtime.worker.claim
    def fail():
        raise database_error()
    monkeypatch.setattr(runtime.worker, "claim", fail)
    assert runtime.worker.run_once() is False
    monkeypatch.setattr(runtime.worker, "claim", claim)
    runtime.clock[0] += timedelta(days=1)
    assert runtime.worker.run_once() is False
    assert runtime.calls == []
    runtime.elapsed[0] += 1
    assert runtime.worker.run_once() is True
