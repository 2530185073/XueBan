"""The worker loop must not let a busy executor starve the ones after it."""
from xueban_backend.worker import run_round


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
