"""Selective blind vision gate for material handwriting disagreements."""

import json
from threading import Barrier

import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, item as base_item, picture


def item(*args, **kwargs):
    return {"options": [], **base_item(*args, **kwargs)}
from xueban_backend.providers.ark import Attempt, ProviderError
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def setup(*, observation=None, review_output=None):
    primary = correct(answer_text="2a+10")
    secondary = correct(answer_text="12x", verdict="incorrect", correct_answer="2a+10")
    adjudication = review(source=secondary, resolution="visual_resolution", selected_source="secondary", answer_text="12x",
        verdict="incorrect", correct_answer="2a+10", correction_method="写上月收入的2倍再加10元。",
        visual_evidence="空格内依次可见1、2和交叉的x，没有加号。")
    return TwoModelWholePagePipeline(
        FakeClient("deepseek", {"items": [primary]}), FakeClient("glm", {"items": [secondary]}),
        reconciliation_client=FakeClient("reconciler", {"items": [adjudication]} if review_output is None else review_output),
        visual_client=FakeClient("astra", {"items": [item(answer_text="12x")]} if observation is None else observation),
    )


def test_blind_visual_read_runs_with_reconciliation_and_can_confirm_only_an_archived_answer():
    flow = setup()
    barrier = Barrier(2)
    flow.reconciliation_client.barrier = flow.visual_client.barrier = barrier
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "incorrect"
    assert row["answer_text"] == "12x"
    assert row["assessment"]["verification"]["status"] == "reviewed"
    assert row["assessment"]["verification"]["reason_codes"] == []
    assert row["assessment"]["verification"]["independent_judgments"] == 2
    request = flow.visual_client.calls[0]
    assert request["role"] == "recognition" and request["thinking"] is True
    assert request["images"] == (picture().data_url,)
    assert json.loads(request["text"]) == {"asset_id": "asset_original", "task_input": {"labels": ["1"]}}
    assert "12x" not in request["text"] and "2a+10" not in request["text"]
    assert len(flow.reconciliation_client.calls) == len(flow.visual_client.calls) == 1
    calls = result.data["multimodel_audit"]["calls"]
    assert [call["stage"] for call in calls] == ["primary", "secondary", "reconciliation", "visual_readback"]
    application = calls[2]["applications"][0]
    assert application["resolved_source"] == "secondary"
    assert application["visual_readback_required"] is True
    assert calls[2]["items"][0]["selected_source"] == "secondary"  # exact source pointer is kept
    assert "verdict" not in calls[3]["items"][0]
    assert len(result.metadata.attempts) == 4


@pytest.mark.parametrize("observation,reason", [
    (item(answer_text="36+[不清]+9\n=81    36", answer_status="unclear",
          uncertainties=["中间加数遮住，下方36位置对应不明"]), "visual_reading_unclear"),
    (item(answer_text="12x", stem_text="当x=[看不清]时，求值"), "visual_reading_unclear"),
    (item(answer_text="2a+10"), "independent_answer_disagreement"),
    (item(answer_text="12x", stem_text=None), "visual_question_missing"),
])
def test_reviewer_clear_claim_cannot_override_blind_readback(observation, reason):
    result = setup(observation={"items": [observation]}).recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["assessment"]["knowledge_points"] == []
    application = result.data["multimodel_audit"]["calls"][2]["applications"][0]
    assert application["rejection_code"] == reason
    if reason == "visual_reading_unclear":
        assert "visual_unreadable" in row["assessment"]["verification"]["reason_codes"]


def test_visual_failure_is_billed_audited_and_does_not_become_image_blur():
    flow = setup(observation=ProviderError("deadline_exceeded", attempts=(Attempt(1, 200, "deadline_exceeded", 20),)))
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert "reconciliation_unavailable" in row["assessment"]["verification"]["reason_codes"]
    assert "visual_unreadable" not in row["assessment"]["verification"]["reason_codes"]
    assert result.metadata.attempts[-1].code == "deadline_exceeded"
    assert result.metadata.usage is None
    assert len(flow.visual_client.calls) == 1


def test_typographic_judgment_conflict_does_not_request_visual_and_failure_is_local():
    flow = setup(observation=ProviderError("deadline_exceeded"))
    flow.client.output["items"].append(correct("2"))
    flow.review_client.output["items"].append(correct("2", stem_text="计算(-2)^2", verdict="incorrect"))
    flow.reconciliation_client.output["items"].append(review(label="2"))
    rows = flow.recognize(images=(picture(),)).data["items"]
    assert json.loads(flow.visual_client.calls[0]["text"])["task_input"]["labels"] == ["1"]
    assert rows[0]["assessment"]["verdict"] == "unable_to_judge"
    assert rows[1]["assessment"]["verdict"] == "correct"
    assert rows[1]["assessment"]["verification"]["status"] == "reviewed"


def test_same_observation_judgment_conflict_never_calls_visual_model():
    flow = setup()
    flow.client.output = {"items": [correct()]}
    flow.review_client.output = {"items": [correct(stem_text="计算(-2)^2", verdict="incorrect")]}
    flow.reconciliation_client.output = {"items": [review()]}
    row = flow.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "correct"
    assert flow.visual_client.calls == []


@pytest.mark.parametrize("observation", [
    {"items": []}, {"items": [item("other", answer_text="12x")]},
    {"items": [item(answer_text="12x"), item(answer_text="12x")]},
])
def test_missing_or_unbound_visual_items_cannot_release_target(observation):
    row = setup(observation=observation).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"


def test_visual_model_cannot_echo_other_provider_secret():
    flow = setup()
    flow.visual_client.output["items"][0]["stem_text"] = flow.client.settings.api_key
    result = flow.recognize(images=(picture(),))
    assert flow.client.settings.api_key not in str(result.data)
    assert result.metadata.attempts[-1].code == "provider_credential_echo"


def test_clear_visual_stem_can_be_paraphrased_and_math_typography_is_not_arithmetic():
    flow = setup(observation={"items": [item(stem_text="该题要求写本月收入", answer_text="12x")]})
    source = flow.visual_client.output["items"][0]
    first = flow.reconciliation_client.output
    def adjudicate(request):
        target = json.loads(request["text"])["task_input"]["items"][0]
        if "visual" not in target:
            return first
        return {"items": [review(source=source, selected_source="visual", resolution="visual_resolution",
                                verdict="incorrect", correct_answer="2a+10", correction_method="列式。")]}
    flow.reconciliation_client.output = adjudicate
    assert flow.recognize(images=(picture(),)).data["items"][0]["assessment"]["verdict"] == "incorrect"
    assert len(flow.reconciliation_client.calls) == 2
    flow = setup(observation={"items": [item(answer_text="6x+6x")]})
    assert flow.recognize(images=(picture(),)).data["items"][0]["assessment"]["verdict"] == "unable_to_judge"


def test_final_81_and_confident_review_do_not_override_independent_unreadable_middle_step():
    primary = correct("5#1", answer_text="36+36+9=81")
    secondary = correct("5#1", answer_text="36+[不清]+9\n=81    36",
                        answer_status="unclear", verdict="unable_to_judge",
                        uncertainties=["中间加数遮住，下方36与该处对应不明"])
    confident = review(source=primary, label="5#1", resolution="visual_resolution", answer_text="36+36+9=81",
                       visual_evidence="第二个36可辨，末行81清楚。")
    flow = TwoModelWholePagePipeline(
        FakeClient("deepseek", {"items": [primary]}), FakeClient("glm", {"items": [secondary]}),
        reconciliation_client=FakeClient("reconciler", {"items": [confident]}),
        visual_client=FakeClient("astra", {"items": [item("5#1", answer_text=secondary["answer_text"],
            answer_status="unclear", uncertainties=["原图第三行遮住，下方另写36，不能移入第三行"])]}))
    row = flow.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert "visual_unreadable" in row["assessment"]["verification"]["reason_codes"]
    assert row["assessment"]["knowledge_points"] == []


def test_independent_unicode_half_matches_written_fraction_coefficient_without_arithmetic():
    primary = correct(answer_text="1/2ah")
    secondary = correct(answer_text="1/2 ah", answer_status="unclear",
                        verdict="unable_to_judge", uncertainties=["原读未确认当前笔迹"])
    flow = TwoModelWholePagePipeline(
        FakeClient("deepseek", {"items": [primary]}), FakeClient("glm", {"items": [secondary]}),
        reconciliation_client=FakeClient("reconciler", {"items": [review(
            resolution="visual_resolution", answer_text="1/2ah",
            visual_evidence="当前可见分数二分之一，右侧依次是a、h，字迹可读。")]}),
        visual_client=FakeClient("astra", {"items": [item(answer_text="½ah")]}))
    row = flow.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "correct"
    assert row["answer_text"] == "1/2ah"
    assert row["assessment"]["verification"]["status"] == "reviewed"


def test_same_wrong_pinyin_read_is_rechecked_and_can_use_archived_blind_options():
    """P25 pattern: both judges autocorrect printed tones, then accept C."""
    first = correct(subject="语文", question_type="single_choice",
                    stem_text="下列加点字注音有误的一项是", answer_text="C",
                    options=[{"label": "C", "text": "翘首(qiào)"},
                             {"label": "D", "text": "深恶痛疾(wù)"}])
    blind = item(stem_text=first["stem_text"], answer_text="C",
                 options=[{"label": "C", "text": "翘首(qiáo)"},
                          {"label": "D", "text": "深恶痛疾(wū)"}])
    def adjudicate(request):
        target = json.loads(request["text"])["task_input"]["items"][0]
        if "visual" not in target:
            return {"items": [review(source=first)]}
        assert target["visual"] == blind
        return {"items": [review(source=blind, selected_source="visual",
            resolution="visual_resolution", verdict="incorrect",
            correct_answer="D", correction_method="深恶痛疾的恶读wù，图上D印的是wū。",
            visual_evidence="C的翘为二声，D的恶上方是平直的一声横线。")]}
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}),
        FakeClient("b", lambda request: {"items": [blind if request["role"] == "recognition" else first]}),
        reconciliation_client=FakeClient("reviewer", adjudicate),
        visual_client=FakeClient("reader", {"items": [blind]}))
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "incorrect"
    assert row["assessment"]["correct_answer"] == "D"
    assert row["answer_text"] == "C" and row["options"] == blind["options"]
    calls = result.data["multimodel_audit"]["calls"]
    assert [call["stage"] for call in calls] == [
        "primary", "secondary", "reconciliation", "visual_readback", "critical_readback", "visual_reconciliation"]
    assert calls[0]["items"][0]["options"] == first["options"]
    assert calls[2]["applications"][0]["rejection_code"] == "independent_condition_disagreement"
    assert calls[-1]["applications"][0]["resolved_source"] == "visual"
    assert len(flow.visual_client.calls) == 1 and len(flow.reconciliation_client.calls) == 2
    assert "C" not in json.loads(flow.visual_client.calls[0]["text"])["task_input"]
    assert len(flow.review_client.calls) == 2
    critical_request = flow.review_client.calls[1]
    assert critical_request["role"] == "recognition"
    assert json.loads(critical_request["text"])["task_input"]["labels"] == ["1"]
    assert "qiáo" not in critical_request["text"] and "wū" not in critical_request["text"]


@pytest.mark.parametrize("other_change,code", [
    ({"stem_text": "选注音正确的一项"}, "critical_condition_disagreement"),
    ({"options": [{"label": "D", "text": "深恶痛疾(wù)"}]}, "critical_print_disagreement"),
    ({"answer_text": "A"}, "critical_answer_disagreement"),
    ({"uncertainties": ["手写字母叠有批改叉"], "answer_status": "unclear"}, "critical_readback_unclear"),
])
def test_single_blind_reader_cannot_release_conflicting_tone_or_teacher_mark(other_change, code):
    first = correct(subject="语文", question_type="single_choice",
                    stem_text="选注音有误的一项", answer_text="C",
                    options=[{"label": "D", "text": "深恶痛疾(wū)"}])
    blind = item(stem_text=first["stem_text"], answer_text="C", options=first["options"])
    other = {**blind, **other_change}
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}),
        FakeClient("b", lambda request: {"items": [other if request["role"] == "recognition" else first]}),
        reconciliation_client=FakeClient("reviewer", {"items": [review(source=first)]}),
        visual_client=FakeClient("reader", {"items": [blind]}))
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["assessment"]["knowledge_points"] == []
    assert result.data["multimodel_audit"]["calls"][2]["applications"][0]["rejection_code"] == code
    assert len(flow.visual_client.calls) == 1 and len(flow.review_client.calls) == 2


def test_critical_readbacks_accept_only_typographic_stem_changes():
    first = correct(subject="语文", question_type="single_choice",
                    stem_text="下列注音有误的一项是（ ）", answer_text="C",
                    options=[{"label": "D", "text": "深恶痛疾(wū)"}])
    blind = item(stem_text=first["stem_text"], answer_text="C", options=first["options"])
    other = {**blind, "stem_text": "下列注音有误的一项是()"}
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}),
        FakeClient("b", lambda request: {"items": [other if request["role"] == "recognition" else first]}),
        reconciliation_client=FakeClient("reviewer", {"items": [review(source=first)]}),
        visual_client=FakeClient("reader", {"items": [blind]}))
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "ready"


def test_pending_display_uses_archived_unclear_reading_not_completed_judge_arithmetic():
    first = correct("5#1", answer_text="36+36+9=81")
    second = correct("5#1", answer_text="36+[不清]+9\n=81    36",
                     answer_status="unclear", verdict="unable_to_judge",
                     uncertainties=["过程中的加数不清"])
    blind = item("5#1", stem_text=first["stem_text"], answer_text="36+[不清]+9\n下方另写36",
                 answer_status="unclear", uncertainties=["中间被遮住"])
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}), FakeClient("b", {"items": [second]}),
        reconciliation_client=FakeClient("reviewer", {"items": [
            review(source=first, label="5#1", resolution="visual_resolution")]}),
        visual_client=FakeClient("reader", {"items": [blind]}))
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["answer_text"] == blind["answer_text"]
    assert row["answer_status"] == "unclear"
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["assessment"]["knowledge_points"] == []
    calls = result.data["multimodel_audit"]["calls"]
    assert calls[0]["items"][0]["answer_text"] == "36+36+9=81"
    assert calls[3]["items"][0] == blind
    assert calls[2]["applications"][0]["pending_display_source_id"].startswith("visual:")


def test_unclear_reader_of_a_different_question_withholds_unreliable_pending_transcript():
    flow = setup(observation={"items": [item(stem_text="另一题：计算3×9", answer_text="[不清]",
                                           answer_status="unclear", uncertainties=["不清"])]})
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["answer_text"] is None and row["answer_status"] == "unclear"
    assert row["stem_text"] == "计算(-2)²。"
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["knowledge_points"] == []
    application = result.data["multimodel_audit"]["calls"][2]["applications"][0]
    assert application["pending_display_action"] == "withhold_unbound_transcription"
    assert "pending_display_source_id" not in application


def test_shared_two_target_context_does_not_keep_invented_middle_value_public():
    """P07: single target vs both expressions plus a first-target annotation."""
    first = correct("5#1", stem_text="当a=-6，b=3时，求代数式a^2-2ab+b^2的值",
                    answer_text="(-6)^2-2×(-6)×3+3^2\n=36+36+9\n=81")
    second = {**first, "uncertainties": ["中间涂改未确认"]}
    blind = item("5#1",
                 stem_text="当a=−6，b=3时，求代数式a²−2ab+b²与(a−b)²的值。（第一项：a²−2ab+b²）",
                 answer_text="=36+[不清]+9\n=81\n（下方另写36）", answer_status="unclear",
                 uncertainties=["中间数字被遮住"])
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}), FakeClient("b", {"items": [second]}),
        reconciliation_client=FakeClient("reviewer", {"items": [
            review(source=first, label="5#1", resolution="visual_resolution")]}),
        visual_client=FakeClient("reader", {"items": [blind]}))
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["stem_text"] == first["stem_text"]
    assert row["answer_text"] is None and row["answer_status"] == "unclear"
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["knowledge_points"] == []
    calls = result.data["multimodel_audit"]["calls"]
    assert calls[0]["items"][0] == first
    assert calls[3]["items"][0] == blind
    assert len(calls) == 4  # unclear reading does not start another paid review


def test_visual_condition_change_drops_old_judges_knowledge_tags():
    first = correct(stem_text="计算2^3", answer_text="5", verdict="incorrect", correct_answer="8")
    second = {**first, "stem_text": "计算2^2", "correct_answer": "4"}
    blind = item(stem_text="计算2×3", answer_text="5")
    def respond(request):
        target = json.loads(request["text"])["task_input"]["items"][0]
        if "visual" not in target:
            return {"items": [review(source=first, resolution="visual_resolution",
                                    verdict="incorrect", correct_answer="8", correction_method="算乘方。")]}
        return {"items": [review(source=blind, selected_source="visual", resolution="visual_resolution",
                                verdict="incorrect", correct_answer="6", correction_method="计算两个数的乘积。")]}
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}), FakeClient("b", {"items": [second]}),
        reconciliation_client=FakeClient("reviewer", respond),
        visual_client=FakeClient("reader", {"items": [blind]}))
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["stem_text"] == "计算2×3"
    assert row["assessment"]["verdict"] == "incorrect"
    assert row["assessment"]["correct_answer"] == "6"
    assert row["assessment"]["knowledge_points"] == []
    assert result.data["multimodel_audit"]["calls"][-1]["applications"][0]["knowledge_tags_retained"] is False


def test_same_conditions_keep_agreeing_knowledge_tags_after_visual_answer_review():
    row = setup().recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["knowledge_points"] == ["有理数的乘方"]


def test_exact_same_observation_can_be_rejudged_without_requiring_same_original_verdict():
    a, b = correct(answer_text="4"), correct(answer_text="4", verdict="incorrect")
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [a]}), FakeClient("b", {"items": [b]}),
        reconciliation_client=FakeClient("reviewer", {"items": [review(source=a)]}))
    row = flow.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "correct"
    assert row["assessment"]["verification"]["status"] == "reviewed"
    assert len(flow.reconciliation_client.calls) == 1
