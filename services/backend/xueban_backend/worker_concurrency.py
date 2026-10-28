"""Bounded rolling checks; other executors retain their serial round ordering.

No sessions or ORM objects cross thread boundaries. CorrectionWorker owns each
claim, lease, session and result fence; only claim identifiers enter the pool.
There is no pool backlog and no retry at this scheduling layer.

Integration: the existing ``python -m xueban_backend.worker`` entrypoint used by
mac_local.py, mac_service.py and Compose selects this runner automatically.
WORKER_CHECK_CONCURRENCY defaults to 3; accepted integers are 1..8, with 1
restoring serial execution. Set it in the existing launcher's environment
(Mac runtime.json's environment object, or Compose's infra/xueban.env) before
an operator's normal worker restart. No migration or live data change is needed.
``--once`` retains one serial correction turn. Effective concurrency is capped
at the smaller configured primary/fallback client limit (currently 2 for
user_original): their semaphore waits only 1 second before concurrency_limit,
so it must not be used as a queue for additional multi-second model calls.

Limits: concurrency is per process; SQLite still requires one worker process.
Each turn admits at most 8 checks and stops claiming after 30 seconds by default.
Completed calls free slots immediately within those budgets. The time budget
limits admission, not running calls: non-check jobs and peer executors still wait
for every started call to finish. Completion order does not change persisted
reading order. Pool shutdown joins started calls before clients/engine close;
an abrupt process kill still relies on lease recovery and may repeat billing.
No services are started/restarted by this module or its offline tests.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from math import isfinite
from time import monotonic


class BoundedCheckRunner:
    def __init__(self, worker, concurrency=3, *, max_checks_per_round=8, max_round_seconds=30.0):
        if type(concurrency) is not int or not 1 <= concurrency <= 8:
            raise ValueError("WORKER_CHECK_CONCURRENCY must be an integer from 1 to 8")
        if type(max_checks_per_round) is not int or max_checks_per_round < 1:
            raise ValueError("max_checks_per_round must be a positive integer")
        if (type(max_round_seconds) not in (int, float)
                or not isfinite(max_round_seconds) or max_round_seconds <= 0):
            raise ValueError("max_round_seconds must be a positive finite number")
        self.worker = worker
        self.concurrency = concurrency
        self.max_checks_per_round = max_checks_per_round
        self.max_round_seconds = max_round_seconds
        pipeline = getattr(worker, "pipeline", None)
        for name in ("client", "fallback_client"):
            settings = getattr(getattr(pipeline, name, None), "settings", None)
            limit = getattr(settings, "max_concurrency", None)
            if type(limit) is int and limit > 0:
                self.concurrency = min(self.concurrency, limit)
        if self.concurrency > 1 and worker.engine.dialect.name == "sqlite":
            # In-memory SQLite databases may be per connection/thread. Keep
            # existing test/embedded usage on its original thread.
            if worker.engine.url.database in (None, "", ":memory:"):
                self.concurrency = 1

    def run_once(self):
        if self.concurrency == 1:
            return self.worker.run_once()
        deadline = monotonic() + self.max_round_seconds
        claim = self.worker.claim()
        if claim is None:
            return False
        if not self.worker.is_parallel_check(*claim):
            # Recognition/checkpoints, layout repairs, hints and explicit
            # original-image rechecks keep their existing serial semantics.
            return self.worker.run_claimed(*claim)

        # Claim only when a slot is available. Join this bounded turn before
        # learning/deletion runs, so their SQLite writes cannot overlap our
        # result transactions or inline learning projections. Each task saves
        # its result immediately, even if another task in the turn is slow.
        with ThreadPoolExecutor(max_workers=self.concurrency,
                                thread_name_prefix="correction-check") as pool:
            pending = {pool.submit(self.worker.run_claimed, *claim)}
            submitted, accepting = 1, True
            while pending:
                # Check every completed result before claiming a replacement,
                # including calls that finished during the previous submit.
                done = {future for future in pending if future.done()}
                for future in done:
                    future.result()
                pending.difference_update(done)
                if (accepting and len(pending) < self.concurrency
                        and submitted < self.max_checks_per_round and monotonic() < deadline):
                    claim = self.worker.claim(checks_only=True)
                    if claim is None:
                        # Do not poll an empty queue again during this turn.
                        accepting = False
                    else:
                        # A claim may itself use up the time budget. Execute
                        # that committed lease, then stop admitting more work.
                        pending.add(pool.submit(self.worker.run_claimed, *claim))
                        submitted += 1
                    continue
                if pending:
                    wait(pending, return_when=FIRST_COMPLETED)
        # The context manager joins all started calls on every exit, including
        # claim/submit/result failures. Let RoundRunner apply safe backoff;
        # committed leases remain responsible for recovery, never this pool.
        return True
