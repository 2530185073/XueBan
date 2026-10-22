"""Commercial two-model correction-policy integration tests.

These tests intentionally exercise the existing MockTransport helper. The
contract is bounded to DS plus one independent GPT fallback: there is no
tertiary reviewer, review round, or adjudication stage.
"""

import json

import httpx
import pytest

from test_correction_pipeline import checked, context, pipeline
from xueban_backend.curriculum_catalog import scope_for_subject, scope_payload
from xueban_backend.providers import ProviderError
from xueban_backend.providers.correction_pipeline import CorrectionPipeline


DS_MODEL = "deepseek-v4-1-flash-260910"
GPT_MODEL = "gpt-6-astra"


def _unable(marker):
    return checked(
        verdict="unable_to_judge",
        reason_code="needs_staff",
        correct_answer=None,
        correction_method=None,
        basis_summary=marker,
    )


def _correct(marker, *, answer="4", uncertainties=None):
    output = checked(
        verdict="correct",
        reason_code=None,
        correct_answer=None,
        correction_method=None,
        basis_summary=marker,
        uncertainties=list(uncertainties or []),
    )
    output["readback"]["answer_text"] = answer
    return output


def _wrong(marker, *, answer="4"):
    return checked(
        verdict="incorrect",
        reason_code=None,
        correct_answer=answer,
        correction_method="先按运算顺序计算，再核对负号；正确答案为4。",
        basis_summary=marker,
    )


def _incomplete_wrong(marker):
    return checked(
        verdict="incorrect",
        reason_code=None,
        correct_answer=None,
        correction_method=None,
        basis_summary=marker,
    )


def _chain(ds_responses, gpt_responses, *, ds_requests=None, gpt_requests=None):
    primary = pipeline(ds_responses, ds_requests, model=DS_MODEL)
    fallback = pipeline(gpt_responses, gpt_requests, model=GPT_MODEL)
    return CorrectionPipeline(primary.client, fallback.client)


def _task(request):
    return json.loads(request["messages"][1]["content"][0]["text"])["task_input"]


def _attempt_models(result):
    return [attempt.model for attempt in result.metadata.attempts]


def _schema(request):
    return json.loads(request["messages"][0]["content"].rsplit("\nJSON Schema:\n", 1)[1])


def _assert_two_attempts(result, *, first_code="ok", second_code="ok"):
    assert _attempt_models(result) == [DS_MODEL, GPT_MODEL]
    assert [attempt.code for attempt in result.metadata.attempts] == [first_code, second_code]


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_ds_correct_requires_gpt_and_only_two_model_attempts(role):
    ctx = context()
    ctx["attempt"]["answer_text"] = "4"
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_correct("DS_CORRECT", answer="4")],
        [_correct("GPT_INDEPENDENT_CORRECT", answer="4")],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = getattr(flow, role)(**ctx)

    assert result.data["verdict"] == "correct"
    assert result.data["validity"] == "candidate"
    assert len(ds_requests) == len(gpt_requests) == 1
    _assert_two_attempts(result)


@pytest.mark.parametrize("primary", [httpx.Response(503), _unable("DS_UNABLE")])
def test_gpt_correct_alone_is_not_two_valid_correct_judgments(primary):
    ctx = context()
    ctx["attempt"]["answer_text"] = "4"
    with _chain([primary], [_correct("GPT_ONLY_CORRECT")]) as flow:
        result = flow.check(**ctx)
    assert result.data["verdict"] == "unable_to_judge"
    assert result.data["validity"] == "needs_review"
    assert result.data["correct_answer"] is None
    assert result.data["correction_method"] is None
    assert len(result.metadata.attempts) == 2


def test_original_transcription_ambiguity_reaches_both_checks_and_blocks_correct():
    ctx = context()
    ctx["attempt"]["answer_text"] = "4"
    warning = "负号看不清，可能是4，也可能是-4。"
    ctx["attempt"]["recognition_uncertainties"] = [warning]
    primary_requests, secondary_requests = [], []
    with _chain([_correct("DS")], [_correct("GPT")],
                ds_requests=primary_requests, gpt_requests=secondary_requests) as flow:
        result = flow.check(**ctx)
    assert _task(primary_requests[0])["recognition_uncertainties"] == [warning]
    assert _task(secondary_requests[0])["recognition_uncertainties"] == [warning]
    assert result.data["verdict"] == "unable_to_judge"
    assert warning in result.data["uncertainties"]
    assert result.data["evidence_verified"] is False


def test_ds_unable_gpt_unable_stays_unable_without_correction_or_slot_result():
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_unable("DS_UNABLE")], [_unable("GPT_UNABLE")],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = flow.check(**context())

    data = result.data
    assert data["verdict"] == "unable_to_judge"
    assert data["validity"] == "needs_review"
    assert data["correct_answer"] is None
    assert data["correction_method"] is None
    assert "answer_checks" not in data
    assert len(ds_requests) == len(gpt_requests) == 1
    _assert_two_attempts(result)
    assert "DS_UNABLE" in json.dumps(_task(gpt_requests[0]), ensure_ascii=False)


def test_gpt_definitive_wrong_with_answer_and_method_completes_without_third_stage():
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_unable("DS_GATE")], [_wrong("GPT_DEFINITIVE_WRONG")],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = flow.check(**context())

    data = result.data
    assert data["verdict"] == "incorrect"
    assert data["validity"] == "candidate"
    assert data["correct_answer"] == "4"
    assert data["correction_method"]
    assert "adjudication" not in data
    assert len(ds_requests) == len(gpt_requests) == 1
    _assert_two_attempts(result)


def test_ds_wrong_gpt_correct_conflict_becomes_unable_not_false_green():
    ctx = context()
    # Force the independent GPT pass while keeping the answer itself simple.
    ctx["question"]["question_type"] = "application"
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_wrong("DS_WRONG")], [_correct("GPT_CORRECT", answer="4")],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = flow.check(**ctx)

    data = result.data
    assert data["verdict"] == "unable_to_judge"
    assert data["validity"] == "needs_review"
    assert data["reason_code"] == "verification_conflict"
    assert data["correct_answer"] is None
    assert data["correction_method"] is None
    assert any(any(word in note for word in ("不一致", "分歧", "冲突"))
               for note in data["uncertainties"])
    _assert_two_attempts(result)


def test_ds_correct_gpt_wrong_preserves_wrong_answer_and_marks_conflict():
    ctx = context()
    ctx["attempt"]["answer_text"] = "4"
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_correct("DS_CORRECT", answer="4")], [_wrong("GPT_WRONG")],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = flow.check(**ctx)

    data = result.data
    assert data["verdict"] == "incorrect"
    assert data["validity"] == "candidate"
    assert data["correct_answer"] == "4"
    assert data["correction_method"]
    assert data["reason_code"] == "verification_conflict"
    assert data["uncertainties"]
    _assert_two_attempts(result)


def test_correct_with_uncertainty_is_not_green_without_clean_gpt_confirmation():
    ctx = context()
    ctx["attempt"]["answer_text"] = "4"
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_correct("DS_CORRECT_WITH_WARNING", answer="4", uncertainties=["手写符号可能有歧义。"])],
        [_unable("GPT_CANNOT_CONFIRM")],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = flow.check(**ctx)

    data = result.data
    assert data["verdict"] == "unable_to_judge"
    assert data["validity"] == "needs_review"
    assert data["correct_answer"] is None
    assert data["correction_method"] is None
    _assert_two_attempts(result)


def test_gpt_not_actually_completed_finishes_unresolved_without_false_red():
    ctx = context()
    ctx["attempt"]["answer_text"] = "4"
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_correct("DS_CORRECT", answer="4")], [httpx.Response(503)],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = flow.check(**ctx)

    assert result.data["verdict"] == "unable_to_judge"
    assert result.data["validity"] == "needs_review"
    assert result.data["correct_answer"] is None
    assert result.data["correction_method"] is None
    assert "不计为答错" in result.data["uncertainties"][0]
    assert len(ds_requests) == len(gpt_requests) == 1
    assert [attempt.model for attempt in result.metadata.attempts] == [DS_MODEL, None]
    assert [attempt.code for attempt in result.metadata.attempts] == ["ok", "upstream_unavailable"]


def test_incorrect_without_answer_or_method_becomes_unable_after_gpt():
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_incomplete_wrong("DS_MISSING_CORRECTION")],
        [_incomplete_wrong("GPT_MISSING_CORRECTION")],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = flow.check(**context())

    data = result.data
    assert data["verdict"] == "unable_to_judge"
    assert data["validity"] == "needs_review"
    assert data["correct_answer"] is None
    assert data["correction_method"] is None
    assert _attempt_models(result) == [DS_MODEL, GPT_MODEL]


def test_ds_slot_binding_failure_runs_gpt_and_preserves_invalid_attempt_audit():
    ctx = context()
    ctx["attempt"]["answer_slots"] = [{
        "index": 1, "prompt": "计算(-2)²", "answer_text": "-4", "answer_status": "present",
    }]
    ds_output = _wrong("DS_SLOT_BINDING_INVALID")
    ds_output["answer_checks"] = [{
        "index": 1, "prompt": "计算(-2)²", "student_answer": "not-the-saved-answer",
        "verdict": "incorrect", "correct_answer": "4", "explanation": "绑定错误。",
    }]
    gpt_output = _wrong("GPT_AFTER_DS_SLOT_FAILURE")
    gpt_output["answer_checks"] = [{
        "index": 1, "prompt": "计算(-2)²", "student_answer": "-4",
        "verdict": "incorrect", "correct_answer": "4", "explanation": "平方结果应为4。",
    }]
    ds_requests, gpt_requests = [], []
    flow = _chain([ds_output], [gpt_output], ds_requests=ds_requests, gpt_requests=gpt_requests)
    with flow:
        result = flow.check(**ctx)

    assert result.data["verdict"] == "incorrect"
    assert result.data["correct_answer"] == "4"
    assert len(ds_requests) == len(gpt_requests) == 1
    _assert_two_attempts(result, first_code="invalid_output")
    assert result.metadata.attempts[0].diagnostic.rule == "slot_binding"
    assert "not-the-saved-answer" not in json.dumps(gpt_requests[0], ensure_ascii=False)


def test_gpt_slot_binding_failure_raises_two_audits_without_repeating_either_request():
    ctx = context()
    ctx["attempt"]["answer_slots"] = [{
        "index": 1, "prompt": "计算(-2)²", "answer_text": "-4", "answer_status": "present",
    }]
    ds_output = _unable("DS_UNABLE_WITH_SLOT")
    ds_output["answer_checks"] = [{
        "index": 1, "prompt": "计算(-2)²", "student_answer": "-4",
        "verdict": "unable_to_judge", "correct_answer": None, "explanation": "无法确认。",
    }]
    gpt_output = _wrong("GPT_SLOT_BINDING_INVALID")
    gpt_output["answer_checks"] = [{
        "index": 1, "prompt": "计算(-2)²", "student_answer": "different-answer",
        "verdict": "incorrect", "correct_answer": "4", "explanation": "绑定错误。",
    }]
    ds_requests, gpt_requests = [], []
    flow = _chain([ds_output], [gpt_output], ds_requests=ds_requests, gpt_requests=gpt_requests)
    with flow, pytest.raises(ProviderError, match="^invalid_output$") as failure:
        flow.check(**ctx)

    assert len(ds_requests) == len(gpt_requests) == 1
    assert [attempt.model for attempt in failure.value.attempts] == [DS_MODEL, GPT_MODEL]
    assert [attempt.code for attempt in failure.value.attempts] == ["ok", "invalid_output"]
    assert failure.value.attempts[1].diagnostic.rule == "slot_binding"


def test_from_env_ignores_legacy_review_configuration_and_builds_only_two_clients():
    with CorrectionPipeline.from_env({
        "ARK_BASE_URL": "https://synthetic.example.invalid/api/v3",
        "ARK_API_KEY": "synthetic-primary-key",
        "ARK_MODEL": DS_MODEL,
        "ARK_FALLBACK_MODEL": GPT_MODEL,
        "ARK_REVIEW_MODEL": "legacy-kimi-model",
        "ARK_REVIEW_PROTOCOL": "invalid-protocol-that-must-be-ignored",
        "ARK_REVIEW_BASE_URL": "https://legacy-review.invalid",
        "ARK_REVIEW_API_KEY": "legacy-review-key",
    }) as flow:
        assert flow.client.settings.model == DS_MODEL
        assert flow.fallback_client.settings.model == GPT_MODEL
        assert flow.fallback_client.settings.base_url == flow.client.settings.base_url
        assert not getattr(flow, "review_client", None)


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_known_curriculum_scope_with_empty_candidates_is_kept_in_both_model_requests(role):
    scope = scope_payload(scope_for_subject(6, "数学"))
    curriculum = {"scope": scope, "status": "available", "revision_policy": "publisher_latest"}
    ctx = context()
    ctx.update(student_grade=6, knowledge_point_candidates=[], curriculum_context=curriculum)
    ds_requests, gpt_requests = [], []
    flow = _chain(
        [_unable("DS_UNABLE")], [_unable("GPT_UNABLE")],
        ds_requests=ds_requests, gpt_requests=gpt_requests,
    )
    with flow:
        result = getattr(flow, role)(**ctx)

    assert result.data["verdict"] == "unable_to_judge"
    assert len(ds_requests) == len(gpt_requests) == 1
    for request in ds_requests + gpt_requests:
        task = _task(request)
        learning = task["student_learning_context"]
        assert learning["grade"] == 6
        assert learning["knowledge_point_candidates"] == []
        assert learning["curriculum"] == curriculum
        assert _schema(request)["properties"]["knowledge_points"]["maxItems"] == 0
