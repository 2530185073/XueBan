"""Independent synthetic transport and orchestration boundary tests."""
from copy import deepcopy
from dataclasses import asdict
import json

import httpx
import pytest

from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers.ark import ArkSettings, ProviderError
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.two_model import TwoModelWholePagePipeline
from xueban_backend.providers.two_model import PAGE_SCHEMA
from xueban_backend.providers.partial_output import capture_enabled, capture_page_failures, PageSchemaFailure, partition_page
from xueban_backend.providers.question_units import schema_for
from xueban_backend.learning_rules import model_decision_usable
from xueban_backend.recognition_recheck import ready
from xueban_backend.staff import case_queue_reason, needs_staff

SCHEMA = schema_for(PAGE_SCHEMA, "answer_slot")


def source(rows, name):
    calls = []
    def serve(request):
        body = json.loads(request.content)
        calls.append(body)
        data = rows(body) if callable(rows) else {"items": rows}
        return httpx.Response(200, json={"id": "req-" + name, "model": name,
            "usage": {"prompt_tokens": 11, "completion_tokens": 23, "total_tokens": 34},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]})
    cfg = ArkSettings(base_url="https://synthetic.invalid/v1", api_key="synthetic-key-" + name,
                      model=name, max_attempts=1)
    return BoundedChatClient(cfg, transport=httpx.MockTransport(serve)), calls


def run(first, second, **kwargs):
    a, ca = source(first, "primary")
    b, cb = source(second, "secondary")
    pipe = TwoModelWholePagePipeline(a, b, partial_output_enabled=True, **kwargs)
    try:
        result = pipe.recognize(images=(picture(),))
    finally:
        pipe.close()
    return result, ca, cb


@pytest.mark.parametrize("duplicate", [False, True])
def test_quarantine_must_not_create_false_global_uniqueness_for_label_alignment(duplicate):
    stem = "计算两个有理数相加的结果：1 + 3 = ?"
    first = [correct("1", stem_text=stem), correct("2", stem_text=stem, answer_text=7)]
    if duplicate:
        first.append(correct("第２题。", stem_text=stem))
    result, a, b = run(first, [correct("2", stem_text=stem)])
    assert len(a) == len(b) == 1
    # The source has two indistinguishable printed targets. Removing a bad
    # schema row cannot prove the remaining label1 is the other reader's 2.
    assert not result.data["multimodel_audit"]["label_alignments"]
    assert all(row["recognition_status"] == "needs_revision" for row in result.data["items"])


@pytest.mark.parametrize("bad_first", [True, False])
def test_single_valid_source_never_gets_extra_review_or_learning_evidence(bad_first):
    good = [correct("1"), correct("2")]
    broken = [correct("1"), correct("2", answer_text={"not_a_transcript": 4})]
    bomb = FakeClient("forbidden-extra-review", RuntimeError("partial item must not be sent for review"))
    result, a, b = run(broken if bad_first else good, good if bad_first else broken,
                       reconciliation_client=bomb, visual_client=bomb, judgment_client=bomb)
    assert len(a) == len(b) == 1 and not bomb.calls
    assert result.metadata.usage.total_tokens == 68 and len(result.metadata.attempts) == 2
    rows = {row["label"]: row for row in result.data["items"]}
    assert rows["1"]["recognition_status"] == "ready"
    row = rows["2"]
    assert row["recognition_status"] == "needs_revision" and row["answer_text"] == "4"
    decision = row["assessment"]
    assert decision["verdict"] == "unable_to_judge" and decision["knowledge_points"] == []
    assert decision["correct_answer"] is decision["correction_method"] is None
    assert decision["verification"]["independent_judgments"] == 1
    assert {"provider_unavailable", "provider_item_invalid"} <= set(decision["verification"]["reason_codes"])
    assert "visual_unreadable" not in decision["verification"]["reason_codes"]
    assert not model_decision_usable(decision)
    assert not ready(row)
    case = {"lifecycle": "active", "progress": "pending", "current_decision": {
        **decision, "origin": "model", "validity": "needs_review"}, "check_ids": ["check_1"]}
    attempt = {"recognition_status": row["recognition_status"], "answer_status": row["answer_status"],
               "recognition_uncertainties": row["uncertainties"]}
    question = {"source": "recognizer", "recognition_uncertainties": []}
    assert needs_staff(case, attempt, question) is None
    assert case_queue_reason(case, attempt, question) == "ai_pending"


def test_all_bad_sources_keep_one_usage_record_per_network_request():
    a, ca = source([correct("1", answer_text=7)], "primary")
    b, cb = source([correct("1", answer_text={})], "secondary")
    pipe = TwoModelWholePagePipeline(a, b, partial_output_enabled=True)
    try:
        with pytest.raises(ProviderError, match="multimodel_unavailable") as caught:
            pipe.recognize(images=(picture(),))
    finally:
        pipe.close()
    assert len(ca) == len(cb) == 1
    assert len(caught.value.attempts) == 2
    assert sum(row.usage.total_tokens for row in caught.value.attempts) == 68
    assert all(row.code == "invalid_output" for row in caught.value.attempts)


def test_all_bad_one_side_is_service_pending_and_other_side_not_discarded():
    result, a, b = run([correct("1", answer_text=7)], [correct("1")])
    [row] = result.data["items"]
    assert row["recognition_status"] == "needs_revision" and row["answer_text"] == "4"
    assert row["assessment"]["verification"]["status"] == "degraded"
    assert "provider_unavailable" in row["assessment"]["verification"]["reason_codes"]
    assert not model_decision_usable(row["assessment"])
    assert result.metadata.usage.total_tokens == 68
    assert len(result.metadata.attempts) == len(a) + len(b) == 2
    assert "1个模型返回项" in result.data["coverage"]["note"]


def test_cross_image_identical_labels_are_not_paired_to_replace_missing_source():
    def primary(body):
        asset = json.loads(body["messages"][-1]["content"][0]["text"])["asset_id"]
        return {"items": [correct("1", answer_text=7 if asset == "asset_b" else "4")]}
    def secondary(body):
        asset = json.loads(body["messages"][-1]["content"][0]["text"])["asset_id"]
        return {"items": [correct("1", answer_text=7 if asset == "asset_a" else "4")]}
    a, ca = source(primary, "primary")
    b, cb = source(secondary, "secondary")
    pipe = TwoModelWholePagePipeline(a, b, partial_output_enabled=True)
    try:
        result = pipe.recognize(images=(picture("asset_a"), picture("asset_b")))
    finally:
        pipe.close()
    assert len(result.data["items"]) == 2
    assert {row["asset_id"] for row in result.data["items"]} == {"asset_a", "asset_b"}
    assert all(row["recognition_status"] == "needs_revision" for row in result.data["items"])
    assert len(ca) == len(cb) == 2 and result.metadata.usage.total_tokens == 136


@pytest.mark.parametrize("field,value", [("answer_text", "x" * 4001), ("answer_text", True),
    ("label", "。"), ("label", None), ("stem_text", None), ("visible_locator", {"printed_label": "1"})])
def test_invalid_values_are_never_repaired_or_used_as_print_identity(field, value):
    rows = [correct("1"), {**correct("2"), field: value}]
    original = deepcopy(rows)
    partition = partition_page({"items": rows}, SCHEMA)
    assert [item.source_index for item in partition.accepted] == [0]
    assert [item.source_index for item in partition.rejected] == [1]
    assert rows == original and partition.accepted[0].item == original[0]


@pytest.mark.parametrize("shell", [{"items": None}, {"items": [correct("1")] * 81},
    {"items": [correct("1")], "private_extra": "sentinel"}, [], {"other": []}])
def test_shell_failure_cannot_salvage_by_dropping_or_truncating(shell):
    with pytest.raises(ProviderError, match="invalid_output"):
        partition_page(shell, SCHEMA)


@pytest.mark.parametrize("disabled_scope", ["r1", "question", "navigation", "flag_off"])
def test_unsupported_scope_never_salvages_schema_failure(disabled_scope):
    a, calls = source([correct("1"), correct("2", answer_text=7)], "primary")
    b, _ = source([correct("1")], "secondary")
    kwargs = {"partial_output_enabled": disabled_scope != "flag_off"}
    if disabled_scope == "question": kwargs["target_unit"] = "question"
    if disabled_scope == "navigation": kwargs["structure_navigation_enabled"] = True
    pipe = TwoModelWholePagePipeline(a, b, **kwargs)
    if disabled_scope == "r1": pipe._original_recheck_target = {"label": "2"}
    try:
        result = pipe._read_and_judge(a, picture(), "primary")
    finally:
        pipe.close()
    assert result["code"] == "invalid_output" and result["items"] == {}
    assert len(calls) == 1 and not capture_enabled()
    assert "partial_output" not in result


@pytest.mark.parametrize("echo_location", ["bad_value", "bad_key", "nested_escape", "request_id"])
def test_other_provider_secret_cannot_survive_rejected_item_or_error_metadata(echo_location):
    other_key = "synthetic-key-secondary"
    data = {"items": [correct("1"), correct("2", answer_text=7)]}
    if echo_location == "bad_value": data["items"][1]["answer_text"] = {"private": other_key}
    if echo_location == "bad_key": data["items"][1][other_key] = "private"
    if echo_location == "nested_escape":
        data["items"][1]["answer_text"] = {"private": "".join("\\u%04x" % ord(char) for char in other_key)}
    headers = {"x-request-id": other_key} if echo_location == "request_id" else {}
    body = {"model": "primary", "usage": {"prompt_tokens": 11, "completion_tokens": 23, "total_tokens": 34},
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}
    if echo_location == "request_id": body["choices"][0]["message"]["content"] = "{bad JSON"
    a = BoundedChatClient(ArkSettings(base_url="https://synthetic.invalid/v1", api_key="synthetic-key-primary",
        model="primary", max_attempts=1), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body, headers=headers)))
    b, _ = source([correct("1")], "secondary")
    pipe = TwoModelWholePagePipeline(a, b, partial_output_enabled=True)
    try:
        result = pipe._read_and_judge(a, picture(), "primary")
    finally:
        pipe.close()
    assert result["code"] == "provider_credential_echo" and result["items"] == {}
    assert other_key not in repr(result)
    assert other_key not in json.dumps(asdict(result["metadata"]))


def test_safe_candidate_is_one_use_and_capture_resets_after_wrong_schema():
    a, calls = source([correct("1", answer_text=7)], "primary")
    try:
        with capture_page_failures(True), pytest.raises(PageSchemaFailure) as caught:
            a.generate(role="check", text="synthetic", output_schema=SCHEMA)
        assert not capture_enabled()
        incompatible = deepcopy(SCHEMA)
        incompatible["properties"]["items"]["maxItems"] = 1
        with pytest.raises(ProviderError): caught.value.take_candidate(incompatible, [])
        with pytest.raises(ProviderError): caught.value.take_candidate(SCHEMA, [])
        with pytest.raises(ProviderError) as strict:
            a.generate(role="check", text="synthetic", output_schema=SCHEMA)
        assert not isinstance(strict.value, PageSchemaFailure)
    finally:
        a.close()
    assert len(calls) == 2
