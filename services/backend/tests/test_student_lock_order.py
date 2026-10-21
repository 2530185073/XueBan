"""Actual correction entry points wait on Student before locking child rows.

The second session represents the existing learning/deletion Student-first
transaction. PostgreSQL's blocking graph, not timing alone, proves the wait;
NOWAIT then proves that the waiting entry point owns no conflicting child lock.
All data and connections use the fixture's disposable migrated schema.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_recognition_recheck import (  # noqa: F401
    attempt_for, recovery_flow, recheck, request_body,
)
from xueban_backend.correction_models import Entity, Task
from xueban_backend.models import Homework, Student
from xueban_backend.recognition_recheck import KIND


def _while_student_locked(flow, case, operation, *, engine, task_id=None, deactivate=False):
    """Run an actual API/worker operation against a contending DB transaction."""
    student_statement = Event()
    operation_pid = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if "students" in statement and "FOR UPDATE" in statement:
            operation_pid.append(conn.connection.driver_connection.info.backend_pid)
            student_statement.set()

    with Session(flow.database.engine) as holder:
        holder_pid = holder.scalar(text("SELECT pg_backend_pid()"))
        student = holder.scalar(select(Student).where(Student.id == "student_a").with_for_update())
        event.listen(engine, "before_cursor_execute", observe)
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(operation)
        try:
            deadline = monotonic() + 5
            with flow.database.engine.connect() as monitor:
                while monotonic() < deadline:
                    if future.done():
                        # Propagate an operation error rather than hiding it
                        # behind a generic missing-wait assertion.
                        future.result()
                        pytest.fail("operation completed without waiting for Student")
                    if student_statement.is_set():
                        blockers = monitor.scalar(text("SELECT pg_blocking_pids(:pid)"),
                                                  {"pid": operation_pid[-1]})
                        if holder_pid in blockers:
                            break
                    sleep(0.02)
                else:
                    pytest.fail("actual operation did not block on the held Student row")

            # A reverse order would already own one of these row locks and
            # fail NOWAIT here. Holding them until release also exercises the
            # complete Student -> Task -> Homework -> Question ordering.
            if task_id is not None:
                assert holder.scalar(select(Task).where(Task.id == task_id).with_for_update(nowait=True))
            homework = holder.scalar(select(Homework).where(Homework.id == flow.homework_id)
                                     .with_for_update(nowait=True))
            assert homework is not None
            assert holder.scalar(select(Entity).where(Entity.id == case["question_id"])
                                 .with_for_update(nowait=True))
            assert not future.done()
            if deactivate:
                student.active = False
                holder.commit()
            else:
                holder.rollback()
            return future.result(timeout=8)
        finally:
            holder.rollback()
            event.remove(engine, "before_cursor_execute", observe)
            pool.shutdown(wait=True)


@pytest.mark.parametrize("entry", ["http_recheck", "worker_dispatch", "worker_finish"])
@pytest.mark.parametrize("deactivate", [False, True], ids=["successful-release", "student-deactivated-while-waiting"])
def test_correction_waits_before_children_and_rechecks_student(recovery_flow, entry, deactivate):
    flow, case, control = recovery_flow
    if flow.database.dialect != "postgresql":
        pytest.skip("row-lock contention requires PostgreSQL")
    original = attempt_for(flow, case)
    identifier = None
    if entry == "http_recheck":
        body = request_body(flow, case)
        operation = lambda: flow.post(f"/v1/cases/{case['id']}/recognition-rechecks", body,
                                      "student-first-http-recheck")
        engine = flow.client.app.state.engine
    else:
        assert recheck(flow, case).status_code == 202
        identifier, token = flow.worker.claim()
        engine = flow.worker.engine
        if entry == "worker_dispatch":
            operation = lambda: flow.worker._dispatch_original_recheck(identifier, token)
        else:
            _, context = flow.worker._load(identifier, token)
            assert flow.worker._dispatch_original_recheck(identifier, token)
            candidate = flow.pipeline.rerecognize_original(**context)
            operation = lambda: flow.worker.finish(identifier, token, result=candidate)

    result = _while_student_locked(flow, case, operation, engine=engine,
                                   task_id=identifier, deactivate=deactivate)
    with Session(flow.database.engine) as session:
        attempt = session.get(Entity, original["id"])
        if entry == "http_recheck":
            assert result.status_code == (404 if deactivate else 202), result.text
            count = session.scalar(select(func.count()).select_from(Task).where(
                Task.homework_id == flow.homework_id, Task.kind == KIND))
            assert count == (0 if deactivate else 1)
        elif entry == "worker_dispatch":
            assert result is (not deactivate)
            task = session.get(Task, identifier)
            assert bool(task.audit.get("recheck_dispatched")) is (not deactivate)
            assert task.status == "running"
        else:
            assert result is True
            task = session.get(Task, identifier)
            assert task.status == ("superseded" if deactivate else "succeeded")
            assert task.audit["executions"][0]["candidate"]["items"][0]["answer_text"] == "2"
            if not deactivate:
                assert attempt.document["recognition_revision"] == original["recognition_revision"] + 1
                assert attempt.document["answer_text"] == "2"
                assert task.audit["recovery_application"]["after"]["attempt_recognition_revision"] == 2
        if entry != "worker_finish" or deactivate:
            assert attempt.document == original
    assert len(control.calls) == (1 if entry == "worker_finish" else 0)
