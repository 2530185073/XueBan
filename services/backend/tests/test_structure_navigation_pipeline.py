"""Offline request counts, coverage and scheduling for the opt-in experiment."""

from copy import deepcopy
import json
from threading import Barrier, Event

import pytest

from test_original_recheck_provider import context
from test_two_model_pipeline import correct, flow
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers import question_units, structure_navigation
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers.two_model import PROMPT_VERSION, TwoModelWholePagePipeline


def navigated(items=None, *, unlocated=None):
    return {"items": [correct()] if items is None else items,
            "unlocated_navigation_labels": [] if unlocated is None else unlocated}


def experiment(first=None, second=None, **kwargs):
    return TwoModelWholePagePipeline(
        FakeClient("primary", {"items": [correct()]} if first is None else first),
        FakeClient("secondary", navigated() if second is None else second),
        structure_navigation_enabled=True, **kwargs)


def secondary_call(result):
    return next(call for call in result.data["multimodel_audit"]["calls"] if call["stage"] == "secondary")


def test_default_stays_parallel_with_identical_v52_result_and_no_navigation():
    plain = flow(barrier=Barrier(2))
    result = plain.recognize(images=(picture(),))
    assert plain.structure_navigation_enabled is False
    assert result.prompt_version == PROMPT_VERSION
    assert "structure_navigation" not in result.data["multimodel_audit"]
    assert result.data["multimodel_audit"]["independent_before_comparison"] is True
    for client in (plain.client, plain.review_client):
        assert json.loads(client.calls[0]["text"])["task_input"] == {}
        assert structure_navigation.REPORT_FIELD not in client.calls[0]["output_schema"]["properties"]


def test_enabled_replaces_secondary_request_without_sharing_content_or_third_vote():
    pipeline = experiment()
    result = pipeline.recognize(images=(picture(),))
    assert len(pipeline.client.calls) == len(pipeline.review_client.calls) == 1
    assert len(result.metadata.attempts) == 2
    assert json.loads(pipeline.client.calls[0]["text"])["task_input"] == {}
    request = pipeline.review_client.calls[0]
    assert json.loads(request["text"])["task_input"] == {
        "navigation": [{"label": "1", "target_unit": "answer_slot"}]}
    assert request["images"] == (picture().data_url,)
    assert "INTERNAL_STANDARD_ANSWER" not in request["text"]
    audit = result.data["multimodel_audit"]
    assert audit["independent_before_comparison"] is False
    assert audit["structure_navigation"] == {"protocol": structure_navigation.PROTOCOL,
        "enabled": True, "structure_shared": True, "content_shared": False}
    assert secondary_call(result)["structure_navigation"]["primary_items_sha256"]
    assert secondary_call(result)["navigation_report"]["coverage_proven"] is False
    assert result.prompt_version == PROMPT_VERSION + ".structure-navigation-experimental-v1"
    assert result.data["items"][0]["assessment"]["verification"]["independent_judgments"] == 2


def test_ready_page_secondary_starts_before_another_pages_slow_primary_finishes():
    secondary_started = Event()
    events = []

    def first(request):
        asset = json.loads(request["text"])["asset_id"]
        if asset == "slow-page":
            assert secondary_started.wait(timeout=2), "other primary imposed a global barrier"
        events.append((asset, "primary-finished"))
        return {"items": [correct()]}

    def second(request):
        asset = json.loads(request["text"])["asset_id"]
        assert (asset, "primary-finished") in events
        if asset == "fast-page":
            events.append((asset, "secondary-started"))
            secondary_started.set()
        return navigated()

    pipeline = experiment(first, second)
    result = pipeline.recognize(images=(picture("slow-page"), picture("fast-page")))
    assert events.index(("fast-page", "secondary-started")) < events.index(("slow-page", "primary-finished"))
    assert len(pipeline.client.calls) == len(pipeline.review_client.calls) == 2
    assert len(result.metadata.attempts) == 4
    assert [(call["asset_id"], call["stage"]) for call in result.data["multimodel_audit"]["calls"]] == [
        ("slow-page", "primary"), ("slow-page", "secondary"),
        ("fast-page", "primary"), ("fast-page", "secondary")]


@pytest.mark.parametrize("first,reason", [
    (ProviderError("timeout"), "primary_unavailable"),
    ({"items": []}, "invalid_navigation_count"),
    ({"items": [correct("not-a-structural-label")]}, "unsupported_navigation_label"),
])
def test_primary_failure_empty_or_unsupported_navigation_uses_one_ordinary_secondary(first, reason):
    pipeline = experiment(first, {"items": [correct()]})
    result = pipeline.recognize(images=(picture(),))
    assert len(pipeline.client.calls) == len(pipeline.review_client.calls) == 1
    assert json.loads(pipeline.review_client.calls[0]["text"])["task_input"] == {}
    assert secondary_call(result)["structure_navigation"]["skip_reason"] == reason
    assert secondary_call(result)["structure_navigation"]["structure_shared"] is False
    assert result.data["multimodel_audit"]["independent_before_comparison"] is True


@pytest.mark.parametrize("failure,expected_code", [
    (ProviderError("timeout"), "timeout"),
    ({"items": [correct()]}, "invalid_output"),
])
def test_failed_navigated_secondary_never_repeats_or_falls_back_after_dispatch(failure, expected_code):
    pipeline = experiment(second=failure)
    result = pipeline.recognize(images=(picture(),))
    assert len(pipeline.client.calls) == len(pipeline.review_client.calls) == 1
    assert secondary_call(result)["code"] == expected_code
    assert secondary_call(result)["structure_navigation"]["structure_shared"] is True
    row = result.data["items"][0]
    assert row["recognition_status"] == "needs_revision"
    assert "provider_unavailable" in row["assessment"]["verification"]["reason_codes"]


def test_secondary_extra_question_survives_as_a_real_pending_observation():
    extra = correct("2", stem_text="独立额外题：3+5", answer_text="8")
    pipeline = experiment(second=navigated([correct(), extra]))
    result = pipeline.recognize(images=(picture(),))
    rows = result.data["items"]
    assert [row["label"] for row in rows] == ["1", "2"]
    assert rows[1]["stem_text"] == extra["stem_text"] and rows[1]["answer_text"] == extra["answer_text"]
    assert rows[1]["recognition_status"] == "needs_revision"
    assert rows[1]["assessment"]["knowledge_points"] == []
    assert secondary_call(result)["navigation_report"]["unlisted_output_labels"] == ["2"]


def test_reported_missing_navigation_does_not_generate_secondary_placeholders():
    pipeline = experiment({"items": [correct("1"), correct("99")]}, navigated([correct()], unlocated=["99"]))
    result = pipeline.recognize(images=(picture(),))
    audit = secondary_call(result)
    assert [row["label"] for row in audit["items"]] == ["1"]
    assert audit["navigation_report"]["navigation_labels_without_exact_output"] == ["99"]
    missing = next(row for row in result.data["items"] if row["label"] == "99")
    assert missing["recognition_status"] == "needs_revision"
    assert "unmatched_label" in missing["assessment"]["verification"]["reason_codes"]


def test_contradictory_navigation_report_only_blocks_that_target_and_preserves_raw_source():
    originals = [correct("1"), correct("2")]
    before = deepcopy(originals)
    pipeline = experiment({"items": originals}, navigated(originals, unlocated=["1", "77"]))
    result = pipeline.recognize(images=(picture(),))
    assert [row["recognition_status"] for row in result.data["items"]] == ["needs_revision", "ready"]
    audit = secondary_call(result)
    assert audit["navigation_report"]["unknown_unlocated_navigation_labels"] == ["77"]
    assert audit["navigation_report"]["reported_unlocated_but_returned"] == ["1"]
    assert audit["raw_items"] == before
    assert originals == before
    assert audit["items"][0]["answer_text"] == before[0]["answer_text"]
    assert audit["observation_normalizations"] == [{"label": "1", "code": "navigation_locator_report_conflict"}]


def test_malformed_auxiliary_report_is_audited_without_erasing_actual_observations():
    pipeline = experiment(second=navigated(unlocated=[" "]))
    result = pipeline.recognize(images=(picture(),))
    audit = secondary_call(result)
    assert audit["code"] == "ok"
    assert audit["navigation_report"] == {"code": "invalid_report", "reason": "invalid_navigation_report_label",
        "raw_unlocated_labels": [" "], "coverage_proven": False}
    assert len(audit["items"]) == 1 and audit["items"][0]["answer_text"] == "4"


def test_unknown_auxiliary_report_alone_does_not_trigger_additional_paid_review():
    reviewer = FakeClient("reviewer", ProviderError("must_not_be_called"))
    blind = FakeClient("blind", ProviderError("must_not_be_called"))
    pipeline = experiment(second=navigated(unlocated=["77"]), reconciliation_client=reviewer, visual_client=blind)
    result = pipeline.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "ready"
    assert secondary_call(result)["navigation_report"]["unknown_unlocated_navigation_labels"] == ["77"]
    assert not reviewer.calls and not blind.calls
    assert len(result.metadata.attempts) == 2


def test_one_page_fallback_does_not_claim_other_navigated_pages_are_fully_independent():
    def first(request):
        asset = json.loads(request["text"])["asset_id"]
        return {"items": [correct("named-section" if asset == "fallback-page" else "1")]}

    def second(request):
        task = json.loads(request["text"])
        if task["asset_id"] == "fallback-page":
            assert task["task_input"] == {}
            return {"items": [correct("named-section")]}
        assert "navigation" in task["task_input"]
        return navigated()

    pipeline = experiment(first, second)
    result = pipeline.recognize(images=(picture("fallback-page"), picture("navigated-page")))
    audit = result.data["multimodel_audit"]
    assert audit["independent_before_comparison"] is False
    calls = {call["asset_id"]: call for call in audit["calls"] if call["stage"] == "secondary"}
    assert calls["fallback-page"]["structure_navigation"]["structure_shared"] is False
    assert calls["fallback-page"]["structure_navigation"]["skip_reason"] == "unsupported_navigation_label"
    assert calls["navigated-page"]["structure_navigation"]["structure_shared"] is True
    assert len(pipeline.client.calls) == len(pipeline.review_client.calls) == 2


@pytest.mark.parametrize("changes,reason", [
    ({"answer_text": "-4"}, "answer_disagreement"),
    ({"stem_text": "另一道题：(-2)^3"}, "stem_disagreement"),
    ({"verdict": "incorrect"}, "judgment_disagreement"),
    ({"visible_locator": {"printed_label": "2", "page_zone": None}}, "target_identity_conflict"),
])
def test_same_navigation_label_does_not_resolve_content_or_visible_identity_conflict(changes, reason):
    original = correct(visible_locator={"printed_label": "1", "page_zone": None})
    pipeline = experiment({"items": [original]}, navigated([{**original, **changes}]))
    result = pipeline.recognize(images=(picture(),))
    assert all(row["recognition_status"] == "needs_revision" for row in result.data["items"])
    assert any(reason in row["assessment"]["verification"]["reason_codes"] for row in result.data["items"])


def test_r1_forces_original_parallel_blind_reads_even_with_experiment_enabled():
    barrier = Barrier(2)
    pipeline = TwoModelWholePagePipeline(
        FakeClient("primary", {"items": [correct()]}, barrier=barrier),
        FakeClient("secondary", {"items": [correct()]}, barrier=barrier),
        structure_navigation_enabled=True)
    result = pipeline.rerecognize_original(**context())
    assert result.prompt_version == PROMPT_VERSION + ".original-recheck-r1"
    assert "structure_navigation" not in result.data["multimodel_audit"]
    for provider in (pipeline.client, pipeline.review_client):
        request = provider.calls[0]
        assert list(json.loads(request["text"])["task_input"]) == ["original_target"]
        assert request["output_schema"]["properties"]["items"]["maxItems"] == 1
        assert structure_navigation.REPORT_FIELD not in request["output_schema"]["properties"]


def test_question_protocol_keeps_actual_count_conflict_instead_of_copying_navigation():
    base = correct("2a", blank_count=8, unit_complete=True, answer_text="第1空：a；第2空：b；第3空：c；第4空：d；第5空：e；第6空：f；第7空：g；第8空：h",
        target_context={"section": "2a", "row": None, "column": None, "anchor": "printed task"})
    other = {**base, "blank_count": 7, "answer_text": base["answer_text"].removesuffix("；第8空：h")}
    pipeline = experiment({"items": [base]}, navigated([other]), target_unit=question_units.QUESTION_UNIT)
    result = pipeline.recognize(images=(picture(),))
    assert json.loads(pipeline.review_client.calls[0]["text"])["task_input"] == {
        "navigation": [{"label": "2a", "target_unit": "question"}]}
    assert len(result.data["items"]) == 2
    assert all(row["recognition_status"] == "needs_revision" for row in result.data["items"])
    assert [call["items"][0]["blank_count"] for call in result.data["multimodel_audit"]["calls"]] == [8, 7]


@pytest.mark.parametrize("value", [None, 0, 1, "0", "1", [], {}])
def test_direct_constructor_requires_actual_boolean(value):
    with pytest.raises(ProviderError, match="invalid_configuration"):
        TwoModelWholePagePipeline(FakeClient("a", {}), FakeClient("b", {}), structure_navigation_enabled=value)
