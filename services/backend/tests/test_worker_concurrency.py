"""Bounded checks against disposable migrated SQLite and synthetic models only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import socket
from threading import Barrier, BoundedSemaphore, Event, Lock, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_learning import prepare_latest_math_curriculum
from test_multimodal_worker import assessment
from xueban_backend import learning_projection, worker as worker_module, worker_concurrency
from xueban_backend.config import Settings
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Entity, Task
from xueban_backend.learning_models import LearningEvidence, QuestionKnowledgeLink
from xueban_backend.providers import ProviderError
from xueban_backend.worker import RoundRunner
from xueban_backend.worker_concurrency import BoundedCheckRunner


pytestmark = pytest.mark.parametrize("database", ["sqlite"], indirect=True)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Concurrency tests must not load model credentials or use the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(worker_module.CorrectionPipeline, "from_env", forbidden)


def prepare_checks(flow, monkeypatch, count):
    recognize = flow.pipeline.recognize

    def recognize_many(**context):
        result = recognize(**context)
        item = result.data["items"][0]
        result.data["items"] = [dict(deepcopy(item), label=str(index + 1), subject="数学")
                                for index in range(count)]
        return result

    monkeypatch.setattr(flow.pipeline, "recognize", recognize_many)
    flow.worker.settings = replace(flow.worker.settings, auto_check_after_recognition=True)
    flow.recognize()
    assert flow.worker.run_once()
    with Session(flow.database.engine, expire_on_commit=False) as session:
        return list(session.scalars(select(Task).where(Task.kind == "check")
                                    .order_by(Task.available_at, Task.id)))


def statuses(flow):
    with Session(flow.database.engine) as session:
        return {task.id: task.status for task in session.scalars(select(Task).where(Task.kind == "check"))}


def assert_once(flow, count):
    with Session(flow.database.engine) as session:
        tasks = session.scalars(select(Task).where(Task.kind == "check")).all()
        assert len(tasks) == count
        assert all(task.status == "succeeded" and task.tries == 1 for task in tasks)
        assert all(len(task.audit["executions"]) == 1 for task in tasks)
        assert all(task.lease_token is task.lease_expires_at is None for task in tasks)
        decisions = session.scalars(select(Entity).where(Entity.kind == "decision")).all()
        assert len(decisions) == count
        assert len({row.document["case_id"] for row in decisions}) == count


@pytest.mark.parametrize("concurrency", [2, 3])
def test_bounded_overlap_incremental_commit_and_serial_projection(flow, monkeypatch, concurrency):
    prepare_latest_math_curriculum(flow)
    tasks = prepare_checks(flow, monkeypatch, 5)
    original_order = flow.homework()["case_ids"]
    check, finish = flow.pipeline.check, flow.worker.finish
    releases = {task.inputs["input_versions"]["question_id"]: Event() for task in tasks}
    started = {task.inputs["input_versions"]["question_id"]: Event() for task in tasks}
    completed = {task.id: Event() for task in tasks}
    all_started, guard = Event(), Lock()
    state = {"active": 0, "peak": 0, "started": []}
    owners = {}

    def session_owner(session, transaction, connection):
        if connection.engine is flow.database.engine:
            assert owners.setdefault(session, get_ident()) == get_ident()

    def blocked_check(**context):
        question_id = context["input_versions"]["question_id"]
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            state["started"].append(question_id)
            started[question_id].set()
            if len(state["started"]) == concurrency:
                all_started.set()
        try:
            assert releases[question_id].wait(8)
            result = check(**context)
            return replace(result, data={**result.data, "knowledge_points": ["有理数的加减法"],
                                        "verification": assessment()["verification"], "uncertainties": []})
        finally:
            with guard:
                state["active"] -= 1

    def record_finish(identifier, token, **kwargs):
        result = finish(identifier, token, **kwargs)
        completed[identifier].set()
        return result

    monkeypatch.setattr(flow.pipeline, "check", blocked_check)
    monkeypatch.setattr(flow.worker, "finish", record_finish)
    monkeypatch.setattr(learning_projection, "PERIODIC", [])
    monkeypatch.setattr(learning_projection, "PROJECTION_FOLLOWUPS", [])
    learner = learning_projection.LearningWorker(flow.database.engine)
    peer_calls = []

    def learning():
        assert state["active"] == 0
        peer_calls.append("learning")
        return learner.run_once()

    def deletion():
        assert state["active"] == 0
        peer_calls.append("deletion")
        return False

    runner = BoundedCheckRunner(flow.worker, concurrency, max_checks_per_round=4)
    loop = RoundRunner(deletion, runner.run_once, learning)
    event.listen(Session, "after_begin", session_owner)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(loop.run_round)
            try:
                assert all_started.wait(8)
                assert peer_calls == ["deletion"]
                current = statuses(flow)
                assert [current[t.id] for t in tasks] == ["running"] * concurrency + ["queued"] * (5 - concurrency)
                with Session(flow.database.engine) as session:
                    active = session.get(Task, tasks[0].id)
                    token = active.lease_token
                assert flow.worker.renew_lease(tasks[0].id, token)
                # A later question commits while the first remains blocked.
                releases[tasks[1].inputs["input_versions"]["question_id"]].set()
                assert completed[tasks[1].id].wait(8)
                assert statuses(flow)[tasks[1].id] == "succeeded"
                assert statuses(flow)[tasks[0].id] == "running"
                assert started[tasks[concurrency].inputs["input_versions"]["question_id"]].wait(8)
                assert statuses(flow)[tasks[0].id] == "running"
                assert len(state["started"]) == concurrency + 1
                if concurrency == 2:
                    # The same free slot can roll again while the first call
                    # is still running, up to the turn's admission budget.
                    releases[tasks[2].inputs["input_versions"]["question_id"]].set()
                    assert started[tasks[3].inputs["input_versions"]["question_id"]].wait(8)
                assert statuses(flow)[tasks[4].id] == "queued"
                assert peer_calls == ["deletion"]
            finally:
                for release in releases.values():
                    release.set()
            assert future.result(timeout=8)
        assert peer_calls == ["deletion", "learning"]
        assert [statuses(flow)[task.id] for task in tasks] == ["succeeded"] * 4 + ["queued"]
        assert loop.run_round()
        assert state["peak"] == concurrency and len(set(state["started"])) == 5
        assert_once(flow, 5)
        assert flow.homework()["case_ids"] == original_order
        with Session(flow.database.engine) as session:
            evidence = session.scalars(select(LearningEvidence)).all()
            links = session.scalars(select(QuestionKnowledgeLink)).all()
            assert len(evidence) == len(links) == 5
            assert len({row.source_key for row in evidence}) == 5
        assert not runner.run_once()
        assert state["started"] and len(state["started"]) == 5
        assert len({owner for owner in owners.values()}) > 1
    finally:
        event.remove(Session, "after_begin", session_owner)


@pytest.mark.parametrize("failure", ["provider", "stale", "expired", "replaced_token", "writeback"])
def test_parallel_failure_keeps_fences_and_does_not_repeat_calls(flow, monkeypatch, caplog, failure):
    tasks = prepare_checks(flow, monkeypatch, 2)
    check, finish = flow.pipeline.check, flow.worker.finish
    barrier, release = Barrier(3), Event()
    first_question = tasks[0].inputs["input_versions"]["question_id"]
    calls, old_token = [], None

    def blocked_check(**context):
        calls.append(context["input_versions"]["question_id"])
        barrier.wait(timeout=8)
        assert release.wait(8)
        if context["input_versions"]["question_id"] == first_question and failure == "provider":
            raise ProviderError("timeout")
        return check(**context)

    def fail_writeback(identifier, token, **kwargs):
        if identifier == tasks[0].id:
            raise RuntimeError("private model credential")
        return finish(identifier, token, **kwargs)

    monkeypatch.setattr(flow.pipeline, "check", blocked_check)
    if failure == "writeback":
        monkeypatch.setattr(flow.worker, "finish", fail_writeback)
    # Keep this fence test to two admissions: an explicitly expired lease is
    # eligible for a new durable claim, which is exercised below in a new turn.
    runner = BoundedCheckRunner(flow.worker, 2, max_checks_per_round=2)
    loop = RoundRunner(runner.run_once)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(loop.run_round)
        try:
            barrier.wait(timeout=8)
            with Session(flow.database.engine) as session, session.begin():
                task = session.get(Task, tasks[0].id)
                old_token = task.lease_token
                if failure == "stale":
                    question = session.get(Entity, first_question)
                    question.revision += 1
                    question.document = {**question.document, "revision": question.revision}
                elif failure == "expired":
                    task.lease_expires_at = now() - timedelta(seconds=1)
                elif failure == "replaced_token":
                    task.lease_token = "lease_synthetic_other_owner"
        finally:
            release.set()
        assert future.result(timeout=8) is (failure != "writeback")
    assert sorted(calls) == sorted(task.inputs["input_versions"]["question_id"] for task in tasks)
    assert "private model credential" not in caplog.text
    current = statuses(flow)
    assert current[tasks[1].id] == "succeeded"
    assert current[tasks[0].id] == {
        "provider": "failed", "stale": "superseded", "expired": "running",
        "replaced_token": "running", "writeback": "running",
    }[failure]
    if failure != "writeback":
        assert not flow.worker.finish(tasks[0].id, old_token)
    with Session(flow.database.engine) as session:
        assert len(session.scalars(select(Entity).where(Entity.kind == "decision")).all()) == 1
        assert session.get(Task, tasks[0].id).tries == 1
    if failure in {"expired", "writeback"}:
        # Only a fresh durable claim after expiry may recover the execution.
        monkeypatch.setattr(flow.pipeline, "check", check)
        monkeypatch.setattr(flow.worker, "finish", finish)
        with Session(flow.database.engine) as session, session.begin():
            session.get(Task, tasks[0].id).lease_expires_at = now() - timedelta(seconds=1)
        assert runner.run_once()
        assert not runner.run_once()
        with Session(flow.database.engine) as session:
            task = session.get(Task, tasks[0].id)
            assert task.status == "succeeded" and task.tries == 2
            assert len(task.audit["executions"]) == 2
            assert len(session.scalars(select(Entity).where(Entity.kind == "decision")).all()) == 2


def test_recognition_runs_alone_on_original_thread_then_bounded_checks(flow, monkeypatch):
    recognize = flow.pipeline.recognize
    threads = []

    def recognize_on_caller(**context):
        threads.append(get_ident())
        return recognize(**context)

    monkeypatch.setattr(flow.pipeline, "recognize", recognize_on_caller)
    flow.worker.settings = replace(flow.worker.settings, auto_check_after_recognition=True)
    flow.recognize()
    runner = BoundedCheckRunner(flow.worker, 3)
    assert runner.run_once()
    assert threads == [get_ident()] and flow.pipeline.calls == ["recognize"]
    assert list(statuses(flow).values()) == ["queued"]
    assert runner.run_once()
    assert_once(flow, 1)


def test_claim_failure_joins_started_task_and_leaves_other_task_unclaimed(flow, monkeypatch, caplog):
    tasks = prepare_checks(flow, monkeypatch, 2)
    claim = flow.worker.claim

    def fail_second_claim(*, checks_only=False):
        if checks_only:
            raise RuntimeError("private claim parameters")
        return claim()

    monkeypatch.setattr(flow.worker, "claim", fail_second_claim)
    runner = BoundedCheckRunner(flow.worker, 3)
    assert not RoundRunner(runner.run_once).run_round()
    assert statuses(flow) == {tasks[0].id: "succeeded", tasks[1].id: "queued"}
    assert flow.pipeline.calls == ["recognize", "check"]
    assert "private claim parameters" not in caplog.text
    monkeypatch.setattr(flow.worker, "claim", claim)
    assert runner.run_once()
    assert_once(flow, 2)


def test_default_three_respects_two_call_clients_without_semaphore_rejection(flow, monkeypatch):
    tasks = prepare_checks(flow, monkeypatch, 3)
    check = flow.pipeline.check
    slots, simultaneous = BoundedSemaphore(2), Barrier(2)
    calls = []
    flow.pipeline.client = SimpleNamespace(settings=SimpleNamespace(max_concurrency=2))
    flow.pipeline.fallback_client = SimpleNamespace(settings=SimpleNamespace(max_concurrency=2))

    def limited_check(**context):
        if not slots.acquire(blocking=False):
            raise ProviderError("concurrency_limit")
        try:
            calls.append(context["input_versions"]["question_id"])
            if len(calls) <= 2:
                simultaneous.wait(timeout=8)
            return check(**context)
        finally:
            slots.release()

    monkeypatch.setattr(flow.pipeline, "check", limited_check)
    runner = BoundedCheckRunner(flow.worker, 3)
    assert runner.concurrency == 2
    assert runner.run_once()
    assert len(calls) == 3
    assert_once(flow, 3)
    assert not runner.run_once()


@pytest.mark.parametrize("budget", [1, 2, 8])
def test_continuous_backlog_yields_to_peers_with_only_free_slots_claimed(database, monkeypatch, budget):
    monkeypatch.setattr(worker_concurrency, "monotonic", lambda: 100.0)
    guard, reserved, claimed, executed, peers = Lock(), set(), [], [], []
    caller = get_ident()

    def claim(*, checks_only=False):
        assert get_ident() == caller
        assert checks_only is (len(claimed) % budget != 0)
        with guard:
            # Includes claims whose calls have not started yet, so a pool
            # backlog would fail even if its active thread count is bounded.
            assert len(reserved) < 2
            identifier = len(claimed)
            claimed.append(identifier)
            reserved.add(identifier)
        return identifier, "synthetic-lease"

    def run_claimed(identifier, token):
        assert get_ident() != caller
        assert token == "synthetic-lease"
        with guard:
            executed.append(identifier)
            reserved.remove(identifier)
        return True

    def peer(name):
        assert not reserved
        peers.append((name, len(executed)))
        return False

    worker = SimpleNamespace(engine=database.engine, claim=claim, run_claimed=run_claimed,
                             is_parallel_check=Mock(return_value=True),
                             pipeline=SimpleNamespace(client=SimpleNamespace(
                                 settings=SimpleNamespace(max_concurrency=2))))
    # Exercise the actual default as well as budgets smaller than pool size.
    options = {} if budget == 8 else {"max_checks_per_round": budget}
    runner = BoundedCheckRunner(worker, 8, **options)
    loop = RoundRunner(lambda: peer("deletion"), runner.run_once, lambda: peer("learning"))
    for turn in range(3):
        assert loop.run_round()
        assert len(claimed) == len(executed) == (turn + 1) * budget
    assert sorted(executed) == claimed == list(range(3 * budget))
    assert peers == [(name, count) for turn in range(3) for name, count in (
        ("deletion", turn * budget), ("learning", (turn + 1) * budget))]


def test_deadline_stops_refill_but_joins_started_calls_and_resets_next_turn(database, monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(worker_concurrency, "monotonic", lambda: clock.now)
    slow_started, release, slow_finished, draining = Event(), Event(), Event(), Event()
    wait = worker_concurrency.wait
    executed = []

    def observed_wait(*args, **kwargs):
        if clock.now >= 130.0:
            draining.set()
        return wait(*args, **kwargs)

    def run_claimed(identifier, token):
        if identifier == 0:
            slow_started.set()
            assert release.wait(8)
            slow_finished.set()
        elif identifier == 1:
            assert slow_started.wait(8)
            clock.now = 130.0  # Exactly the default admission deadline.
        executed.append(identifier)
        return True

    monkeypatch.setattr(worker_concurrency, "wait", observed_wait)
    claim = Mock(side_effect=[(index, "synthetic-lease") for index in range(4)] + [None])
    worker = SimpleNamespace(engine=database.engine, claim=claim, run_claimed=run_claimed,
                             is_parallel_check=Mock(return_value=True))
    runner = BoundedCheckRunner(worker, 2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_once)
        try:
            assert draining.wait(8)
            assert claim.call_count == 2
            assert not slow_finished.is_set() and not future.done()
        finally:
            release.set()
        assert future.result(timeout=8)
    assert sorted(executed) == [0, 1]
    assert runner.run_once()  # A fresh deadline admits the remaining checks.
    assert sorted(executed) == [0, 1, 2, 3]
    assert claim.call_count == 5


@pytest.mark.parametrize("expiring_claim", [1, 2])
def test_claim_time_counts_toward_deadline_without_abandoning_committed_claim(database, monkeypatch, expiring_claim):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(worker_concurrency, "monotonic", lambda: clock.now)
    claims = []

    def claim(*, checks_only=False):
        claims.append((len(claims), "synthetic-lease"))
        if len(claims) == expiring_claim:
            clock.now = 131.0
        return claims[-1]

    execute = Mock(return_value=True)
    worker = SimpleNamespace(engine=database.engine, claim=claim, run_claimed=execute,
                             is_parallel_check=Mock(return_value=True))
    assert BoundedCheckRunner(worker, 2).run_once()
    assert len(claims) == execute.call_count == expiring_claim
    assert {call.args for call in execute.call_args_list} == set(claims)


@pytest.mark.parametrize("failure,error_type", [
    ("claim", RuntimeError), ("submit", RuntimeError), ("result", RuntimeError),
    ("result", KeyboardInterrupt),
])
def test_failure_during_rolling_turn_joins_all_started_calls_before_propagating(database, monkeypatch, failure, error_type):
    slow_started, release, cleanup_started, slow_finished = Event(), Event(), Event(), Event()
    claimed, executed, finished = [], [], []
    error = error_type("synthetic scheduler failure")

    def claim(*, checks_only=False):
        identifier = len(claimed)
        if failure == "claim" and identifier == 2:
            raise error
        claimed.append(identifier)
        return identifier, "synthetic-lease"

    def run_claimed(identifier, token):
        executed.append(identifier)
        try:
            if identifier == 0:
                slow_started.set()
                assert release.wait(8)
                slow_finished.set()
            else:
                assert slow_started.wait(8)
                if failure == "result":
                    raise error
            return True
        finally:
            finished.append(identifier)

    class ObservedPool(ThreadPoolExecutor):
        def submit(self, function, *args, **kwargs):
            if failure == "submit" and args[0] == 2:
                raise error
            return super().submit(function, *args, **kwargs)

        def shutdown(self, wait=True, *, cancel_futures=False):
            cleanup_started.set()
            return super().shutdown(wait=wait, cancel_futures=cancel_futures)

    monkeypatch.setattr(worker_concurrency, "ThreadPoolExecutor", ObservedPool)
    worker = SimpleNamespace(engine=database.engine, claim=claim, run_claimed=run_claimed,
                             is_parallel_check=Mock(return_value=True))
    runner = BoundedCheckRunner(worker, 2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_once)
        try:
            assert slow_started.wait(8)
            assert cleanup_started.wait(8)
            assert not future.done() and not slow_finished.is_set()
            assert sorted(executed) == [0, 1]
            assert claimed == ([0, 1, 2] if failure == "submit" else [0, 1])
        finally:
            release.set()
        with pytest.raises(error_type) as caught:
            future.result(timeout=8)
        assert caught.value is error
    assert sorted(finished) == [0, 1] and slow_finished.is_set()


def test_empty_queue_is_not_polled_again_after_started_call_finishes(database, monkeypatch):
    empty_seen = Event()
    claims = []

    def claim(*, checks_only=False):
        claims.append(checks_only)
        if checks_only:
            empty_seen.set()
            return None
        return "check", "synthetic-lease"

    def run_claimed(*claim):
        assert empty_seen.wait(8)
        return True

    worker = SimpleNamespace(engine=database.engine, claim=claim, run_claimed=run_claimed,
                             is_parallel_check=Mock(return_value=True))
    assert BoundedCheckRunner(worker, 2).run_once()
    assert claims == [False, True]


@pytest.mark.parametrize("options", [
    {"max_checks_per_round": value} for value in (0, -1, True, 2.5, "8")
] + [
    {"max_round_seconds": value} for value in (0, -1, True, "30", float("inf"), float("nan"))
])
def test_invalid_round_budgets_fail_before_claiming(database, options):
    worker = SimpleNamespace(engine=database.engine, claim=Mock())
    with pytest.raises(ValueError, match=next(iter(options))):
        BoundedCheckRunner(worker, 2, **options)
    worker.claim.assert_not_called()


@pytest.mark.parametrize("primary,fallback,requested,expected", [
    (4, 2, 3, 2), (2, 4, 3, 2), (2, 2, 1, 1), (2, 2, 8, 2), (4, 4, 3, 3),
])
def test_effective_concurrency_respects_each_client(database, primary, fallback, requested, expected):
    pipeline = SimpleNamespace(client=SimpleNamespace(settings=SimpleNamespace(max_concurrency=primary)),
                               fallback_client=SimpleNamespace(settings=SimpleNamespace(max_concurrency=fallback)))
    runner = BoundedCheckRunner(SimpleNamespace(engine=database.engine, pipeline=pipeline), requested)
    assert runner.concurrency == expected


@pytest.mark.parametrize("once", [False, True])
def test_production_entrypoint_uses_configured_pool_and_closes_after_join(flow, monkeypatch, once):
    from xueban_backend import governance, learning_jobs

    tasks = prepare_checks(flow, monkeypatch, 2)
    check = flow.pipeline.check
    barrier = Barrier(2)
    completed, closed, threads = [], [], []
    expected = 1 if once else 2

    def simultaneous_check(**context):
        threads.append(get_ident())
        if not once:
            barrier.wait(timeout=8)  # A serial launcher cannot pass this barrier.
        result = check(**context)
        completed.append(context["input_versions"]["question_id"])
        return result

    @contextmanager
    def pipeline_context():
        flow.pipeline.client = None
        try:
            yield flow.pipeline
        finally:
            assert len(completed) == expected
            closed.append("pipeline")

    def end_idle_loop(*args):
        assert not once, "--once must not sleep"
        raise SystemExit(0)

    monkeypatch.setattr(flow.pipeline, "check", simultaneous_check)
    monkeypatch.setattr(worker_module.Settings, "from_environment", lambda: replace(flow.cfg, worker_check_concurrency=2))
    monkeypatch.setattr(worker_module, "create_database_engine", lambda *args, **kwargs: flow.database.engine)
    monkeypatch.setattr(worker_module.CorrectionPipeline, "from_env", pipeline_context)
    monkeypatch.setattr(governance, "DeletionExecutor", lambda *args: SimpleNamespace(run_once=lambda: False))
    monkeypatch.setattr(learning_jobs, "LearningWorker", lambda *args, **kwargs: SimpleNamespace(run_once=lambda: False))
    monkeypatch.setattr(worker_module.sys, "argv", ["synthetic-worker"] + (["--once"] if once else []))
    monkeypatch.setattr(worker_module.time, "sleep", end_idle_loop)
    if once:
        worker_module.main()
        assert threads == [get_ident()]
        assert [statuses(flow)[task.id] for task in tasks] == ["succeeded", "queued"]
    else:
        with pytest.raises(SystemExit):
            worker_module.main()
        assert_once(flow, 2)
    assert len(completed) == expected
    assert closed == ["pipeline"]


@pytest.mark.parametrize("concurrency", [1, 2, 3, 8])
def test_configured_bounds_and_environment(database, monkeypatch, concurrency):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("WORKER_CHECK_CONCURRENCY", str(concurrency))
    settings = Settings.from_environment()
    assert settings.worker_check_concurrency == concurrency
    runner = BoundedCheckRunner(SimpleNamespace(engine=database.engine), concurrency)
    assert runner.concurrency == concurrency


@pytest.mark.parametrize("value", [0, -1, 9, 1000, True, 2.5, "3"])
def test_invalid_bounds_fail_before_claiming(database, value):
    worker = SimpleNamespace(engine=database.engine, claim=Mock())
    with pytest.raises(ValueError, match="WORKER_CHECK_CONCURRENCY"):
        BoundedCheckRunner(worker, value)
    worker.claim.assert_not_called()


def test_in_memory_sqlite_keeps_original_thread(database):
    worker = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name="sqlite"),
                             url=SimpleNamespace(database=":memory:")), run_once=Mock(return_value=True))
    runner = BoundedCheckRunner(worker, 3)
    assert runner.concurrency == 1
    assert runner.run_once()
    worker.run_once.assert_called_once_with()
