"""Batch integration: blind observations stay immutable and review is scoped."""

import json
from threading import Barrier

import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers.ark import Attempt, ProviderError
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def pipeline(primary, secondary, output):
    return TwoModelWholePagePipeline(
        FakeClient("deepseek", {"items": primary}),
        FakeClient("astra", {"items": secondary}),
        reconciliation_client=FakeClient("astra-review", output))


def different_stem(label="1", **changes):
    return correct(label, stem_text="求(-2)²的值", **changes)


def test_one_batch_review_only_for_conflicts_keeps_both_blind_reads_and_private_resolution():
    first = [correct("1"), correct("2"), correct("3")]
    second = [different_stem("1"), different_stem("2"), correct("3")]
    flow = pipeline(first, second, {"items": [review(label="1"), review(label="2")]})
    result = flow.recognize(images=(picture(),))
    rows = result.data["items"]
    assert [row["assessment"]["verification"]["status"] for row in rows] == ["reviewed", "reviewed", "matched"]
    assert all(row["assessment"]["verification"]["reason_codes"] == [] for row in rows)
    assert len(flow.client.calls) == len(flow.review_client.calls) == len(flow.reconciliation_client.calls) == 1
    assert flow.client.calls[0]["thinking"] is False
    assert flow.review_client.calls[0]["thinking"] is True
    request = flow.reconciliation_client.calls[0]
    assert request["thinking"] is False
    assert request["images"] == (picture().data_url,)
    targets = json.loads(request["text"])["task_input"]["items"]
    assert [item["label"] for item in targets] == ["1", "2"]
    for independent in (flow.client, flow.review_client):
        assert json.loads(independent.calls[0]["text"])["task_input"] == {}
    audit = result.data["multimodel_audit"]["calls"]
    assert audit[0]["items"] == first and audit[1]["items"] == second
    assert audit[2]["applications"][0]["original_reason_codes"] == ["stem_disagreement"]
    assert audit[2]["applications"][0]["resolved_reason_codes"] == ["semantic_review_equivalent"]
    assert len(result.metadata.attempts) == 3
    assert result.metadata.usage.total_tokens == 90


def test_review_does_not_call_when_both_reads_match_or_a_service_is_unavailable():
    flow = pipeline([correct()], [correct()], {"items": []})
    assert flow.recognize(images=(picture(),)).data["items"][0]["assessment"]["verdict"] == "correct"
    assert not flow.reconciliation_client.calls
    flow.review_client.output = ProviderError("deadline_exceeded")
    row = flow.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verification"]["status"] == "degraded"
    assert not flow.reconciliation_client.calls


@pytest.mark.parametrize("cause", ["visual_unreadable", "model_disagreement"])
def test_failed_review_has_specific_action_reason_and_does_not_taint_agreed_items(cause):
    unresolved = review(resolution="unresolved", answer_clear=False, unresolved_cause=cause,
                        remaining_uncertainties=["图上无法核实该处"] if cause == "visual_unreadable" else ["两种判定尚未排除"])
    flow = pipeline([correct("1"), correct("2")], [different_stem("1"), correct("2")],
                    {"items": [unresolved]})
    rows = flow.recognize(images=(picture(),)).data["items"]
    assert rows[0]["recognition_status"] == "needs_revision"
    assert cause in rows[0]["assessment"]["verification"]["reason_codes"]
    assert rows[0]["assessment"]["knowledge_points"] == []
    assert rows[1]["assessment"]["verdict"] == "correct"


def test_review_timeout_is_audited_not_retried_and_not_called_image_blur():
    flow = pipeline([correct()], [different_stem()],
                    ProviderError("deadline_exceeded", attempts=(Attempt(1, 200, "deadline_exceeded", 40),)))
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["assessment"]["verification"]["reason_codes"] == ["stem_disagreement", "reconciliation_unavailable"]
    assert "服务暂未完成" in row["assessment"]["basis_summary"]
    assert len(flow.reconciliation_client.calls) == 1
    assert [a.code for a in result.metadata.attempts] == ["ok", "ok", "deadline_exceeded"]
    assert result.metadata.usage is None


@pytest.mark.parametrize("output", [
    {"items": [review(label="other")]},
    {"items": [review(), review()]},
    {"items": [{"label": "1"}]},
])
def test_invalid_review_cannot_release_conflict_and_keeps_paid_attempt(output):
    result = pipeline([correct()], [different_stem()], output).recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "unable_to_judge"
    assert result.metadata.attempts[-1].code == "invalid_output"


def test_review_missing_one_item_leaves_only_that_item_pending():
    flow = pipeline([correct("1"), correct("2")], [different_stem("1"), different_stem("2")],
                    {"items": [review(label="1")]})
    rows = flow.recognize(images=(picture(),)).data["items"]
    assert rows[0]["assessment"]["verdict"] == "correct"
    assert rows[1]["assessment"]["verdict"] == "unable_to_judge"
    assert "model_disagreement" in rows[1]["assessment"]["verification"]["reason_codes"]


def test_review_cannot_echo_another_clients_secret_into_audit():
    flow = pipeline([correct()], [different_stem()], {"items": []})
    flow.reconciliation_client.output = {"items": [review(visual_evidence=flow.client.settings.api_key)]}
    result = flow.recognize(images=(picture(),))
    assert flow.client.settings.api_key not in str(result.data)
    assert result.metadata.attempts[-1].code == "provider_credential_echo"


def test_visual_resolution_keeps_selected_original_student_answer_and_changes_judgment():
    first = correct(answer_text="12x", verdict="incorrect", correct_answer="2a+10")
    second = correct(answer_text="2a+10")
    choice = review(resolution="visual_resolution", answer_text="12x", verdict="incorrect",
                    correct_answer="2a+10", correction_method="按上月收入的2倍再加10元列式。",
                    visual_evidence="原图中第一位是1，中间是2，末尾可见交叉的x，未见加号。")
    result = pipeline([first], [second], {"items": [choice]}).recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["answer_text"] == "12x"
    assert row["assessment"]["verdict"] == "incorrect"
    assert row["assessment"]["correct_answer"] == "2a+10"
    assert row["assessment"]["verification"]["status"] == "reviewed"
    assert row["assessment"]["verification"]["reason_codes"] == []
    assert result.data["multimodel_audit"]["calls"][1]["items"][0]["answer_text"] == "2a+10"


def test_same_client_reused_for_review_is_closed_only_once():
    class ClosingClient(FakeClient):
        closed = 0
        def close(self):
            self.closed += 1
    a, b = ClosingClient("a", {}), ClosingClient("b", {})
    flow = TwoModelWholePagePipeline(a, b, reconciliation_client=b, visual_client=b)
    flow.close()
    assert a.closed == b.closed == 1


def test_four_pages_review_concurrently_with_exactly_one_batch_for_each_page():
    flow = pipeline([correct()], [different_stem()], {"items": [review()]})
    # All four review calls must enter together; serial scheduling would break
    # this barrier rather than merely making the test a little slower.
    flow.reconciliation_client.barrier = Barrier(4)
    result = flow.recognize(images=tuple(picture(f"asset_page{i}") for i in range(4)))
    requests = flow.reconciliation_client.calls
    assert len(requests) == 4
    assert {json.loads(request["text"])["asset_id"] for request in requests} == {
        f"asset_page{i}" for i in range(4)}
    assert all(len(json.loads(request["text"])["task_input"]["items"]) == 1 for request in requests)
    assert all(row["assessment"]["verification"]["status"] == "reviewed" for row in result.data["items"])
    assert len(result.metadata.attempts) == 12


@pytest.mark.parametrize("text", ["36+[不清]+9=81", "36+（涂改不清）+9=81", "36+[illegible]+9=81"])
def test_two_initial_corrects_cannot_hide_explicit_unreadable_marker(text):
    first = correct(answer_text=text, uncertainties=[])
    flow = TwoModelWholePagePipeline(FakeClient("deepseek", {"items": [first]}),
                                    FakeClient("astra", {"items": [first]}))
    row = flow.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert "visual_unreadable" in row["assessment"]["verification"]["reason_codes"]
    assert row["uncertainties"]
    assert row["answer_text"] == text
    assert first["uncertainties"] == []


def test_legible_edit_marker_alone_does_not_block_initial_agreement():
    first = correct(answer_text="36+[划掉-36，改写36]+9=81", uncertainties=[])
    flow = TwoModelWholePagePipeline(FakeClient("deepseek", {"items": [first]}),
                                    FakeClient("astra", {"items": [first]}))
    row = flow.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "correct"
    assert row["uncertainties"] == []


def test_inferred_overwritten_step_is_not_released_by_original_candidate_selection():
    a = correct(answer_text="36+36+9=81")
    b = correct(answer_text="36+[涂改不清]+9=81", answer_status="unclear",
                verdict="unable_to_judge", uncertainties=["中间加数被涂改遮住"])
    unsafe = review(resolution="visual_resolution", answer_text=a["answer_text"],
                    visual_evidence="末行=81清楚，结合首行可见，中间加数应为36，涂改后得到正确结果81。")
    result = pipeline([a], [b], {"items": [unsafe]}).recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["assessment"]["knowledge_points"] == []
    assert row["recognition_status"] == "needs_revision"
    application = result.data["multimodel_audit"]["calls"][-1]["applications"][0]
    assert application["rejection_code"] == "inferred_student_transcription"


@pytest.mark.parametrize("changes", [
    {"stem_text": "当x=[看不清]时，求x+1"},
    {"options": [{"label": "A", "text": "x+[无法辨认]"}], "question_type": "single_choice"},
])
def test_initial_agreement_cannot_hide_unreadable_printed_conditions(changes):
    first = correct(**changes)
    flow = TwoModelWholePagePipeline(FakeClient("deepseek", {"items": [first]}),
                                    FakeClient("astra", {"items": [first]}))
    row = flow.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["recognition_status"] == "needs_revision"
    assert "visual_unreadable" in row["assessment"]["verification"]["reason_codes"]
    assert row["uncertainties"]
    assert row["assessment"]["knowledge_points"] == []


def test_missing_stems_stay_pending_even_if_visual_reviewer_claims_correct():
    first = correct(stem_text=None)
    flow = pipeline([first], [first], {"items": [review(resolution="visual_resolution")]})
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["knowledge_points"] == []
    assert result.data["multimodel_audit"]["calls"][-1]["applications"][0]["rejection_code"] == "missing_question_text"


@pytest.mark.parametrize("label,text", [("3(2)", "1/2ah"), ("3(8)", "0.8a")])
def test_clear_current_fill_answer_does_not_require_reconstructing_deleted_history(label, text):
    a = correct(label, question_type="fill_blank", answer_text=text)
    b = correct(label, question_type="fill_blank", answer_text=text, answer_status="unclear",
                verdict="unable_to_judge", uncertainties=["可辨认当前答案，但无法确定覆盖的旧笔迹下是否另有数字"])
    choice = review(label=label, resolution="visual_resolution", answer_text=text,
                    visual_evidence="旧字已明确划去，横线上方当前有效字迹完整清楚，新答案与旧字归属可区分。")
    row = pipeline([a], [b], {"items": [choice]}).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "correct"
    assert row["assessment"]["verification"]["status"] == "reviewed"
    assert row["uncertainties"] == []
    assert row["answer_text"] == text


def test_many_source_uncertainties_are_bounded_publicly_without_losing_private_audit_or_clear_items():
    first = [correct("1", uncertainties=[f"主读疑点{i}" for i in range(19)]), correct("2")]
    second = [correct("1", uncertainties=[f"复读疑点{i}" for i in range(19)]), correct("2")]
    unresolved = review(resolution="unresolved", answer_clear=False, unresolved_cause="visual_unreadable",
                        remaining_uncertainties=["复核仍无法读清"])
    result = pipeline(first, second, {"items": [unresolved]}).recognize(images=(picture(),))
    a, b = result.data["items"]
    assert len(a["uncertainties"]) == 20
    assert len(a["assessment"]["uncertainties"]) == 20
    assert len(set(a["uncertainties"])) == 20
    assert a["uncertainties"][0] == a["assessment"]["basis_summary"]
    assert "复核仍无法读清" in a["uncertainties"]
    assert b["assessment"]["verdict"] == "correct" and b["uncertainties"] == []
    calls = result.data["multimodel_audit"]["calls"]
    assert calls[0]["items"][0]["uncertainties"] == first[0]["uncertainties"]
    assert calls[1]["items"][0]["uncertainties"] == second[0]["uncertainties"]
    assert calls[2]["items"][0]["remaining_uncertainties"] == ["复核仍无法读清"]
    assert calls[2]["applications"][0]["original_reason_codes"] == ["uncertain_observation"]
    # The same cap must hold before any optional review, not only afterwards.
    direct = TwoModelWholePagePipeline(FakeClient("deepseek", {"items": first}),
                                      FakeClient("astra", {"items": second}))
    initial = direct.recognize(images=(picture(),)).data["items"][0]
    assert len(initial["uncertainties"]) == len(initial["assessment"]["uncertainties"]) == 20
