"""Selective blind vision gate for material handwriting disagreements."""

import json
from threading import Barrier

import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, item, picture
from xueban_backend.providers.ark import Attempt, ProviderError
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def setup(*, observation=None, review_output=None):
    primary = correct(answer_text="2a+10")
    secondary = correct(answer_text="12x", verdict="incorrect", correct_answer="2a+10")
    adjudication = review(resolution="visual_resolution", selected_source="primary", answer_text="12x",
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
    assert calls[2]["items"][0]["selected_source"] == "primary"  # raw response is kept
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


def test_visual_model_does_not_receive_semantic_only_targets_and_failure_is_local():
    flow = setup(observation=ProviderError("deadline_exceeded"))
    flow.client.output["items"].append(correct("2"))
    flow.review_client.output["items"].append(correct("2", stem_text="求(-2)²的值"))
    flow.reconciliation_client.output["items"].append(review(label="2"))
    rows = flow.recognize(images=(picture(),)).data["items"]
    assert json.loads(flow.visual_client.calls[0]["text"])["task_input"]["labels"] == ["1"]
    assert rows[0]["assessment"]["verdict"] == "unable_to_judge"
    assert rows[1]["assessment"]["verdict"] == "correct"
    assert rows[1]["assessment"]["verification"]["status"] == "reviewed"


def test_semantic_only_conflict_never_calls_visual_model():
    flow = setup()
    flow.client.output = {"items": [correct()]}
    flow.review_client.output = {"items": [correct(stem_text="求(-2)²的值")]}
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
    assert flow.recognize(images=(picture(),)).data["items"][0]["assessment"]["verdict"] == "incorrect"
    flow = setup(observation={"items": [item(answer_text="6x+6x")]})
    assert flow.recognize(images=(picture(),)).data["items"][0]["assessment"]["verdict"] == "unable_to_judge"


def test_final_81_and_confident_review_do_not_override_independent_unreadable_middle_step():
    primary = correct("5#1", answer_text="36+36+9=81")
    secondary = correct("5#1", answer_text="36+[不清]+9\n=81    36",
                        answer_status="unclear", verdict="unable_to_judge",
                        uncertainties=["中间加数遮住，下方36与该处对应不明"])
    confident = review(label="5#1", resolution="visual_resolution", answer_text="36+36+9=81",
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
