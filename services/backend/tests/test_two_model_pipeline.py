"""Offline safety and orchestration regressions for two blind page judges."""

from copy import deepcopy
import json
from threading import Barrier

import pytest

from test_whole_page_pipeline import FakeClient, check_context, judged, picture
from xueban_backend.providers.ark import Attempt, ProviderError
from xueban_backend.providers.correction_pipeline import CorrectionPipeline
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def answer(label="1", **changes):
    return judged(label, **{"subject": "数学", "question_type": "calculation", "options": [],
                  "knowledge_points": ["有理数的乘方"], "correct_answer": "4",
                  "correction_method": "负数的平方是正数，先计算括号内数的平方。", **changes})


def correct(label="1", **changes):
    return answer(label, **{"verdict": "correct", "answer_text": "4", **changes})


def flow(first=None, second=None, *, barrier=None):
    return TwoModelWholePagePipeline(
        FakeClient("deepseek", {"items": first if first is not None else [answer()]}, barrier=barrier),
        FakeClient("astra", {"items": second if second is not None else [answer()]}, barrier=barrier))


def test_two_blind_whole_page_judges_run_in_parallel_and_emit_correction():
    pipeline = flow(barrier=Barrier(2))
    result = pipeline.recognize(images=(picture(),))
    assessment = result.data["items"][0]["assessment"]
    assert assessment["verdict"] == "incorrect"
    assert assessment["correct_answer"] == "4" and assessment["correction_method"]
    assert assessment["knowledge_points"] == ["有理数的乘方"]
    assert assessment["verification"]["mode"] == "two_model"
    assert assessment["verification"]["independent_judgments"] == 2
    assert len(result.metadata.attempts) == 2
    for provider in (pipeline.client, pipeline.review_client):
        request = provider.calls[0]
        assert request["images"] == (picture().data_url,)
        assert json.loads(request["text"])["task_input"] == {}
        assert "INTERNAL_STANDARD_ANSWER" not in request["text"]
        assert "-4" not in request["text"]
        assert request["role"] == "check"
        assert len(provider.calls) == 1
    assert result.data["multimodel_audit"]["independent_before_comparison"] is True


@pytest.mark.parametrize("secondary", [
    correct(answer_text="-4"), correct(uncertainties=["负号看不清"]),
    correct(verdict="unable_to_judge"), correct(verdict="incorrect"),
    correct(stem_text="计算2²"), correct(answer_status="unclear"),
])
def test_correct_requires_matching_reading_and_two_clean_correct_judgments(secondary):
    row = flow([correct()], [secondary]).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["knowledge_points"] == []
    assert row["assessment"]["correct_answer"] is None
    assert row["assessment"]["correction_method"] is None


def test_two_clean_corrects_release_only_provisional_correct():
    row = flow([correct()], [correct()]).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "correct"
    assert row["assessment"]["verification"]["status"] == "matched"
    assert row["recognition_status"] == "ready"
    assert "confirmed_by" not in row["assessment"]
    assert row["assessment"]["correct_answer"] is None


def test_unknown_display_taxonomy_does_not_discard_whole_page_or_change_student_facts():
    first = correct(question_type="short_answer", guidance_code="pronunciation", reason_code="calculation_error")
    result = flow([first], [first]).recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "correct"
    assert row["question_type"] == "unknown"
    assert row["answer_text"] == first["answer_text"]
    audit = result.data["multimodel_audit"]["calls"][0]
    assert audit["category_normalizations"] == [
        {"label": "1", "field": "question_type", "from": "short_answer", "to": "unknown"},
        {"label": "1", "field": "reason_code", "from": "calculation_error", "to": "other"},
    ]
    # A judgment is not a display category and cannot receive such a fallback.
    invalid = flow([correct(verdict="probably_correct")], [correct()]).recognize(images=(picture(),))
    assert invalid.data["items"][0]["assessment"]["verdict"] == "unable_to_judge"


def test_live_page_typography_can_agree_without_weakening_mathematical_evidence():
    first = [
        correct("2", stem_text="下列赋予代数式 8a 实际意义的例子中，错误的是",
                question_type="single_choice", answer_text="B"),
        correct("3(1)#1", stem_text="若正方形的边长为 a，则正方形的面积是____", answer_text="a²"),
        correct("3(10)", stem_text="本月收入比上月的 2 倍多 10 元，本月收入____元", answer_text="12x"),
        correct("5#1", stem_text="当 a=−6，b=3 时，求 a²−2ab+b² 的值",
                answer_text="36+36+9=81", correct_answer="81"),
    ]
    second = [
        correct("2", stem_text="下列赋予代数式8a实际意义的例子中，错误的是（ ）。",
                question_type="single_choice", answer_text="B"),
        correct("3(1)#1", stem_text="若正方形的边长为a，则正方形的面积是____。", answer_text="a^2"),
        correct("3(10)", stem_text="本月收入比上月的2倍多10元，本月收入____元。", answer_text="2a+10"),
        correct("5#1", stem_text="当a＝－6，b＝3时，求a²－2ab＋b²的值。",
                answer_text="36+（涂改）+9=81"),
    ]
    rows = flow(first, second).recognize(images=(picture(),)).data["items"]
    assert [row["assessment"]["verdict"] for row in rows] == [
        "correct", "correct", "unable_to_judge", "unable_to_judge"]
    assert rows[2]["answer_text"] == "12x"
    assert rows[3]["assessment"]["verification"]["reason_codes"] == ["answer_disagreement"]


def test_same_selected_letter_does_not_override_different_printed_choice():
    base = correct(question_type="single_choice", answer_text="A",
                   stem_text="下列各式符合书写规范的是",
                   options=[{"label": "D", "text": "2½n"}])
    other = {**base, "stem_text": "下列各式符合书写规范的是（ ）。",
             "options": [{"label": "D", "text": "2½π"}]}
    row = flow([base], [other]).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["assessment"]["verification"]["reason_codes"] == ["options_disagreement"]


@pytest.mark.parametrize("failed_stage", ["primary", "secondary"])
def test_provider_failure_is_not_a_second_correct_vote_and_attempts_survive(failed_stage):
    pipeline = flow([correct()], [correct()])
    failed_client = pipeline.client if failed_stage == "primary" else pipeline.review_client
    failed_client.output = ProviderError(
        "upstream_timeout", attempts=(Attempt(1, 200, "upstream_timeout", 10),), latency_ms=10)
    result = pipeline.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["assessment"]["verification"]["status"] == "degraded"
    assert row["assessment"]["verification"]["reason_codes"] == [
        "provider_unavailable", "unmatched_label", "uncertain_observation"]
    assert row["assessment"]["knowledge_points"] == []
    assert row["recognition_status"] == "needs_revision"
    assert "有一路核对服务暂未完成，原图已保留" in row["assessment"]["basis_summary"]
    assert "有一路核对服务暂未完成，原图已保留" in row["uncertainties"][-1]
    assert "两次独立识读或判题尚未一致" not in str(row["assessment"])
    assert sorted(attempt.code for attempt in result.metadata.attempts) == ["ok", "upstream_timeout"]
    assert result.metadata.usage is None
    assert len(pipeline.review_client.calls) == 1


def test_failure_of_both_models_is_not_an_empty_success():
    pipeline = flow()
    for client in (pipeline.client, pipeline.review_client):
        client.output = ProviderError("upstream_timeout", attempts=(Attempt(1, 200, "upstream_timeout", 10),))
    with pytest.raises(ProviderError, match="multimodel_unavailable") as captured:
        pipeline.recognize(images=(picture(),))
    assert len(captured.value.attempts) == 2


def test_conflict_is_local_to_one_blank_and_reader_only_items_survive():
    first = [correct("3(1)#1"), correct("3(1)#2", answer_text="12x")]
    second = [correct("３（１）#１"), correct("3(1)#2", answer_text="2a+10"), correct("4")]
    result = flow(first, second).recognize(images=(picture(),))
    rows = result.data["items"]
    assert [row["assessment"]["verdict"] for row in rows] == ["correct", "unable_to_judge", "unable_to_judge"]
    assert "answer_disagreement" in rows[1]["assessment"]["verification"]["reason_codes"]
    assert "unmatched_label" in rows[2]["assessment"]["verification"]["reason_codes"]
    assert rows[1]["answer_text"] == "12x"
    assert result.data["multimodel_audit"]["calls"][1]["items"][1]["answer_text"] == "2a+10"


@pytest.mark.parametrize("change,reason", [
    ({"correct_answer": "5"}, "correction_disagreement"),
    ({"correct_answer": None}, "missing_correction"),
    ({"correction_method": None}, "missing_correction"),
    ({"options": [{"label": "A", "text": "另一个条件"}]}, "options_disagreement"),
])
def test_wrong_answer_requires_supported_matching_correction(change, reason):
    other = answer()
    other.update(change)
    row = flow([answer()], [other]).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert reason in row["assessment"]["verification"]["reason_codes"]
    assert row["assessment"]["correct_answer"] is None


def test_model_cannot_reclassify_unwritten_as_wrong():
    blank = answer(answer_status="blank", answer_text=None, verdict="unanswered")
    row = flow([blank], [blank]).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unanswered"
    bad = deepcopy(blank)
    bad["verdict"] = "incorrect"
    row = flow([bad], [bad]).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"


def test_same_model_or_unknown_subject_cannot_fabricate_independent_knowledge():
    pipeline = flow()
    pipeline.review_client.settings = pipeline.client.settings
    row = pipeline.recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    other = answer()
    other["subject"] = None
    row = flow([answer()], [other]).recognize(images=(picture(),)).data["items"][0]
    assert row["assessment"]["verdict"] == "incorrect"
    assert row["subject"] is None and row["assessment"]["knowledge_points"] == []


def test_multiple_pages_have_separate_source_associations_and_two_calls_each():
    result = flow().recognize(images=(picture(), picture("asset_second")))
    assert [row["asset_id"] for row in result.data["items"]] == ["asset_original", "asset_second"]
    assert len(result.metadata.attempts) == 4


def test_invalid_provider_schema_keeps_its_paid_attempt_and_other_observation():
    pipeline = flow()
    del pipeline.client.output["items"][0]["verdict"]
    result = pipeline.recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verification"]["status"] == "degraded"
    assert [attempt.code for attempt in result.metadata.attempts] == ["invalid_output", "ok"]


def test_cross_provider_credential_echo_does_not_survive_audit():
    pipeline = flow()
    pipeline.client.output["items"][0]["basis_summary"] = pipeline.review_client.settings.api_key
    result = pipeline.recognize(images=(picture(),))
    assert pipeline.review_client.settings.api_key not in str(result.data)
    assert result.metadata.attempts[0].code == "provider_credential_echo"


def test_explicit_recheck_reads_only_current_photo_and_binds_full_question():
    pipeline = flow([correct()], [correct()])
    ctx = check_context()
    ctx["images"] = (picture(), picture("asset_new"))
    ctx["attempt"].update(asset_ids=["asset_new"], answer_text="4")
    result = pipeline.recheck(**ctx, student_grade=7, knowledge_point_candidates=[])
    assert result.data["verdict"] == "correct"
    for client in (pipeline.client, pipeline.review_client):
        assert json.loads(client.calls[0]["text"])["asset_id"] == "asset_new"
    ctx["question"]["stem_text"] = "计算(-3)²。"
    assert pipeline.recheck(**ctx).data["verdict"] == "unable_to_judge"


@pytest.mark.parametrize("context", [
    {}, {"scope": None, "status": "unresolved_subject"},
    {"scope": {"grade": 7}, "status": "unsupported_scope"},
])
def test_missing_curriculum_blocks_graph_tags_but_not_basic_image_correction(context):
    pipeline = flow()
    result = pipeline.check(**check_context(), curriculum_context=context,
                            knowledge_point_candidates=["有理数的乘方"])
    assert result.data["verdict"] == "incorrect"
    assert result.data["correct_answer"] == "4"
    assert result.data["correction_method"]
    assert result.data["knowledge_points"] == []
    assert result.data["evidence_verified"] is True


def test_retry_cannot_silently_erase_saved_observation_uncertainty():
    ctx = check_context()
    ctx["attempt"]["recognition_uncertainties"] = ["原照片的负号不清晰"]
    result = flow().check(**ctx)
    assert result.data["verdict"] == "unable_to_judge"
    assert "原照片的负号不清晰" in result.data["uncertainties"]
    assert result.data["knowledge_points"] == []


def test_factory_selects_two_model_without_breaking_existing_modes():
    with CorrectionPipeline.from_env({"CORRECTION_MODE": "two_model"}) as pipeline:
        assert isinstance(pipeline, TwoModelWholePagePipeline)
        assert pipeline.client.settings.model != pipeline.review_client.settings.model
        assert pipeline.client.settings.timeout_seconds == 200
        assert pipeline.client.settings.total_timeout_seconds == 200
        assert pipeline.review_client.settings.timeout_seconds == 118
        assert pipeline.review_client.settings.total_timeout_seconds == 118
        assert pipeline.client.settings.max_attempts == pipeline.review_client.settings.max_attempts == 1


def test_duplicate_target_failure_keeps_a_fixed_private_diagnostic_and_usage():
    from test_whole_page_pipeline import FakeClient, picture
    client = FakeClient("synthetic-reader", {"items": [correct("1"), correct("1")]})
    pipeline = TwoModelWholePagePipeline(client, client)
    call = pipeline._read_and_judge(client, picture(), "primary")
    assert call["code"] == "invalid_output"
    assert call["items"] == {}
    attempt = call["metadata"].attempts[0]
    assert attempt.usage.total_tokens == 30
    assert attempt.diagnostic.rule == "duplicate_target_label"
    assert attempt.diagnostic.path == ("items", 1, "label")


def test_one_inconsistent_answer_status_does_not_discard_other_page_results():
    from test_whole_page_pipeline import FakeClient, picture
    first = FakeClient("first-reader", {"items": [
        correct("1", answer_text=None, answer_status="present"), correct("2")]})
    second = FakeClient("second-reader", {"items": [correct("1"), correct("2")]})
    pipeline = TwoModelWholePagePipeline(first, second)
    result = pipeline.recognize(images=(picture(),))
    rows = {row["label"]: row for row in result.data["items"]}
    assert rows["1"]["assessment"]["verdict"] == "unable_to_judge"
    assert rows["2"]["assessment"]["verdict"] == "correct"
    call = result.data["multimodel_audit"]["calls"][0]
    assert call["code"] == "ok"
    assert call["raw_items"][0]["answer_status"] == "present"
    assert call["items"][0]["answer_status"] == "unclear"
    assert call["observation_normalizations"] == [{"label": "1", "code": "answer_status_mismatch"}]


def test_blank_empty_string_is_only_a_representation_change_and_raw_is_retained():
    from test_whole_page_pipeline import FakeClient, picture
    client = FakeClient("synthetic-reader", {"items": [correct(
        answer_text=" \n", answer_status="blank", verdict="unanswered")]})
    pipeline = TwoModelWholePagePipeline(client, client)
    call = pipeline._read_and_judge(client, picture(), "primary")
    assert call["code"] == "ok"
    assert call["items"]["1"]["answer_text"] is None
    assert call["items"]["1"]["answer_status"] == "blank"
    assert call["raw_items"][0]["answer_text"] == " \n"
    assert call["observation_normalizations"] == [{"label": "1", "code": "empty_blank_to_null"}]


def test_blank_label_with_real_text_never_erases_the_text_or_becomes_unanswered():
    from test_whole_page_pipeline import FakeClient, picture
    client = FakeClient("synthetic-reader", {"items": [correct(
        answer_text="-4", answer_status="blank", verdict="unanswered")]})
    pipeline = TwoModelWholePagePipeline(client, client)
    call = pipeline._read_and_judge(client, picture(), "primary")
    assert call["items"]["1"]["answer_text"] == "-4"
    assert call["items"]["1"]["answer_status"] == "unclear"
    assert call["items"]["1"]["uncertainties"]
