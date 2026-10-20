"""Synthetic counterexamples for the authority of image-backed reconciliation."""

from copy import deepcopy

import pytest

from test_two_model_pipeline import correct
from xueban_backend.providers.reconciliation import evaluate_review, has_unreadable_marker


def review(**changes):
    return {
        "label": "1", "resolution": "equivalent", "selected_source": "primary",
        "same_question": True, "answer_clear": True, "answer_text": "4",
        "answer_status": "present", "verdict": "correct", "reason_code": None,
        "correct_answer": None, "correction_method": None,
        "observation_basis": "direct_image",
        "visual_evidence": "原图中两份转述都指向同一问，学生写出的数字为4。",
        "remaining_uncertainties": [], "unresolved_cause": None, **changes,
    }


def test_equivalent_stem_paraphrase_keeps_archived_student_text():
    a, b = correct(stem_text="根据x的值求值"), correct(stem_text="根据下列x的值，分别求值")
    original = deepcopy(a)
    result = evaluate_review(review(), a, b)
    assert result["resolved"] is True
    assert result["reason_code"] == "semantic_review_equivalent"
    assert result["source"] == original == a


def test_non_material_unselected_option_can_release_judgment_without_claiming_literal_match():
    a = correct(answer_text="A", options=[{"label": "D", "text": "2又1/2 n"}])
    b = correct(answer_text="A", options=[{"label": "D", "text": "2又1/2 π"}])
    result = evaluate_review(review(resolution="non_material", answer_text="A",
        visual_evidence="学生选A；D无论读作n还是π都保留带分数，两种读法均不规范，不改变选A。"), a, b)
    assert result["resolved"] is True
    assert result["source"]["options"] == a["options"]
    assert "不代表所有文字均已逐字核实" in result["basis_summary"]


@pytest.mark.parametrize("resolution", ["equivalent", "non_material"])
@pytest.mark.parametrize("change", [
    {"answer_text": "-4"}, {"answer_status": "unclear"},
    {"uncertainties": ["负号不清"]}, {"verdict": "incorrect"},
])
def test_semantic_label_cannot_hide_material_handwriting_or_judgment_conflict(resolution, change):
    result = evaluate_review(review(resolution=resolution), correct(), correct(**change))
    assert result["resolved"] is False
    assert result["rejection_code"] == "material_observation_change"


@pytest.mark.parametrize("change,reason", [
    ({"same_question": False}, "question_not_bound"),
    ({"answer_clear": False}, "answer_not_clear"),
    ({"answer_text": "2a+10"}, "new_answer_not_allowed"),
    ({"visual_evidence": " "}, "missing_image_evidence"),
    ({"remaining_uncertainties": ["末尾仍有模糊笔画"]}, "remaining_uncertainty"),
    ({"selected_source": None}, "missing_source"),
])
def test_visual_resolution_requires_bound_clear_original_candidate(change, reason):
    result = evaluate_review(review(resolution="visual_resolution", **change), correct(), correct())
    assert result["resolved"] is False
    assert result["rejection_code"] == reason


def test_visual_resolution_can_choose_written_wrong_answer_without_inventing_third_answer():
    a = correct(answer_text="12x", verdict="incorrect", correct_answer="2a+10")
    b = correct(answer_text="2a+10")
    result = evaluate_review(review(resolution="visual_resolution", answer_text="12x",
        verdict="incorrect", correct_answer="2a+10", correction_method="先写上月收入的2倍，再加10。",
        visual_evidence="原图本空依次可见1、2及交叉形x，未见a或加号；候选primary保留该笔迹。"), a, b)
    assert result["resolved"] is True
    assert result["source"]["answer_text"] == "12x"
    assert result["verdict"] == "incorrect"
    assert result["correct_answer"] == "2a+10"


def test_unreadable_is_distinct_from_model_disagreement():
    for cause in ("visual_unreadable", "model_disagreement"):
        result = evaluate_review(review(resolution="unresolved", unresolved_cause=cause,
            answer_clear=False, remaining_uncertainties=["仍不能确认"]), correct(), correct())
        assert result["resolved"] is False
        assert result["cause"] == cause


def test_explicit_unreadable_marker_cannot_become_clear_by_boolean_alone():
    text = "36+[涂改不清]+9=81"
    result = evaluate_review(review(resolution="visual_resolution", answer_text=text),
                            correct(answer_text=text), correct(answer_text="36+36+9=81"))
    assert result["resolved"] is False
    assert result["cause"] == "visual_unreadable"


@pytest.mark.parametrize("text", ["36+[不清]+9", "36+（看不清）+9", "[涂改不清]", "【无法辨认】", "[illegible]"])
def test_unreadable_marker_is_explicit(text):
    assert has_unreadable_marker(text)


@pytest.mark.parametrize("text", ["36+[涂改]+9=81", "36+（划掉-36，改成36）+9=81", "[已改成36]", "36+36+9=81"])
def test_crossing_out_or_legible_correction_alone_is_not_unreadable(text):
    assert not has_unreadable_marker(text)
    result = evaluate_review(review(resolution="visual_resolution", answer_text=text),
                            correct(answer_text=text), correct(answer_text=text))
    assert result["resolved"] is True


@pytest.mark.parametrize("basis", ["semantic_only", "calculation", "uncertain"])
def test_visual_resolution_requires_direct_observation_not_reasoning(basis):
    result = evaluate_review(review(resolution="visual_resolution", observation_basis=basis),
                            correct(), correct())
    assert result["resolved"] is False
    assert result["rejection_code"] == "observation_not_direct"


def test_correct_final_result_does_not_prove_overwritten_intermediate_number():
    inferred = correct(answer_text="36+36+9=81")
    uncertain = correct(answer_text="36+[涂改不清]+9=81", answer_status="unclear",
                        uncertainties=["中间加数被涂改遮住"])
    unsafe_review = review(
        resolution="visual_resolution", answer_text=inferred["answer_text"],
        visual_evidence="末行=81清楚，结合首行可见，中间加数应为36，涂改后得到正确结果81。")
    result = evaluate_review(unsafe_review, inferred, uncertain)
    assert result["resolved"] is False
    assert result["rejection_code"] == "inferred_student_transcription"


def test_calculation_can_explain_wrong_answer_without_becoming_reading_evidence():
    a = correct(answer_text="12x", verdict="incorrect", correct_answer="2a+10")
    result = evaluate_review(review(resolution="visual_resolution", answer_text="12x",
        verdict="incorrect", correct_answer="2a+10", correction_method="根据题意，收入应为2a+10。",
        visual_evidence="空格内依次可见1、2和交叉形x，未见加号。"), a, correct(answer_text="2a+10"))
    assert result["resolved"] is True


def test_equivalent_or_non_material_review_can_use_semantic_basis():
    for resolution in ("equivalent", "non_material"):
        assert evaluate_review(review(resolution=resolution, observation_basis="semantic_only"),
                               correct(), correct())["resolved"] is True


@pytest.mark.parametrize("stem", [None, "", "   "])
def test_visual_resolution_cannot_bind_a_question_without_printed_conditions(stem):
    source = correct(stem_text=stem)
    result = evaluate_review(review(resolution="visual_resolution"), source, source)
    assert result["resolved"] is False
    assert result["rejection_code"] == "missing_question_text"


@pytest.mark.parametrize("changes", [
    {"stem_text": "当x=[看不清]时，求x+1"},
    {"options": [{"label": "A", "text": "x+[无法辨认]"}]},
])
def test_visual_review_cannot_clear_unreadable_question_or_choice_by_boolean(changes):
    source = correct(**changes)
    result = evaluate_review(review(resolution="visual_resolution"), source, source)
    assert result["resolved"] is False
    assert result["cause"] == "visual_unreadable"
    assert result["rejection_code"] == "unreadable_question_marker"


def test_unique_original_answer_echo_can_correct_a_mislabeled_source_pointer():
    first, second = correct(answer_text="2a+10"), correct(answer_text="12x")
    result = evaluate_review(review(resolution="visual_resolution", selected_source="primary",
                                   answer_text="12x"), first, second)
    assert result["resolved"] is True
    assert result["source"] == second
    assert result["resolved_source"] == "secondary"
    # A newly invented third answer still has no authority.
    rejected = evaluate_review(review(resolution="visual_resolution", answer_text="12a"), first, second)
    assert rejected["resolved"] is False
    assert rejected["rejection_code"] == "new_answer_not_allowed"


def test_literal_fraction_coefficient_layout_does_not_require_visual_guessing():
    first, second = correct(answer_text="1/2ah"), correct(answer_text="1/2 ah")
    result = evaluate_review(review(answer_text="1/2ah", observation_basis="semantic_only"),
                             first, second)
    assert result["resolved"] is True
    assert result["source"]["answer_text"] == "1/2ah"
