"""Large pages must not lose every result when one bounded review fails."""
import json
from threading import Lock, BoundedSemaphore
import time

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_two_model_reconciliation import different_stem, pipeline
from test_whole_page_pipeline import picture
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers.two_model import review_batches


def outputs(request):
    targets = json.loads(request["text"])["task_input"]["items"]
    return {"items": [review(label=item["label"]) for item in targets]}


def test_large_page_reviews_are_bounded_and_one_failed_batch_does_not_poison_the_rest():
    first = [correct(str(n)) for n in range(1, 16)]
    second = [different_stem(str(n)) for n in range(1, 16)]

    def respond(request):
        targets = json.loads(request["text"])["task_input"]["items"]
        if any(item["label"] == "7" for item in targets):
            raise ProviderError("incomplete_output")
        return outputs(request)

    flow = pipeline(first, second, respond)
    result = flow.recognize(images=(picture(),))
    rows = {row["label"]: row for row in result.data["items"]}
    assert len(flow.reconciliation_client.calls) == 3
    assert all(rows[str(n)]["assessment"]["verdict"] == "correct" for n in [*range(1, 7), 13, 14, 15])
    assert all(rows[str(n)]["assessment"]["verdict"] == "unable_to_judge" for n in range(7, 13))
    calls = result.data["multimodel_audit"]["calls"]
    batches = sorted((call for call in calls if call["stage"] == "visual_adjudication"),
                     key=lambda call: call["batch_index"])
    assert [call["target_labels"] for call in batches] == [
        [str(n) for n in range(1, 7)], [str(n) for n in range(7, 13)], ["13", "14", "15"]]
    assert [call["code"] for call in batches] == ["ok", "incomplete_output", "ok"]
    # The page-level blind read is shared, not billed/audited once per batch.
    assert len(flow.visual_client.calls) == 1
    assert sum(call["stage"] == "visual_readback" for call in calls) == 1
    assert not any(call["stage"] == "visual_reconciliation" for call in calls)


def test_review_requests_for_one_page_are_concurrent_but_never_exceed_two():
    lock = Lock()
    active = peak = 0

    def respond(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(.03)
            return outputs(request)
        finally:
            with lock:
                active -= 1

    flow = pipeline([correct(str(n)) for n in range(20)],
                    [different_stem(str(n)) for n in range(20)], respond)
    result = flow.recognize(images=(picture(),))
    assert all(row["assessment"]["verdict"] == "correct" for row in result.data["items"])
    assert peak == 2


def test_long_answers_stay_whole_but_reduce_the_batch_size():
    targets = [{"label": str(n), "primary": {"answer_text": "完整步骤" * 600}} for n in range(4)]
    batches = review_batches(targets)
    assert len(batches) == 2
    assert [target for batch in batches for target in batch] == targets
    assert all(target["primary"]["answer_text"] == "完整步骤" * 600 for batch in batches for target in batch)


def test_elapsed_dispatch_budget_makes_no_paid_call_and_releases_the_limiter():
    flow = pipeline([correct()], [different_stem()], outputs)
    limiter = BoundedSemaphore(1)
    result = flow._bounded_review(picture(), [{"label": "1"}], limiter, time.monotonic() - 1)
    assert result["code"] == "review_dispatch_budget_exhausted"
    assert result["metadata"].attempts == ()
    assert not flow.reconciliation_client.calls
    assert limiter.acquire(blocking=False)
    limiter.release()


def test_grading_conflicts_do_not_drag_format_only_targets_into_stronger_review():
    targets = [
        {"label": "1", "reason_codes": ["judgment_disagreement"]},
        {"label": "2", "reason_codes": ["stem_disagreement"]},
        {"label": "3", "reason_codes": ["correction_disagreement"]},
        {"label": "4", "reason_codes": ["answer_disagreement"]},
    ]
    assert [[row["label"] for row in batch] for batch in review_batches(targets)] == [
        ["2", "4"], ["1", "3"]]


def test_only_grading_conflicts_use_configured_judgment_provider_with_original_image():
    from test_whole_page_pipeline import FakeClient
    from xueban_backend.providers.reconciliation import review_target
    flow = pipeline([correct()], [correct()], {"items": [review()]})
    flow.judgment_client = FakeClient("strong-judge", {"items": [review()]})
    target = review_target("1", correct(), correct(), ["judgment_disagreement"])
    result = flow._review_conflicts(picture(), [target])
    assert result["model"] == "strong-judge"
    assert flow.judgment_client.calls[0]["images"] == (picture().data_url,)
    assert not flow.reconciliation_client.calls
    target["reason_codes"] = ["stem_disagreement"]
    assert flow._review_conflicts(picture(), [target])["model"] == "astra-review"
    assert len(flow.judgment_client.calls) == len(flow.reconciliation_client.calls) == 1


def test_stronger_review_failure_does_not_fall_back_to_original_judge():
    from test_whole_page_pipeline import FakeClient
    from xueban_backend.providers.reconciliation import review_target
    flow = pipeline([correct()], [correct()], {"items": [review()]})
    flow.judgment_client = FakeClient("strong-judge", ProviderError("deadline_exceeded"))
    target = review_target("1", correct(), correct(), ["judgment_disagreement"])
    result = flow._review_conflicts(picture(), [target])
    assert result["code"] == "deadline_exceeded"
    assert result["model"] == "strong-judge"
    assert not flow.reconciliation_client.calls
