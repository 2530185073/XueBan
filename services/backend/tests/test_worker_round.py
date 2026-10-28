"""Offline scheduling checks with stubs or isolated SQLite; no live worker/model."""
from contextlib import nullcontext
from datetime import timedelta
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401 -- isolated SQLite/synthetic pipeline
from test_recognition_recheck import recovery_flow, recheck  # noqa: F401
from xueban_backend import worker
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Entity, Task
from xueban_backend.worker import RoundRunner, run_round


def test_sustained_correction_backlog_still_runs_learning_jobs():
    calls = []

    def runner(name, busy):
        def run():
            calls.append(name)
            return busy
        return run

    rounds = [run_round(runner("deletion", False), runner("correction", True), runner("learning", True)) for _ in range(3)]
    assert rounds == [True, True, True]
    assert calls.count("learning") == 3 and calls.count("correction") == 3 and calls.count("deletion") == 3
    assert not run_round(runner("deletion", False), runner("correction", False), runner("learning", False))


@pytest.mark.parametrize("failed_index", [0, 1, 2], ids=["first", "middle", "last"])
@pytest.mark.parametrize("healthy_worked", [True, False], ids=["busy", "idle"])
def test_failure_isolated_at_every_position(failed_index, healthy_worked, caplog):
    calls = []

    def runner(index):
        def run():
            calls.append(index)
            if index == failed_index:
                raise RuntimeError("private student and credentials")
            return healthy_worked
        return run

    assert run_round(*(runner(index) for index in range(3))) is healthy_worked
    assert calls == [0, 1, 2]
    assert [record.getMessage() for record in caplog.records] == [
        f"worker_executor_failed: executor={failed_index + 1} category=executor_error",
    ]


def test_multiple_failures_do_not_skip_remaining_executors(caplog):
    runners = [Mock(side_effect=RuntimeError("private")) for _ in range(3)]
    assert run_round(*runners) is False
    assert [runner.call_count for runner in runners] == [1, 1, 1]
    assert len(caplog.records) == 3
    assert run_round() is False


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=100.0)
    monkeypatch.setattr(worker.time, "monotonic", lambda: state.now)
    return state


@pytest.mark.parametrize("failed_index", [0, 1, 2], ids=["first", "middle", "last"])
def test_busy_peers_continue_while_failed_executor_cools_down(clock, failed_index, caplog):
    runners = [Mock(return_value=True) for _ in range(3)]
    runners[failed_index].side_effect = RuntimeError("private")
    loop = RoundRunner(*runners)
    assert loop.run_round() is True
    for _ in range(100):
        assert loop.run_round() is True
    assert [runner.call_count for runner in runners] == [
        1 if index == failed_index else 101 for index in range(3)
    ]
    assert len(caplog.records) == 1
    clock.now += 0.99
    assert loop.run_round() is True
    assert runners[failed_index].call_count == 1
    clock.now += 0.01
    assert loop.run_round() is True
    assert runners[failed_index].call_count == 2
    assert len(caplog.records) == 2


def test_all_failures_have_bounded_exponential_backoff(clock):
    runners = [Mock(side_effect=RuntimeError("private")) for _ in range(3)]
    loop = RoundRunner(*runners)
    for attempt, delay in enumerate([1, 2, 4, 8, 16, 30, 30, 30], start=1):
        assert loop.run_round() is False
        assert loop.idle_delay() == delay
        # Extra polls cannot repeat an executor before its deadline.
        assert loop.run_round() is False
        assert [runner.call_count for runner in runners] == [attempt] * 3
        clock.now += delay


@pytest.mark.parametrize("recovered_worked", [True, False])
def test_recovered_executor_resets_its_backoff(clock, recovered_worked):
    callback = Mock(side_effect=[RuntimeError(), RuntimeError(), recovered_worked, RuntimeError()])
    loop = RoundRunner(callback)
    assert loop.run_round() is False
    clock.now += loop.idle_delay()
    assert loop.run_round() is False
    assert loop.idle_delay() == 2
    clock.now += loop.idle_delay()
    assert loop.run_round() is recovered_worked
    assert loop.run_round() is False
    assert loop.idle_delay() == 1
    assert callback.call_count == 4


def test_idle_peer_keeps_polling_during_another_executors_backoff(clock):
    broken = Mock(side_effect=RuntimeError())
    idle = Mock(return_value=False)
    loop = RoundRunner(broken, idle)
    for _ in range(5):
        assert loop.run_round() is False
        assert loop.idle_delay() == 1
        clock.now += loop.idle_delay()
    assert broken.call_count == 3  # Due at t=0, 1, 3; next attempt at t=7.
    assert idle.call_count == 5


def test_backoff_starts_after_slow_failure_and_sleep_has_a_positive_floor(clock):
    def slow_failure():
        clock.now += 10
        raise RuntimeError()

    callback = Mock(side_effect=slow_failure)
    loop = RoundRunner(callback)
    assert loop.run_round() is False
    assert loop.idle_delay() == 1
    assert loop.run_round() is False
    assert callback.call_count == 1
    clock.now += 0.5
    assert loop.idle_delay() == 1
    assert RoundRunner().idle_delay() == 1


PRIVATE_SQL = "SELECT synthetic_private_student FROM private_records WHERE password=:secret"
PRIVATE_PARAMS = {"secret": "synthetic-private-credential", "student": "synthetic-private-student"}


@pytest.mark.parametrize("failure_kind,code", [
    ("busy", "database_busy"),
    ("locked", "database_locked"),
    ("contention", "database_contention"),
    ("integrity", "database_integrity_error"),
    ("database", "database_error"),
    ("other", "executor_error"),
])
def test_logs_use_fixed_codes_without_sql_params_students_or_credentials(failure_kind, code, caplog, capsys):
    original = sqlite3.OperationalError("synthetic-private-driver-secret")
    if failure_kind in {"busy", "locked"}:
        original.sqlite_errorcode = sqlite3.SQLITE_BUSY if failure_kind == "busy" else sqlite3.SQLITE_LOCKED
    if failure_kind == "contention":
        original.sqlstate = "40P01"
    error_type = IntegrityError if failure_kind == "integrity" else OperationalError
    error = error_type(PRIVATE_SQL, PRIVATE_PARAMS, original)
    if failure_kind == "other":
        error = RuntimeError(PRIVATE_SQL, PRIVATE_PARAMS)
    assert run_round(Mock(side_effect=error)) is False
    assert [record.getMessage() for record in caplog.records] == [
        f"worker_executor_failed: executor=1 category={code}",
    ]
    record = caplog.records[0]
    assert record.args == (1, code)
    assert record.exc_info is None and record.exc_text is None and record.stack_info is None
    captured = capsys.readouterr()
    output = caplog.text + captured.out + captured.err
    for private in (PRIVATE_SQL, *PRIVATE_PARAMS.values(), "synthetic-private-driver-secret", "Traceback"):
        assert private not in output


def test_logging_never_renders_exception_or_callable(caplog):
    class PrivateError(SQLAlchemyError):
        def __str__(self):
            raise AssertionError("must not format private exception")

        def __repr__(self):
            raise AssertionError("must not inspect private exception")

    class PrivateRunner:
        def __call__(self):
            raise PrivateError()

        def __repr__(self):
            raise AssertionError("must not inspect private callable")

    assert run_round(PrivateRunner()) is False
    assert caplog.records[0].getMessage() == "worker_executor_failed: executor=1 category=database_error"


def test_result_truthiness_failure_is_isolated(caplog):
    class BrokenResult:
        def __bool__(self):
            raise RuntimeError("private result")

    remaining = Mock(return_value=True)
    assert run_round(lambda: BrokenResult(), remaining) is True
    remaining.assert_called_once_with()
    assert "private result" not in caplog.text


@pytest.mark.parametrize("exit_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("failed_index", [0, 1, 2], ids=["first", "middle", "last"])
def test_system_exit_propagates_without_running_later_executors(exit_type, failed_index, caplog):
    stopped = exit_type(17)
    runners = [Mock(return_value=True) for _ in range(3)]
    runners[failed_index].side_effect = stopped
    with pytest.raises(exit_type) as caught:
        run_round(*runners)
    assert caught.value is stopped
    assert [runner.call_count for runner in runners] == [
        1 if index <= failed_index else 0 for index in range(3)
    ]
    assert caplog.records == []


@pytest.fixture
def stub_main(monkeypatch):
    """Exercise only scheduling and cleanup; replace every production port."""
    from xueban_backend import governance, learning_jobs

    def install(runners, *, once=False):
        engine = SimpleNamespace(dispose=Mock())
        pipeline = SimpleNamespace(client=object())
        settings = SimpleNamespace(database_url="unused", allow_sqlite_for_tests=True, asset_storage_dir=None,
                                   worker_check_concurrency=1)
        monkeypatch.setattr(worker.Settings, "from_environment", lambda: settings)
        monkeypatch.setattr(worker, "create_database_engine", lambda *args, **kwargs: engine)
        monkeypatch.setattr(worker.CorrectionPipeline, "from_env", lambda: nullcontext(pipeline))
        monkeypatch.setattr(governance, "DeletionExecutor", lambda *args: SimpleNamespace(run_once=runners[0]))
        monkeypatch.setattr(worker, "CorrectionWorker", lambda *args: SimpleNamespace(run_once=runners[1]))
        monkeypatch.setattr(learning_jobs, "LearningWorker", lambda *args, **kwargs: SimpleNamespace(run_once=runners[2]))
        monkeypatch.setattr(worker.sys, "argv", ["worker-test"] + (["--once"] if once else []))
        return engine
    return install


@pytest.mark.parametrize("failed_index", [0, 1, 2], ids=["first", "middle", "last"])
def test_once_runs_all_executors_without_sleeping(stub_main, monkeypatch, failed_index):
    runners = [Mock(return_value=True) for _ in range(3)]
    runners[failed_index].side_effect = RuntimeError()
    engine = stub_main(runners, once=True)
    sleep = Mock(side_effect=AssertionError("once must not sleep"))
    monkeypatch.setattr(worker.time, "sleep", sleep)
    worker.main()
    assert [runner.call_count for runner in runners] == [1, 1, 1]
    sleep.assert_not_called()
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("exit_type", [KeyboardInterrupt, SystemExit])
def test_main_backs_off_persistent_failures_and_disposes_on_exit(stub_main, clock, monkeypatch, exit_type):
    runners = [Mock(side_effect=RuntimeError()) for _ in range(3)]
    engine = stub_main(runners)
    sleeps = []
    stopped = exit_type(17)

    def sleep(delay):
        sleeps.append(delay)
        clock.now += delay
        if len(sleeps) == 8:
            raise stopped
        assert len(sleeps) < 8

    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(exit_type) as caught:
        worker.main()
    assert caught.value is stopped
    assert sleeps == [1, 2, 4, 8, 16, 30, 30, 30]
    assert [runner.call_count for runner in runners] == [8, 8, 8]
    engine.dispose.assert_called_once_with()


def test_main_idle_polling_continues_and_executor_exit_disposes(stub_main, monkeypatch):
    stopped = SystemExit(23)
    runners = [Mock(return_value=False) for _ in range(3)]
    runners[1].side_effect = [False, stopped]
    engine = stub_main(runners)
    sleep = Mock()
    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(SystemExit) as caught:
        worker.main()
    assert caught.value is stopped
    assert [runner.call_count for runner in runners] == [2, 2, 1]
    sleep.assert_called_once_with(1)
    engine.dispose.assert_called_once_with()


def test_finish_failure_keeps_lease_and_unknown_recheck_is_never_replayed(recovery_flow, clock, monkeypatch):
    flow, case, control = recovery_flow
    response = recheck(flow, case)
    assert response.status_code == 202
    identifier = response.json()["job"]["id"]
    correction = Mock(wraps=flow.worker.run_once)
    healthy = Mock(return_value=True)
    loop = RoundRunner(lambda: False, correction, healthy)
    failure = OperationalError(PRIVATE_SQL, PRIVATE_PARAMS, sqlite3.OperationalError("private"))
    with monkeypatch.context() as patch:
        finish = Mock(side_effect=failure)
        patch.setattr(flow.worker, "finish", finish)
        assert loop.run_round() is True
        assert len(control.calls) == 1
        with Session(flow.database.engine) as session:
            task = session.get(Task, identifier)
            token, expiry = task.lease_token, task.lease_expires_at
            assert token and expiry and task.status == "running" and task.tries == 1
            assert task.audit["recheck_dispatched"] is True
            assert task.audit["executions"][-1]["charge_status"] == "unknown"
        assert loop.run_round() is True
        assert correction.call_count == 1  # Still cooling down.
        clock.now += 1
        assert loop.run_round() is True
        assert correction.call_count == 2  # Fresh poll respects the live lease.
        assert len(control.calls) == 1
        assert finish.call_count == 2  # Result write and failure write, no new inference.
        with Session(flow.database.engine) as session:
            task = session.get(Task, identifier)
            assert (task.status, task.tries, task.lease_token, task.lease_expires_at) == (
                "running", 1, token, expiry,
            )

    with Session(flow.database.engine) as session, session.begin():
        session.get(Task, identifier).lease_expires_at = now() - timedelta(seconds=1)
    assert loop.run_round() is True
    assert len(control.calls) == 1
    assert not flow.worker.finish(identifier, token, result=control.last)
    with Session(flow.database.engine) as session:
        task = session.get(Task, identifier)
        assert task.status == "failed" and task.tries == 1
        assert task.audit["recovery_outcome"] == "execution_unknown"
        assert task.audit["executions"][0]["late_return"]["applied"] is False
        assert session.get(Entity, case["current_attempt_id"]).document["recognition_revision"] == 1
    assert healthy.call_count == 4
