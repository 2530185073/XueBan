"""Offline worker contention: migrated temporary SQLite, real API, fake models.

Locks are held by an independent sqlite3 connection. No timing races, runtime
configuration, deployment keys, or model HTTP requests are needed. Lease expiry
is advanced in the private database instead of sleeping through retry windows.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import socket
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401 -- real migrated database/API fixture
from test_learning import prepare_latest_math_curriculum
from test_multimodal_worker import assessment
from xueban_backend import learning_projection
from xueban_backend.config import Settings
from xueban_backend.correction_core import now
from xueban_backend.correction_models import CorrectionEvent, Entity, Task
from xueban_backend.learning_jobs import LearningWorker
from xueban_backend.learning_models import (
    LearningEvidence, LearningOutbox, QuestionKnowledgeLink, StudentLearningProfile,
)
from xueban_backend.providers.correction_pipeline import CorrectionPipeline
from xueban_backend.worker import run_round


pytestmark = pytest.mark.parametrize("database", ["sqlite"], indirect=True)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Contention regression must not load runtime settings or use the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(Settings, "from_environment", forbidden)
    monkeypatch.setattr(CorrectionPipeline, "from_env", forbidden)


@pytest.fixture
def contention(flow, monkeypatch):
    # Keep the real projection handler; unrelated scheduled report/memory work
    # would obscure which transaction encountered the deliberately held lock.
    monkeypatch.setattr(learning_projection, "PERIODIC", [])
    monkeypatch.setattr(learning_projection, "PROJECTION_FOLLOWUPS", [])
    clock = [1000.0]
    monkeypatch.setattr(learning_projection, "monotonic", lambda: clock[0])
    prepare_latest_math_curriculum(flow)
    recognize, check = flow.pipeline.recognize, flow.pipeline.check

    def recognize_math(**context):
        result = recognize(**context)
        for item in result.data["items"]:
            item.update(subject="数学", answer_text="2")
        return result

    def check_math(**context):
        result = check(**context)
        return replace(result, data={
            **result.data, "knowledge_points": ["有理数的加减法"],
            "verification": assessment("correct")["verification"],
            "uncertainties": [], "correct_answer": None, "correction_method": None,
        })

    monkeypatch.setattr(flow.pipeline, "recognize", recognize_math)
    monkeypatch.setattr(flow.pipeline, "check", check_math)
    flow.pipeline.verdict = "correct"
    case = flow.ready()
    job = flow.start_check(case)
    worker = LearningWorker(flow.database.engine, client=None, scan_interval=timedelta(0))
    failures = []

    def short_timeout(connection, *_):
        connection.execute("PRAGMA busy_timeout=1")

    def record_error(context):
        if isinstance(context.original_exception, sqlite3.OperationalError):
            failures.append((context.statement or "", context.original_exception.sqlite_errorcode))

    event.listen(flow.database.engine, "checkout", short_timeout)
    event.listen(flow.database.engine, "handle_error", record_error)
    try:
        yield SimpleNamespace(flow=flow, learner=worker, case=case, job=job, failures=failures, clock=clock)
    finally:
        event.remove(flow.database.engine, "checkout", short_timeout)
        event.remove(flow.database.engine, "handle_error", record_error)


@contextmanager
def write_lock(engine):
    """A real, separate writer; release even when the tested worker crashes."""
    connection = sqlite3.connect(engine.url.database, isolation_level=None, timeout=0.05)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def get(flow, path):
    response = flow.client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def business_records(flow):
    """Compare identities and documents so replacements cannot hide behind counts."""
    with Session(flow.database.engine) as session:
        return {
            "entities": [(row.id, row.kind, row.revision, deepcopy(row.document)) for row in
                         session.scalars(select(Entity).where(Entity.homework_id == flow.homework_id)
                                         .order_by(Entity.id))],
            "events": [(row.id, row.kind, row.aggregate_id) for row in session.scalars(
                select(CorrectionEvent).where(CorrectionEvent.homework_id == flow.homework_id)
                .order_by(CorrectionEvent.id))],
            "links": [(row.id, row.question_id, row.status, deepcopy(row.observation_bindings))
                      for row in session.scalars(select(QuestionKnowledgeLink).order_by(QuestionKnowledgeLink.id))],
            "evidence": [(row.id, row.source_key, row.validity, deepcopy(row.document))
                         for row in session.scalars(select(LearningEvidence).order_by(LearningEvidence.id))],
        }


def finish_and_assert_once(context):
    flow, learner = context.flow, context.learner
    # Advance past the bounded local cooldown without bypassing production
    # retry guards or waiting in real time. The lease still gates recovery.
    context.clock[0] += 31
    for _ in range(8):
        if not run_round(learner.run_once, flow.worker.run_once):
            break
    else:
        pytest.fail("Released SQLite lock did not allow the queues to drain")

    assert get(flow, f"/v1/jobs/{context.job['id']}")["status"] == "succeeded"
    case = get(flow, f"/v1/cases/{context.case['id']}")
    assert case["current_decision"]["verdict"] == "correct"
    assert len(case["check_ids"]) == 1
    assert flow.pipeline.calls == ["recognize", "check"]
    with Session(flow.database.engine) as session:
        task = session.get(Task, context.job["id"])
        assert task.tries == 1
        assert len(task.audit["executions"]) == 1
        assert task.lease_token is task.lease_expires_at is None
        jobs = session.scalars(select(LearningOutbox)).all()
        assert jobs and all(job.status == "succeeded" for job in jobs)
        assert all(job.lease_token is job.lease_expires_at is None for job in jobs)

    before = business_records(flow)
    assert sum(row[1] == "check" for row in before["entities"]) == 1
    assert sum(row[1] == "decision" for row in before["entities"]) == 1
    assert len(before["links"]) == len(before["evidence"]) == 1
    for _ in range(3):
        assert not run_round(learner.run_once, flow.worker.run_once)
    assert business_records(flow) == before
    assert flow.pipeline.calls == ["recognize", "check"]


def test_sqlite_connections_use_wal_and_allow_writer_with_open_reader(database):
    with database.engine.connect() as reader, database.engine.connect() as writer:
        for connection in (reader, writer):
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() >= 1000
        statement = text("SELECT display_name FROM students WHERE id = 'student_a'")
        original = reader.execute(statement).scalar_one()
        # A rollback-journal regression fails promptly instead of spending the
        # production busy_timeout waiting for this test's deliberately open reader.
        writer.exec_driver_sql("PRAGMA busy_timeout=1")
        writer.execute(text("UPDATE students SET display_name = 'synthetic-writer' WHERE id = 'student_a'"))
        writer.commit()
        assert reader.execute(statement).scalar_one() == original
        reader.rollback()
        assert reader.execute(statement).scalar_one() == "synthetic-writer"


@pytest.mark.parametrize("phase", ["scan", "claim"])
def test_learning_write_lock_preserves_queue_and_recovers_without_duplicate_calls(contention, phase):
    context = contention
    flow, learner = context.flow, context.learner
    if phase == "claim":
        learner.scan(force=True)
        learner.next_scan = now() + timedelta(hours=1)
    with write_lock(flow.database.engine):
        # Direct invocation also verifies LearningWorker's own boundary. The
        # round-level guard must not conceal a missing scan/claim guard here.
        assert isinstance(learner.run_once(), bool)
        with Session(flow.database.engine) as session:
            task = session.get(Task, context.job["id"])
            assert (task.status, task.tries) == ("queued", 0)
            assert task.lease_token is None
            pending = session.scalars(select(LearningOutbox)).all()
            if phase == "claim":
                assert len(pending) == 1
            assert all(row.status == "queued" and row.tries == 0 and row.lease_token is None for row in pending)
        assert flow.pipeline.calls == ["recognize"]
    assert context.failures
    assert all(code & 0xFF == sqlite3.SQLITE_BUSY for _, code in context.failures)
    assert any(("INSERT" if phase == "scan" else "UPDATE") in statement.upper()
               and "learning_outbox" in statement for statement, _ in context.failures)
    learner.next_scan = now() - timedelta(seconds=1)
    finish_and_assert_once(context)


def test_failed_learning_failure_writeback_keeps_lease_and_does_not_block_correction(contention, monkeypatch):
    context = contention
    flow, learner = context.flow, context.learner
    learner.scan(force=True)
    original = learning_projection.JOB_HANDLERS["project_student"]
    attempts = []
    lock = sqlite3.connect(flow.database.engine.url.database, isolation_level=None, timeout=0.05)

    def fail_with_locked_failure_record(session, job, worker):
        attempts.append(job.id)
        if len(attempts) == 1:
            lock.execute("BEGIN IMMEDIATE")
            # The original failure precedes inference. Its status UPDATE then
            # encounters a *second*, genuine SQLite OperationalError.
            raise RuntimeError("synthetic pre-model learning failure")
        return original(session, job, worker)

    monkeypatch.setitem(learning_projection.JOB_HANDLERS, "project_student", fail_with_locked_failure_record)
    try:
        assert isinstance(learner.run_once(), bool)
        with Session(flow.database.engine) as session:
            job = session.get(LearningOutbox, attempts[0])
            assert (job.status, job.tries) == ("running", 1)
            assert job.lease_token and job.lease_expires_at is not None
        assert any("UPDATE learning_outbox" in statement and code & 0xFF == sqlite3.SQLITE_BUSY
                   for statement, code in context.failures)
    finally:
        lock.rollback()
        lock.close()

    # A surviving worker can finish the pending correction even before the
    # failed learning job's original lease is eligible for recovery.
    assert run_round(learner.run_once, flow.worker.run_once)
    assert get(flow, f"/v1/jobs/{context.job['id']}")["status"] == "succeeded"
    assert len(attempts) == 1
    with Session(flow.database.engine) as session, session.begin():
        job = session.get(LearningOutbox, attempts[0])
        job.lease_expires_at = now() - timedelta(seconds=1)

    finish_and_assert_once(context)
    assert attempts == [attempts[0], attempts[0]]
    with Session(flow.database.engine) as session:
        assert session.get(LearningOutbox, attempts[0]).tries == 2


def test_unhandled_runner_database_failure_still_runs_pending_correction(contention):
    context = contention
    flow = context.flow
    visited = []

    def failed_runner():
        visited.append("failed")
        with write_lock(flow.database.engine), Session(flow.database.engine) as session, session.begin():
            session.execute(update(StudentLearningProfile).where(StudentLearningProfile.student_id == "student_a")
                            .values(revision=StudentLearningProfile.revision + 1))

    def correction_runner():
        visited.append("correction")
        return flow.worker.run_once()

    assert run_round(failed_runner, correction_runner)
    assert visited == ["failed", "correction"]
    assert context.failures and context.failures[0][1] & 0xFF == sqlite3.SQLITE_BUSY
    assert get(flow, f"/v1/jobs/{context.job['id']}")["status"] == "succeeded"
    finish_and_assert_once(context)
