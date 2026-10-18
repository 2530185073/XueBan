"""Synthetic model outputs only; these tests never read real credentials."""

from copy import deepcopy
from dataclasses import asdict
from threading import Barrier

import pytest

from xueban_backend.providers.ark import (ArkSettings, Attempt, CandidateResult,
    Metadata, ProviderError, Usage)
from xueban_backend.providers.correction_pipeline import CorrectionPipeline, PipelineImage
from xueban_backend.providers.whole_page import (WholePageCorrectionPipeline, canonical_label,
                                                normalize_literal_math, normalize_page_stem)


PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="


def item(label="1", **changes):
    return {"label": label, "stem_text": "计算(-2)²。", "answer_text": "-4", "answer_status": "present",
            "uncertainties": [], **changes}


def judged(label="1", **changes):
    return {**item(label), "verdict": "incorrect", "reason_code": None,
            "guidance_code": "sign", "basis_summary": "INTERNAL_STANDARD_ANSWER=4", **changes}


def picture(asset="asset_original"):
    return PipelineImage(asset, PNG, 1, 1)


class FakeClient:
    def __init__(self, model, output, *, barrier=None):
        self.settings = ArkSettings(model=model, api_key="synthetic-" + model)
        self.output, self.calls, self.barrier = output, [], barrier

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.barrier:
            self.barrier.wait(timeout=2)
        if isinstance(self.output, Exception):
            raise self.output
        output = self.output(kwargs) if callable(self.output) else deepcopy(self.output)
        usage = Usage(10, 20, 30)
        attempt = Attempt(1, 200, "ok", 20, model=self.settings.model, usage=usage)
        return CandidateResult(kwargs["role"], output,
            Metadata(self.settings.model, self.settings.model, None, None, usage, 20, (attempt,)))

    def close(self):
        pass


def pipeline(first=None, second=None, review=None, *, barrier=None):
    return WholePageCorrectionPipeline(FakeClient("primary", first if first is not None else {"items": [judged()]}, barrier=barrier),
        FakeClient("reader", second if second is not None else {"items": [item()]}, barrier=barrier),
        FakeClient("reviewer", review if review is not None else {"items": [judged()]}))


def test_two_independent_calls_start_in_parallel_and_do_not_see_each_other():
    flow = pipeline(barrier=Barrier(2))
    result = flow.recognize(images=(picture(),))
    assessment = result.data["items"][0]["assessment"]
    assert assessment["verdict"] == "incorrect"
    assert assessment["verification"]["status"] == "matched"
    assert assessment["verification"]["independent_readback"] is True
    assert len(result.metadata.attempts) == 2
    assert result.metadata.usage.total_tokens == 60
    assert not flow.review_client.calls
    for client in (flow.client, flow.readback_client):
        assert "INTERNAL_STANDARD_ANSWER" not in client.calls[0]["text"]
        assert client.calls[0]["images"] == (PNG,)
    assert flow.client.calls[0]["role"] == "check"
    assert flow.readback_client.calls[0]["role"] == "recognition"
    assert flow.client.calls[0]["max_tokens"] == 24576
    assert flow.client.calls[0]["thinking"] is True
    assert flow.readback_client.calls[0]["max_tokens"] == 12288
    assert "INTERNAL_STANDARD_ANSWER" not in assessment["basis_summary"]
    assert "INTERNAL_STANDARD_ANSWER" in result.data["multimodel_audit"]["calls"][0]["items"][0]["basis_summary"]


def test_same_label_on_different_pages_and_multiple_blanks_are_never_collapsed():
    flow = pipeline(first={"items": [judged("3(1)#1"), judged("3(1)#2")]},
                    second={"items": [item("３（１）#１"), item("3(1)#2")]})
    result = flow.recognize(images=(picture(), picture("asset_second")))
    assert len(result.data["items"]) == 4
    assert len(result.metadata.attempts) == 4
    assert all(i["assessment"]["verification"]["status"] == "matched" for i in result.data["items"])
    assert canonical_label("3(1)#1") != canonical_label("3(1)#2")


def test_reader_only_question_is_retained_and_not_marked_correct():
    flow = pipeline(first={"items": [judged()]}, second={"items": [item(), item("2")]},
                    review={"items": [judged("2")]})
    result = flow.recognize(images=(picture(),))
    assert [i["label"] for i in result.data["items"]] == ["1", "2"]
    missing = result.data["items"][1]
    assert missing["assessment"]["verdict"] == "unable_to_judge"
    assert "unmatched_label" in missing["assessment"]["verification"]["reason_codes"]
    assert len(result.metadata.attempts) == 3


@pytest.mark.parametrize("first,second,review", [
    (judged(answer_text="2a+10", verdict="correct"), item(answer_text="12x"), judged(answer_text="2a+10", verdict="correct")),
    (judged(), item(uncertainties=["负号不清楚"]), judged()),
    (judged(uncertainties=["中间步骤被涂改"]), item(), judged()),
    (judged(answer_status="blank", answer_text=None, verdict="correct"), item(answer_status="blank", answer_text=None), judged()),
    (judged(verdict="unable_to_judge"), item(), judged()),
])
def test_majority_vote_never_erases_substantive_conflict(first, second, review):
    result = pipeline({"items": [first]}, {"items": [second]}, {"items": [review]}).recognize(images=(picture(),))
    value = result.data["items"][0]
    assert value["assessment"]["verdict"] == "unable_to_judge"
    assert value["recognition_status"] == "needs_revision"
    assert value["assessment"]["uncertainties"]
    assert value["assessment"]["verification"]["status"] == "conflict"


def test_review_may_resolve_stem_scope_difference_but_not_answer_disagreement():
    flow = pipeline(second={"items": [item(stem_text="计算所给代数式的值。题目内容不同抄写范围") ]})
    value = flow.recognize(images=(picture(),)).data["items"][0]
    assert value["assessment"]["verification"]["status"] == "reviewed"
    assert value["assessment"]["verdict"] == "incorrect"
    request = flow.review_client.calls[0]
    assert "labels" in request["text"]
    assert "-4" not in request["text"] and "INTERNAL_STANDARD_ANSWER" not in request["text"]


def test_provider_failure_preserves_usable_items_and_every_paid_attempt():
    failure = ProviderError("upstream_timeout", attempts=(Attempt(1, 200, "upstream_timeout", 100),), latency_ms=100)
    flow = pipeline(second=failure)
    result = flow.recognize(images=(picture(),))
    assert len(result.data["items"]) == 1
    assert result.data["items"][0]["assessment"]["verification"]["status"] == "degraded"
    assert result.data["items"][0]["assessment"]["verdict"] == "unable_to_judge"
    assert [a.code for a in result.metadata.attempts] == ["ok", "upstream_timeout"]
    assert [a.model for a in result.metadata.attempts] == ["primary", "reader"]
    assert result.metadata.usage is None
    assert not flow.review_client.calls


def test_all_providers_failed_raises_with_all_attempts_instead_of_empty_homework():
    failure = ProviderError("upstream_timeout", attempts=(Attempt(1, None, "upstream_timeout", 100),))
    with pytest.raises(ProviderError, match="multimodel_unavailable") as captured:
        pipeline(failure, failure).recognize(images=(picture(),))
    assert len(captured.value.attempts) == 2


def test_duplicate_normalized_labels_reject_that_provider_and_keep_other_read():
    result = pipeline(first={"items": [judged("3(1)"), judged("３（１）")]}).recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verification"]["status"] == "degraded"
    assert [a.code for a in result.metadata.attempts] == ["invalid_output", "ok"]


def test_credentials_echo_cannot_reach_internal_audit_or_candidate():
    result = pipeline(first={"items": [judged(basis_summary="synthetic-primary")]}).recognize(images=(picture(),))
    assert "synthetic-primary" not in str(result.data) + str(asdict(result.metadata))
    assert result.metadata.attempts[0].code == "provider_credential_echo"


def test_blank_unanswered_is_valid_but_never_wrong():
    first = judged(answer_text=None, answer_status="blank", verdict="unanswered")
    second = item(answer_text=None, answer_status="blank")
    value = pipeline({"items": [first]}, {"items": [second]}).recognize(images=(picture(),)).data["items"][0]
    assert value["assessment"]["verdict"] == "unanswered"
    assert value["recognition_status"] == "ready"


def test_factory_defaults_to_existing_pipeline_and_opt_in_is_explicit():
    with CorrectionPipeline.from_env({}) as flow:
        assert type(flow) is CorrectionPipeline
    with CorrectionPipeline.from_env({"CORRECTION_MODE": "multimodel"}) as flow:
        assert isinstance(flow, WholePageCorrectionPipeline)
        assert flow.client.settings.max_concurrency == flow.readback_client.settings.max_concurrency == 4
        assert flow.client.settings.total_timeout_seconds <= 118


def check_context():
    return {"images": (picture(),),
        "question": {"question_id": "question_one", "revision": 1, "stem_text": "计算(-2)²。", "label": "1",
                     "regions": [CorrectionPipeline.page_region(picture())]},
        "attempt": {"id": "attempt_one", "question_id": "question_one", "captured_question_revision": 1,
                    "recognition_revision": 1, "answer_text": "-4", "answer_status": "present",
                    "answer_regions": [], "recognition_status": "ready", "asset_ids": ["asset_original"]},
        "input_versions": {"question_id": "question_one", "question_revision": 1, "attempt_id": "attempt_one",
                           "attempt_recognition_revision": 1, "case_revision_at_submission": 1, "fingerprint": "a" * 64}}


def check_output(**changes):
    return {"verdict": "incorrect", "reason_code": None,
        "readback": {"stem_text": "计算(-2)²。", "answer_text": "-4", "answer_status": "present"},
        "recognition_consistent": True, "basis_summary": "INTERNAL_STANDARD_ANSWER=4", "uncertainties": [], **changes}


def target_read():
    return {key: value for key, value in check_output().items() if key in {"readback", "uncertainties"}}


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_current_attempt_checks_require_two_independent_calls_and_hide_answer(role):
    flow = pipeline(check_output(), target_read(), barrier=Barrier(2))
    context = check_context()
    if role == "recheck":
        context["images"] = (picture(), picture("asset_new"))
        context["attempt"].update(asset_ids=["asset_new"], prior_answer="DO_NOT_SEND_OLD_ANSWER")
    result = getattr(flow, role)(**context)
    assert result.data["verdict"] == "incorrect"
    assert result.data["verification"]["status"] == "matched"
    assert len(result.metadata.attempts) == 2
    assert "INTERNAL_STANDARD_ANSWER" not in str(result.data["basis"])
    for client in (flow.client, flow.readback_client):
        assert "DO_NOT_SEND_OLD" not in client.calls[0]["text"]
        assert "本次" in client.calls[0]["instructions"]
    assert "-4" not in flow.readback_client.calls[0]["text"]
    assert "provisional_transcription" not in flow.readback_client.calls[0]["text"]
    assert flow.readback_client.calls[0]["role"] == "recognition"


def test_check_does_not_disguise_reader_failure_as_multimodel_success():
    failure = ProviderError("upstream_timeout", attempts=(Attempt(1, None, "upstream_timeout", 10),))
    flow = pipeline(check_output(), failure)
    result = flow.check(**check_context())
    assert result.data["verdict"] == "unable_to_judge"
    assert result.data["verification"]["status"] == "degraded"
    assert len(result.metadata.attempts) == 2


def test_check_preserves_both_paid_attempts_if_primary_fails():
    failure = ProviderError("upstream_timeout", attempts=(Attempt(1, None, "upstream_timeout", 10),))
    flow = pipeline(failure, target_read())
    with pytest.raises(ProviderError, match="upstream_timeout") as result:
        flow.check(**check_context())
    assert len(result.value.attempts) == 2


@pytest.mark.parametrize("answer", ["学生作答：", "   "])
def test_empty_present_answers_do_not_become_matched(answer):
    result = pipeline({"items": [judged(answer_text=answer)]}, {"items": [item(answer_text=answer)]})
    with pytest.raises(ProviderError, match="multimodel_unavailable"):
        result.recognize(images=(picture(),))


@pytest.mark.parametrize("left,right", [
    ("a²", "a^2"), ("(-2)²", "(-2)^2"), ("x⁻²", "x^-2"),
    ("4 a", "4a"), ("4 a + 2", "4a+2"), ("m / a", "m/a"),
    ("x²⁺¹", "x^{2+1}"), ("学生作答：4 a", "4a"),
])
def test_multimodel_literal_typography_comparison_preserves_visible_math(left, right):
    assert normalize_literal_math(left, "answer_text") == normalize_literal_math(right, "answer_text")


@pytest.mark.parametrize("left,right", [
    ("x²", "x2"), ("1 2", "12"), ("1 2x", "12x"), ("x 2", "x2"),
    ("-4", "4"), ("x²⁺¹", "x^2+1"), ("(x+1)²", "x+1²"),
    ("2½n", "2又1/2 n"), ("4 apples", "4apples"), ("12x", "2a+10"),
    ("2+2", "4"), ("1/2", "0.5"), ("a²", "a*a"),
])
def test_literal_comparison_never_solves_or_erases_distinguishing_symbols(left, right):
    assert normalize_literal_math(left, "answer_text") != normalize_literal_math(right, "answer_text")


def test_multimodel_typography_does_not_change_raw_archived_student_answer():
    flow = pipeline({"items": [judged(answer_text="a²")]}, {"items": [item(answer_text="a^2")]})
    value = flow.recognize(images=(picture(),)).data["items"][0]
    assert value["answer_text"] == "a²"
    assert value["assessment"]["verification"]["status"] == "matched"


@pytest.mark.parametrize("label,left,right", [
    ("3(1)#1", "3. 用代数式表示：(1)若正方形的边长为a，则正方形的面积是____。", "用代数式表示：若正方形的边长为a，则正方形的面积是____"),
    ("3(10)", "3(10)、计算3(x - 2)的值。", "计算3(x-2)的值"),
    ("3(10)", "3(10) 某商店上月收入a元，本月收入____。", "某商店上月收入a元，本月收入____"),
    ("4(1)", "4. 根据x求值：(1)x=0.5。", "根据x求值：x=0.5"),
    ("3(6)#1", "3. 用代数式表示：(6)其余两个数分别为____、____（前一空）。", "用代数式表示：其余两个数分别为____、____"),
    ("3(6)#2", "用代数式表示：其余两个数分别为____、____。本空为第二个数。", "用代数式表示：其余两个数分别为____、____"),
    ("2", "第2题：填写__ __。", "填写________"),
    ("1", "求x²，填写____。", "求x^2,填写____"),
])
def test_full_stem_normalization_removes_only_matching_label_and_layout(label, left, right):
    assert normalize_page_stem({"label": label, "stem_text": left}) == normalize_page_stem({"label": label, "stem_text": right})


@pytest.mark.parametrize("label,left,right", [
    ("3(10)", "3(10)+2", "+2"),
    ("3", "3.14+2", "14+2"),
    ("3(1)", "计算：(1)+2", "计算：+2"),
    ("3(1)", "计算f(1)的值", "计算f的值"),
    ("3(1)", "3. 计算：(2)若x为整数", "计算：若x为整数"),
    ("3(10)", "计算3(x-2)的值", "计算3x-2的值"),
    ("3(10)", "计算3(x-2)的值", "计算3(x+2)的值"),
    ("3(10)", "计算3(x-2)的值", "计算4(x-2)的值"),
    ("3(1)", "若x=1，求x²", "求x²"),
    ("1", "求x²", "求x2"),
    ("1", "求数列1,2的和", "求数列12的和"),
    ("1", "求数列1,2,3,...", "求数列1,2,3,"),
    ("1", "求1 2的值", "求12的值"),
    ("3(6)#1", "填____（后一空）", "填____"),
])
def test_full_stem_normalization_preserves_math_conditions_and_unmatched_labels(label, left, right):
    assert normalize_page_stem({"label": label, "stem_text": left}) != normalize_page_stem({"label": label, "stem_text": right})


def test_correct_math_after_label_cleanup_does_not_erase_uncertainty_or_actual_answer_conflict():
    main = judged("3(10)", stem_text="3. 用代数式表示：(10)本月收入是多少？", answer_text="2a+10", verdict="correct")
    reader = item("3(10)", stem_text="用代数式表示：本月收入是多少？", answer_text="12x")
    data = pipeline({"items": [main]}, {"items": [reader]}, {"items": [main]}).recognize(images=(picture(),)).data
    assert data["items"][0]["assessment"]["verification"]["status"] == "conflict"
    assert "answer_disagreement" in data["items"][0]["assessment"]["verification"]["reason_codes"]
    main = judged("3(7)", stem_text="3. 用代数式表示：(7)路程是多少？", answer_text="vt", verdict="correct")
    reader = item("3(7)", stem_text="用代数式表示：路程是多少？", answer_text="vt", uncertainties=["v与u笔迹不清楚"])
    data = pipeline({"items": [main]}, {"items": [reader]}, {"items": [main]}).recognize(images=(picture(),)).data
    assert data["items"][0]["assessment"]["verdict"] == "unable_to_judge"
