"""Offline fault injection through the real bounded HTTP/parser path."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import json

import httpx
import pytest

from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers.ark import ArkSettings, ProviderError
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.partial_output import (PageSchemaFailure, capture_enabled,
    capture_page_failures, partition_page)
from xueban_backend.providers.question_units import schema_for
from xueban_backend.providers.two_model import PAGE_SCHEMA, TwoModelWholePagePipeline

SCHEMA = schema_for(PAGE_SCHEMA, "answer_slot")


def envelope(data, *, finish="stop"):
    return {"id": "synthetic-response", "model": "synthetic-model",
            "usage": {"prompt_tokens": 11, "completion_tokens": 23, "total_tokens": 34},
            "choices": [{"finish_reason": finish, "message": {"content": json.dumps(data, ensure_ascii=False)}}]}


def client(data, name, *, response=None):
    requests = []
    def serve(request):
        requests.append(request)
        return response if response is not None else httpx.Response(200, json=envelope(data))
    config = ArkSettings(base_url="https://synthetic.invalid/v1", api_key=f"secret-{name}-not-real",
                         model=name, max_attempts=1)
    return BoundedChatClient(config, transport=httpx.MockTransport(serve)), requests


def pipeline_result(first, second, *, enabled=True, **kwargs):
    a, a_requests = client({"items": first}, "model-a")
    b, b_requests = client({"items": second}, "model-b")
    pipeline = TwoModelWholePagePipeline(a, b, partial_output_enabled=enabled, **kwargs)
    try:
        result = pipeline.recognize(images=(picture(),))
    finally:
        pipeline.close()
    assert len(a_requests) == len(b_requests) == 1
    assert len(result.metadata.attempts) == 2
    return result


@pytest.mark.parametrize("bad_answer", ["", 7, True, {}, [], "x" * 4001])
def test_one_invalid_answer_preserves_other_two_source_units_and_never_repairs(bad_answer):
    first = [correct("1"), correct("2", answer_text=bad_answer)]
    before = deepcopy(first)
    result = pipeline_result(first, [correct("1"), correct("2")])
    one, two = result.data["items"]
    assert one["label"] == "1" and one["recognition_status"] == "ready"
    assert one["assessment"]["verification"]["independent_judgments"] == 2
    assert two["label"] == "2" and two["recognition_status"] == "needs_revision"
    assert two["answer_text"] == "4"  # Only the valid second source is displayed.
    assert two["assessment"]["verification"]["independent_judgments"] == 1
    assert {"provider_unavailable", "provider_item_invalid"} <= set(two["assessment"]["verification"]["reason_codes"])
    assert "visual_unreadable" not in two["assessment"]["verification"]["reason_codes"]
    assert two["assessment"]["knowledge_points"] == []
    assert two["assessment"]["verdict"] == "unable_to_judge"
    assert result.data["multimodel_audit"]["calls"][0]["code"] == "partial_output"
    assert result.metadata.attempts[0].code == "invalid_output"
    assert result.metadata.attempts[1].code == "ok"
    assert result.metadata.usage.total_tokens == 68
    assert first == before


def test_default_disabled_keeps_existing_whole_source_failure_behavior():
    result = pipeline_result([correct("1"), correct("2", answer_text=8)], [correct("1"), correct("2")], enabled=False)
    assert all(item["recognition_status"] == "needs_revision" for item in result.data["items"])
    assert "partial_output" not in result.data["multimodel_audit"]


def test_same_bad_row_in_both_sources_is_page_gap_not_invented_question():
    result = pipeline_result([correct("1"), correct("2", answer_text=8)],
                             [correct("1"), correct("2", answer_text={})])
    assert [item["label"] for item in result.data["items"]] == ["1"]
    assert "2个模型返回项" in result.data["coverage"]["note"]
    assert "核对服务尚未全部完成" in result.data["coverage"]["note"]
    assert result.data["coverage"]["collection_scope"] == "partial"


def test_duplicate_group_including_schema_bad_member_is_entirely_quarantined():
    result = pipeline_result([correct("1"), correct("第３题。"), correct(" ３ 、", answer_text=7)],
                             [correct("1"), correct("3")])
    assert [item["recognition_status"] for item in result.data["items"]] == ["ready", "needs_revision"]
    audit = result.data["multimodel_audit"]["calls"][0]["partial_output"]
    assert audit["accepted_source_indices"] == [0]
    assert [(row["source_index"], row["rule"]) for row in audit["rejected"]] == [
        (1, "duplicate_target_label"), (2, "duplicate_target_label")]


@pytest.mark.parametrize("shell", [{"items": {}}, {"items": [correct()] * 81}, {"items": [correct()], "unknown": "private"}, {}])
def test_bad_page_shell_is_not_salvaged(shell):
    with pytest.raises(ProviderError, match="invalid_output"):
        partition_page(shell, SCHEMA)


@pytest.mark.parametrize("changes", [{"label": 3}, {"label": "。"}, {"label": ""}, {"stem_text": None},
                                      {"stem_text": {}}, {"stem_text": " "}, {"visible_locator": {"unknown": "private"}}])
def test_unusable_identity_only_creates_a_page_gap(changes):
    result = partition_page({"items": [correct("1"), {**correct("2"), **changes}]}, SCHEMA)
    assert [row.source_index for row in result.accepted] == [0]
    assert len(result.rejected) == 1
    assert "private" not in repr(result) and "private" not in str(result.audit())


def test_failure_payload_is_private_one_use_and_full_original_schema_stays_requested():
    bad = {"items": [correct("1"), correct("2", answer_text={"private_student": "private-answer"})]}
    a, requests = client(bad, "model-a")
    try:
        with capture_page_failures(True), pytest.raises(PageSchemaFailure) as caught:
            a.generate(role="check", text="synthetic", output_schema=SCHEMA)
        error = caught.value
        assert "private-answer" not in repr(error) + str(error) + str(asdict(error.metadata))
        assert error.take_candidate(SCHEMA, [a.settings.api_key]) == bad
        with pytest.raises(ProviderError):
            error.take_candidate(SCHEMA, [])
        request = json.loads(requests[0].content)
        assert json.dumps(SCHEMA, ensure_ascii=False) in request["messages"][0]["content"]
    finally:
        a.close()
    assert not capture_enabled()


@pytest.mark.parametrize("kind", ["truncated", "bad_json", "conflicting_keys", "refusal", "tool_calls", "multiple_choices", "http_failure", "secret"])
def test_envelope_json_transport_and_credential_failures_cannot_yield_private_candidate(kind):
    data = {"items": [correct("1"), correct("2", answer_text=7)]}
    value = envelope(data)
    if kind == "truncated":
        value["choices"][0]["finish_reason"] = "length"
    elif kind == "bad_json":
        value["choices"][0]["message"]["content"] = '{"items":['
    elif kind == "conflicting_keys":
        value["choices"][0]["message"]["content"] = '{"items":[],"items":[3]}'
    elif kind in ("refusal", "tool_calls"):
        value["choices"][0]["message"][kind] = "private-answer"
    elif kind == "multiple_choices":
        value["choices"] *= 2
    elif kind == "secret":
        data["items"][1]["answer_text"] = {"hidden": "secret-model-a-not-real"}
        value = envelope(data)
    response = httpx.Response(500 if kind == "http_failure" else 200, json=value)
    a, requests = client(data, "model-a", response=response)
    try:
        with capture_page_failures(True), pytest.raises(ProviderError) as caught:
            a.generate(role="check", text="synthetic", output_schema=SCHEMA)
        assert not isinstance(caught.value, PageSchemaFailure)
        assert len(requests) == len(caught.value.attempts) == 1
        assert "private-answer" not in repr(caught.value) + str(caught.value)
    finally:
        a.close()


def test_cross_provider_secret_in_rejected_row_is_not_discarded_before_security_check():
    result = pipeline_result([correct("1"), correct("2", answer_text={"key": "secret-model-b-not-real"})],
                             [correct("1"), correct("2")])
    assert result.metadata.attempts[0].code == "provider_credential_echo"
    assert result.metadata.attempts[0].usage.total_tokens == 34
    assert all(row["recognition_status"] == "needs_revision" for row in result.data["items"])
    assert "secret-model-b-not-real" not in str(result.data) + repr(result.metadata)


@pytest.mark.parametrize("status", [200, 500])
def test_cross_provider_secret_in_failed_request_id_never_enters_audit(status):
    response = httpx.Response(status, headers={"x-request-id": "secret-model-b-not-real"},
                             json=envelope({}) if status == 500 else envelope({}))
    if status == 200:
        value = envelope({})
        value["choices"][0]["message"]["content"] = '{"items":'
        response = httpx.Response(200, headers={"x-request-id": "secret-model-b-not-real"}, json=value)
    first, requests = client({}, "model-a", response=response)
    second, _ = client({"items": [correct()]}, "model-b")
    pipeline = TwoModelWholePagePipeline(first, second, partial_output_enabled=True)
    try:
        call = pipeline._read_and_judge(first, picture(), "primary")
        assert call["code"] == "provider_credential_echo"
        assert "secret-model-b-not-real" not in repr(call) + str(asdict(call["metadata"]))
        assert call["metadata"].attempts[0].request_id is None
        assert len(requests) == len(call["metadata"].attempts) == 1
    finally:
        pipeline.close()


def test_partial_filtering_cannot_create_a_new_unique_cross_label_match():
    # Both original primary rows had the same full print. Dropping the malformed
    # row cannot make the remaining row a globally unique partner for label 3.
    stem = "根据下列a的值，求代数式a+1的值：a=3"
    result = pipeline_result([correct("1", stem_text=stem), correct("2", stem_text=stem, answer_text=7)],
                             [correct("3", stem_text=stem)])
    assert {item["label"] for item in result.data["items"]} == {"1", "3"}
    assert all(item["recognition_status"] == "needs_revision" for item in result.data["items"])
    assert result.data["multimodel_audit"]["label_alignments"] == []


def test_capture_scope_is_thread_local_and_resets_after_exception():
    def work(enabled):
        try:
            with capture_page_failures(enabled):
                assert capture_enabled() is enabled
                raise RuntimeError("synthetic")
        except RuntimeError:
            return capture_enabled()
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(work, [True, False])) == [False, False]
    assert not capture_enabled()


def test_missing_bad_source_never_enqueues_an_additional_review():
    reviewer = FakeClient("unused-reviewer", {"items": []})
    result = pipeline_result([correct("1"), correct("2", answer_text=7)], [correct("1"), correct("2")],
                             reconciliation_client=reviewer)
    assert reviewer.calls == []
    assert result.data["items"][1]["recognition_status"] == "needs_revision"


def test_other_valid_disagreement_on_partial_page_does_not_dispatch_more_calls():
    reviewer = FakeClient("unused-reviewer", RuntimeError("unexpected extra request"))
    result = pipeline_result([correct("1"), correct("2", answer_text=7)],
                             [correct("1", answer_text="-4"), correct("2")],
                             reconciliation_client=reviewer, visual_client=reviewer,
                             judgment_client=reviewer)
    assert reviewer.calls == []
    assert all(row["recognition_status"] == "needs_revision" for row in result.data["items"])
    assert result.metadata.usage.total_tokens == 68


@pytest.mark.parametrize("value", [None, "", "true", "false", "yes", "2", 0, 1, True, False])
def test_invalid_environment_flag_fails_before_constructing_a_client(monkeypatch, value):
    from xueban_backend.providers import two_model
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid configuration must fail before constructing clients")
    monkeypatch.setattr(two_model, "BoundedChatClient", forbidden)
    monkeypatch.setattr(two_model, "ResponsesClient", forbidden)
    with pytest.raises(ProviderError, match="invalid_configuration"):
        two_model.TwoModelWholePagePipeline.from_env({"PARTIAL_PAGE_OUTPUT_ENABLED": value})


@pytest.mark.parametrize("env,expected", [({}, False), ({"PARTIAL_PAGE_OUTPUT_ENABLED": "0"}, False),
                                         ({"PARTIAL_PAGE_OUTPUT_ENABLED": "1"}, True)])
def test_environment_flag_defaults_off_and_requires_explicit_one(env, expected):
    # No configured credentials: construction makes no requests and reads no runtime env.
    with TwoModelWholePagePipeline.from_env(env) as pipeline:
        assert pipeline.partial_output_enabled is expected
        assert pipeline._partial_output_active() is expected


@pytest.mark.parametrize("value", [None, 0, 1, "0", "1", [], {}])
def test_constructor_flag_rejects_non_boolean_values(value):
    with pytest.raises(ProviderError, match="invalid_configuration"):
        TwoModelWholePagePipeline(FakeClient("primary", {}), FakeClient("secondary", {}),
                                  partial_output_enabled=value)


def test_r1_does_not_opt_in_to_partial_recovery():
    a, _ = client({"items": [correct("1"), correct("2", answer_text=7)]}, "model-a")
    b, _ = client({"items": [correct("1")]}, "model-b")
    pipeline = TwoModelWholePagePipeline(a, b, partial_output_enabled=True)
    pipeline._original_recheck_target = {}
    assert pipeline._partial_output_active() is False
    pipeline.close()
