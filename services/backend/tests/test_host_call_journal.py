"""Host-only invocation evidence; MockTransport and isolated DB, no paid calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import replace
import json
from threading import Barrier, BoundedSemaphore
import time

import httpx
import pytest
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_partial_output_persistence import make_client
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.correction_models import Task
from xueban_backend.providers.ark import Attempt, CandidateResult, Metadata, ProviderError, Usage
from xueban_backend.providers.host_call_journal import HostCallJournal
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def pipeline(*, enabled=True, first=None, second=None, **kwargs):
    return TwoModelWholePagePipeline(
        FakeClient("synthetic-primary", {"items": [correct()]} if first is None else first),
        FakeClient("synthetic-secondary", {"items": [correct()]} if second is None else second),
        host_call_journal_enabled=enabled, **kwargs)


def run(p, images=None):
    journal = p.create_host_call_journal()
    result = p.recognize(images=images or (picture(),), host_call_journal=journal)
    return result, journal


def assert_private(journal):
    assert journal["wire_verified"] is False and journal["scope_admission_eligible"] is False
    assert journal["charge_status"] == "unknown" and journal["evidence_level"] == "host_call_metadata"


def test_default_off_is_request_and_candidate_identical_to_enabled_host_observation():
    a, b = pipeline(enabled=False), pipeline()
    off, _ = run(a); on, journal = run(b)
    assert off.host_call_journal is None and off.data == on.data
    assert a.client.calls == b.client.calls and a.review_client.calls == b.review_client.calls
    assert off.metadata.attempts == on.metadata.attempts and off.metadata.usage == on.metadata.usage
    doc = on.host_call_journal.document()
    assert doc == journal.snapshot().document(); assert_private(doc)
    assert doc["run_status"] == "returned" and len(doc["calls"]) == 2
    for call in doc["calls"]:
        assert call["target_scope"] == "whole_page" and call["requested_target_count"] is None
        assert call["attempt_count"] == 1 and call["metadata"]["usage"]["total_tokens"] == 30
        assert call["outcome"] == "returned"
    assert "host_call_journal" not in repr(on)
    doc["calls"].clear()
    assert len(on.host_call_journal.document()["calls"]) == 2


@pytest.mark.parametrize("value", [None, 0, 1, "1", [], {}])
def test_constructor_flag_is_strict_bool(value):
    with pytest.raises(ProviderError, match="invalid_configuration"): pipeline(enabled=value)


@pytest.mark.parametrize("value", ["true", "false", "", "2", 1, True])
def test_environment_flag_rejects_invalid_values_before_clients(value):
    with pytest.raises(ProviderError, match="invalid_configuration"):
        TwoModelWholePagePipeline.from_env({"HOST_CALL_JOURNAL_ENABLED": value})


def test_cross_page_and_concurrent_invocations_are_isolated():
    p = pipeline(); barrier = Barrier(4)
    p.client.barrier = p.review_client.barrier = barrier
    images = [replace(picture(), asset_id="asset_first"), replace(picture(), asset_id="asset_second")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, p, (image,)) for image in images]
        pairs = [future.result(timeout=5) for future in futures]
    docs = [pair[0].host_call_journal.document() for pair in pairs]
    assert len({doc["invocation_id"] for doc in docs}) == 2
    assert [{c["asset_id"] for c in d["calls"]} for d in docs] == [{im.asset_id} for im in images]
    assert len({call["call_id"] for doc in docs for call in doc["calls"]}) == 4
    assert not hasattr(p, "_host_call_journal")
    p.client.barrier = p.review_client.barrier = None
    result, _ = run(p, tuple(images))
    assert len(result.host_call_journal.document()["calls"]) == len(result.metadata.attempts) == 4


def test_all_provider_failures_keep_metadata_without_claiming_zero_charge():
    error = ProviderError("upstream_timeout", attempts=(Attempt(1, None, "upstream_timeout", 7),))
    p = pipeline(first=error, second=error); journal = p.create_host_call_journal()
    with pytest.raises(ProviderError, match="multimodel_unavailable"):
        p.recognize(images=(picture(),), host_call_journal=journal)
    doc = journal.snapshot().document()
    assert doc["run_status"] == "failed" and len(doc["calls"]) == 2
    assert all(r["outcome"] == "failed" and r["attempt_count"] == 1 for r in doc["calls"])
    assert all(r["metadata"]["usage"] is None for r in doc["calls"]); assert_private(doc)


def test_unknown_exception_and_cancellation_remain_interrupted_with_unknown_usage():
    class Stop(BaseException): pass
    p = pipeline()
    def fail(**kwargs): raise Stop("private-exception-sentinel")
    p.client.generate = fail; journal = p.create_host_call_journal()
    with pytest.raises(Stop): p.recognize(images=(picture(),), host_call_journal=journal)
    doc = journal.snapshot().document()
    assert doc["run_status"] == "failed"
    [call] = [r for r in doc["calls"] if r["stage"] == "primary"]
    assert call["outcome"] == "interrupted" and call["metadata"] is None and call["attempt_count"] is None
    assert "private-exception-sentinel" not in json.dumps(doc)


def test_four_boundaries_capture_requested_targets_and_skip_has_no_invocation():
    p = pipeline(reconciliation_client=FakeClient("synthetic-review", {"items": []}),
                 visual_client=FakeClient("synthetic-visual", {"items": []}))
    local = copy(p); journal = HostCallJournal(); journal.start(); local._host_call_journal = journal
    image = picture(); local._read_and_judge(p.client, image, "primary")
    target = {"label": "private-label-sentinel", "reason_codes": [], "primary": correct(), "secondary": correct()}
    local._review_conflicts(image, [target]); local._read_visual_once(image, ["private-label-sentinel"])
    local._align_page(image, {"1": correct()}, {"2": correct("2")})
    before = journal.snapshot().document()
    skipped = local._bounded_review(image, [target], BoundedSemaphore(2), time.monotonic() - 1)
    assert skipped["metadata"].attempts == () and journal.snapshot().document() == before
    journal.close("returned"); calls = journal.snapshot().document()["calls"]
    assert [c["stage"] for c in calls] == ["primary", "reconciliation", "visual_readback", "semantic_alignment"]
    assert [c["requested_target_count"] for c in calls] == [None, 1, 1, 2]
    assert len({c["input_text_sha256"] for c in calls}) == 4
    assert "private-label-sentinel" not in json.dumps(calls) and image.data_url not in json.dumps(calls)


@pytest.mark.parametrize("location", ["metadata", "candidate", "exception"])
def test_cross_provider_echo_never_enters_journal(location):
    p = pipeline(); secret = p.review_client.settings.api_key
    metadata = Metadata(p.client.settings.model, p.client.settings.model, None, None, Usage(2, 3, 5), 10,
                        (Attempt(1, 200, "ok", 10, usage=Usage(2, 3, 5)),))
    def generate(**kwargs):
        if location == "exception":
            raise ProviderError("invalid_output", attempts=(Attempt(1, 200, "invalid_output", 10, request_id=secret),))
        data = {"items": [correct(answer_text=secret)]} if location == "candidate" else {"items": [correct()]}
        return CandidateResult("check", data, replace(metadata, request_id=secret) if location == "metadata" else metadata)
    p.client.generate = generate; result, journal = run(p)
    serialized = json.dumps(journal.snapshot().document())
    assert secret not in serialized and "provider_credential_echo" in serialized
    assert result.data["items"][0]["recognition_status"] == "needs_revision"


def test_same_collector_cannot_cross_recognize_invocations():
    p = pipeline(); _, journal = run(p)
    with pytest.raises(ProviderError, match="invalid_configuration"):
        p.recognize(images=(picture(),), host_call_journal=journal)
    assert len(p.client.calls) == len(p.review_client.calls) == 1


@pytest.mark.parametrize("failed", [False, True])
def test_worker_records_own_journal_outside_candidate_even_on_whole_page_failure(flow, failed):
    a, first = make_client([correct()], "synthetic-one"); b, second = make_client([correct()], "synthetic-two")
    if failed:
        a._async_transport = b._async_transport = httpx.MockTransport(lambda request: httpx.Response(500))
    p = TwoModelWholePagePipeline(a, b, host_call_journal_enabled=True); flow.worker.pipeline = p
    try:
        _, job = flow.recognize(); assert flow.worker.run_once()
        with Session(flow.database.engine) as session:
            task = session.get(Task, job["id"]); execution = task.audit["executions"][-1]
            doc = execution["host_call_journal"]
            assert len(doc["calls"]) == 2 and doc["run_status"] == ("failed" if failed else "returned")
            assert "host_call_journal" not in execution.get("candidate", {})
            assert "scope_recovery_host_pages" not in task.audit
            assert len(execution.get("attempts", execution.get("metadata", {}).get("attempts", []))) == 2
        assert "host_call_journal" not in json.dumps(flow.client.get("/v1/jobs/" + job["id"]).json())
    finally: p.close()


def test_worker_ignores_provider_candidate_forged_journal(flow):
    original = flow.pipeline.recognize
    def spoof(**kwargs):
        result = original(**kwargs)
        result.data["host_call_journal"] = {"wire_verified": True, "scope_admission_eligible": True}
        return result
    flow.pipeline.recognize = spoof
    _, job = flow.recognize(); assert flow.worker.run_once()
    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        assert "host_call_journal" not in task.audit["executions"][-1] and "scope_recovery_host_pages" not in task.audit


def test_late_worker_cannot_save_its_journal_over_current_execution(flow):
    _, job = flow.recognize(); task_id, token = flow.worker.claim()
    p = pipeline(); result, journal = run(p)
    with Session(flow.database.engine) as session, session.begin():
        task = session.get(Task, task_id); task.lease_token = "lease_another_owner"; saved = deepcopy(task.audit)
    assert flow.worker.finish(task_id, token, result=result, host_call_journal=journal.snapshot()) is False
    with Session(flow.database.engine) as session: assert session.get(Task, task_id).audit == saved


def test_actual_mock_http_request_bytes_unchanged_and_no_journal_images_or_raw_answers():
    def execute(enabled):
        a, first = make_client([correct(answer_text="private-handwriting-sentinel")], "same-primary")
        b, second = make_client([correct(answer_text="private-handwriting-sentinel")], "same-secondary")
        p = TwoModelWholePagePipeline(a, b, host_call_journal_enabled=enabled)
        try:
            result, journal = run(p)
            return result, journal, [r.content for r in [*first, *second]]
        finally: p.close()
    off, _, bodies_off = execute(False); on, journal, bodies_on = execute(True)
    assert bodies_off == bodies_on and len(bodies_on) == 2 and off.data == on.data
    doc = json.dumps(journal.snapshot().document())
    assert "private-handwriting-sentinel" not in doc and picture().data_url not in doc
    assert "same-primary" in doc and on.metadata.usage == off.metadata.usage


@pytest.mark.parametrize("value,enabled", [(None, False), ("0", False), ("1", True)])
def test_environment_default_off_and_opt_in(value, enabled):
    env = {"ARK_BASE_URL": "https://synthetic.invalid", "ARK_API_KEY": "synthetic-key"}
    if value is not None: env["HOST_CALL_JOURNAL_ENABLED"] = value
    p = TwoModelWholePagePipeline.from_env(env)
    try: assert (p.create_host_call_journal() is not None) is enabled
    finally: p.close()


def test_two_actual_tasks_run_simultaneously_and_save_only_their_own_journal(flow):
    assets_jobs = [flow.recognize(), flow.recognize()]
    claims = [flow.worker.claim(), flow.worker.claim()]
    assert len({claim[0] for claim in claims}) == 2
    p = pipeline(); p.client.barrier = p.review_client.barrier = Barrier(4)
    flow.worker.pipeline = p
    journals = [p.create_host_call_journal(), p.create_host_call_journal()]
    contexts = [flow.worker._load(identifier, token)[1] for identifier, token in claims]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(p.recognize, **context, host_call_journal=journal)
                   for context, journal in zip(contexts, journals)]
        results = [future.result(timeout=5) for future in pending]
    for (identifier, token), result, journal in zip(claims, results, journals):
        assert flow.worker.finish(identifier, token, result=result, host_call_journal=journal.snapshot())
    with Session(flow.database.engine) as session:
        docs = [session.get(Task, identifier).audit["executions"][-1]["host_call_journal"] for identifier, _ in claims]
        assert len({doc["invocation_id"] for doc in docs}) == 2
        for (identifier, _), doc in zip(claims, docs):
            assert {call["asset_id"] for call in doc["calls"]} == set(session.get(Task, identifier).inputs["asset_ids"])
            assert len(doc["calls"]) == 2
