"""Synthetic counterexamples for the authority of image-backed reconciliation."""

from copy import deepcopy

import pytest

from test_two_model_pipeline import correct
from xueban_backend.providers.reconciliation import (
    _condition_quote_requirement, _critical_literals, evaluate_review,
    has_unreadable_marker, review_target, source_id,
)


def review(*, source=None, comparison=None, **changes):
    """An explicitly source-bound synthetic review; never used by production."""
    if source is None:
        source = correct(changes.get("label", "1"), **{
            key: changes[key] for key in ("answer_text", "answer_status") if key in changes
        })
    selected = changes.get("selected_source", "primary")
    identity = source_id(selected, source) if selected else None
    return {
        "label": "1", "resolution": "equivalent", "selected_source": "primary",
        "source_id": identity,
        "observation_comparison": comparison or {
            "observations_same": True, "conditions_same": True,
            "difference_type": "paraphrase",
            "explanation": "两份观察描述同一小问的相同有效作答和条件，原判定另外核算。",
        },
        "judgment_basis": {
            "source_id": identity,
            "condition_quotes": [{"field": "stem_text", "option_label": None,
                                  "text": source.get("stem_text") or "无法引用"}],
            "answer_quote": source.get("answer_text"),
            "summary": "依据所引题干条件重新核验所选源的有效作答。",
        },
        "same_question": True, "answer_clear": True, "answer_text": source.get("answer_text"),
        "answer_status": source.get("answer_status"), "verdict": "correct", "reason_code": None,
        "correct_answer": None, "correction_method": None,
        "observation_basis": "direct_image",
        "visual_evidence": "原图中两份转述都指向同一问，学生写出的数字为4。",
        "remaining_uncertainties": [], "unresolved_cause": None, **changes,
    }


def test_equivalent_stem_paraphrase_keeps_archived_student_text():
    a, b = correct(stem_text="根据x的值求值"), correct(stem_text="根据下列x的值，分别求值")
    original = deepcopy(a)
    result = evaluate_review(review(source=a), a, b)
    assert result["resolved"] is True
    assert result["reason_code"] == "semantic_review_equivalent"
    assert result["source"] == original == a
    assert result["requires_visual_support"] is True


def test_condition_quote_typography_and_answer_excerpt_do_not_rewrite_source():
    a = correct(stem_text="计算：（－21）÷（－7）", answer_text="左侧：\n＋(21÷7)\n＝3")
    proposal = review(source=a)
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "(-21)÷(-7)"
    proposal["judgment_basis"]["answer_quote"] = "+(21÷7)=3"
    result = evaluate_review(proposal, a, a)
    assert result["resolved"] is True
    assert result["source"]["answer_text"] == a["answer_text"]
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "(-21)÷(7)"
    assert evaluate_review(proposal, a, a)["rejection_code"] == "condition_quote_not_bound"


def test_choice_may_cite_relevant_option_without_repeating_already_bound_stem():
    a = correct(stem_text="下面算法正确的是", answer_text="D",
                options=[{"label": "D", "text": "(-8)÷(-4)=8÷4"}])
    proposal = review(source=a)
    proposal["judgment_basis"]["condition_quotes"] = [
        {"field": "option", "option_label": "D", "text": "(-8)÷(-4)=8÷4"}]
    assert evaluate_review(proposal, a, a)["resolved"] is True


@pytest.mark.parametrize("quote", ["计算", "55", "87.2"])
def test_short_stem_judgment_must_cite_all_printed_conditions_not_a_generic_heading(quote):
    source = correct(stem_text="计算87.2÷55。", answer_text="1.04")
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = quote
    proposal["judgment_basis"]["summary"] = "57.2÷55=1.04，学生答案正确。"
    outcome = evaluate_review(proposal, source, source)
    assert outcome["resolved"] is False
    assert outcome["rejection_code"] == "condition_quote_missing_literals"


def test_condition_coverage_ignores_matching_question_labels_and_empty_answer_boundary():
    source = correct("3(1)", stem_text="3(1)：计算87.2÷55=____", answer_text="1.04")
    proposal = review(source=source, label="3(1)")
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "87.2÷55"
    # Computed values in mathematical reasoning are not new observations and
    # must not be rejected simply because they were not printed in the stem.
    proposal["judgment_basis"]["summary"] = "87.2÷55约为1.585，学生的1.04不正确。"
    proposal.update(verdict="incorrect", correct_answer="436/275", correction_method="重新计算商。")
    outcome = evaluate_review(proposal, source, source)
    assert outcome["resolved"] is True
    assert outcome["audit"]["condition_quote_coverage"] == "full_short_stem_literals"


@pytest.mark.parametrize("stem,quote,citation", [
    ("(2024·陕西)-3的倒数是____", "-3的倒数是____", "(2024·陕西)"),
    ("计算：(2025·天津改编)(-21)÷(-7)=____", "(-21)÷(-7)", "(2025·天津改编)"),
    ("（2024·陕西中考）计算87.2÷55", "87.2÷55", "(2024·陕西中考)"),
])
def test_leading_source_year_can_be_omitted_from_quotes_but_is_archived(stem, quote, citation):
    source = correct(stem_text=stem)
    original = deepcopy(source)
    identity = source_id("primary", source)
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = quote
    outcome = evaluate_review(proposal, source, source)
    assert outcome["resolved"] is True
    assert outcome["audit"]["condition_quote_ignored_source_metadata"] == [citation]
    assert outcome["source"] == source == original
    assert source_id("primary", source) == identity
    assert citation[1:5] in _critical_literals(stem)


@pytest.mark.parametrize("stem,quotes", [
    ("2024年有80人，2025年有100人，人数增加多少？", ["80人", "100人"]),
    ("(2024·产量)80吨，(2025·产量)100吨，求增加量", ["80吨", "100吨"]),
    ("某人出生在(2024·陕西)，到2025年过了多少年？", ["2025年"]),
    ("在(2024+1)年有80人，求年份", ["80人"]),
    ("(2024·陕西)2024年80人，2025年100人，求年增长量", ["80人", "100人"]),
    ("(2024·陕西)计算87.2÷55", ["计算"]),
])
def test_source_citation_exception_does_not_drop_body_years_or_other_conditions(stem, quotes):
    source = correct(stem_text=stem)
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"] = [
        {"field": "stem_text", "option_label": None, "text": quote} for quote in quotes]
    assert evaluate_review(proposal, source, source)["rejection_code"] == "condition_quote_missing_literals"
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = stem
    assert evaluate_review(proposal, source, source)["resolved"] is True


def test_citation_quote_exception_does_not_weaken_original_condition_veto():
    primary = correct(stem_text="(2024·陕西)计算87.2÷55")
    secondary = correct(stem_text="(2025·陕西)计算87.2÷55")
    proposal = review(source=primary)
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "87.2÷55"
    assert evaluate_review(proposal, primary, secondary)["rejection_code"] == "material_condition_change"


@pytest.mark.parametrize("target,answer", [
    ("I’m ______ years old.", "14"),
    ("My favourite sport is ______.", "run"),
    ("My favourite place is ______.", "Chinese chess"),
])
def test_p19_common_english_heading_need_not_be_requoted(target, answer):
    # Real P19 observed stem/quote shapes; this only tests source binding.
    # It does not establish that any of these answers is correct.
    source = correct(
        stem_text="Write some sentences about yourself using the structures below. " + target,
        answer_text=answer, subject="英语")
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = target
    outcome = evaluate_review(proposal, source, source)
    assert outcome["resolved"] is True
    assert outcome["source"]["answer_text"] == answer


@pytest.mark.parametrize("stem,insufficient,sufficient", [
    ("长方形长为a、宽为b，求ab", ["长方形"], ["a", "b", "ab"]),
    ("已知AB=10cm，求BC", ["AB=10", "BC"], ["AB=10cm", "BC"]),
    ("distance=speed×time", ["distance=speed"], ["distance=speed×time"]),
    ("计算x/y+a", ["x/y"], ["x/y", "+", "a"]),
    ("计算a-b与a+b", ["a-b"], ["a-b", "a+b"]),
    ("Let a=2 and b=3. Find a+b.", ["a=2", "b=3"], ["a=2", "b=3", "a+b"]),
    ("Let a and b denote lengths.", ["a"], ["a", "b"]),
    ("A runner travels 10 km in 30 minutes.", ["10", "30"], ["10 km", "30 minutes"]),
    ("3kg与5m/s", ["3", "5", "/"], ["3kg", "5m/s"]),
    ("面积为3cm²，求边长", ["3"], ["3cm²"]),
    ("计算sin(x)+cos(y)", ["x", "+", "y"], ["sin(x)+cos(y)"]),
    ("圆面积为πr²", ["r²"], ["πr²"]),
    ("辨别zhǐ和zhí的声调", ["zhǐ"], ["zhǐ", "zhí"]),
    ("辨别音标/ʃ/与/θ/", ["/ʃ/"], ["/ʃ/", "/θ/"]),
])
def test_quote_coverage_keeps_mathematical_identifiers_units_and_phonetic_marks(
        stem, insufficient, sufficient):
    source = correct(stem_text=stem)
    proposal = review(source=source)
    def quotes(parts):
        return [{"field": "stem_text", "option_label": None, "text": part} for part in parts]
    proposal["judgment_basis"]["condition_quotes"] = quotes(insufficient)
    assert evaluate_review(proposal, source, source)["rejection_code"] == "condition_quote_missing_literals"
    proposal["judgment_basis"]["condition_quotes"] = quotes(sufficient)
    assert evaluate_review(proposal, source, source)["resolved"] is True


@pytest.mark.parametrize("value", ["57.2", "87.2"])
def test_decimal_condition_cannot_be_replaced_by_generic_compute_heading(value):
    source = correct(stem_text=f"计算{value}÷55", answer_text="1.04")
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "计算"
    assert evaluate_review(proposal, source, source)["rejection_code"] == "condition_quote_missing_literals"


def test_multi_blank_substitution_is_a_judgment_not_an_observation_rewrite():
    source = correct(stem_text="计算：(-18)÷9=____(18÷9)=____", answer_text="-；2")
    secondary = {**deepcopy(source), "verdict": "incorrect", "correct_answer": "第1空：-；第2空：-2"}
    proposal = review(source=source, verdict="incorrect", correct_answer="第1空：-；第2空：-2",
                      correction_method="第2空应独立填写-2，不能把第1空的负号借给第2空。")
    proposal["judgment_basis"]["summary"] = "首空为-，但末空为2；(-18)÷9=-2，末空错误。"
    outcome = evaluate_review(proposal, source, secondary)
    assert outcome["resolved"] is True
    assert outcome["verdict"] == "incorrect"
    assert outcome["source"]["answer_text"] == "-；2"
    # This validator binds observations, not arithmetic. The prompt asks for
    # per-slot substitution; its real model effectiveness needs an image rerun.


def test_separate_expression_quotes_keep_multiletter_variables_and_operators():
    source = correct(stem_text="distance=speed×time")
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"] = [
        {"field": "stem_text", "option_label": None, "text": part}
        for part in ["distance=", "speed", "×", "time"]]
    assert evaluate_review(proposal, source, source)["resolved"] is True


def test_long_passage_needs_a_quantity_anchor_not_an_ordinary_word_or_operator():
    source = correct(stem_text=("The story describes several families. " * 25)
                     + "Let distance=speed×time.")
    proposal = review(source=source)
    for quote in ["The story describes several families.", "×"]:
        proposal["judgment_basis"]["condition_quotes"][0]["text"] = quote
        assert evaluate_review(proposal, source, source)["rejection_code"] == "condition_quote_missing_literals"
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "speed"
    assert evaluate_review(proposal, source, source)["resolved"] is True
    assert _condition_quote_requirement(source)[0] == "long_stem_anchor_only"


def test_option_quotes_cannot_cover_omitted_short_stem_conditions():
    source = correct(stem_text="当x=3时选择正确结果", options=[{"label": "A", "text": "x+3=6"}])
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"] = [
        {"field": "option", "option_label": "A", "text": "x+3=6"}]
    assert evaluate_review(proposal, source, source)["rejection_code"] == "condition_quote_missing_literals"


def test_long_reading_requires_relevant_literal_anchor_without_quoting_every_background_number():
    background = "The story describes several families and their everyday lives. " * 15
    source = correct(stem_text=background + "In 1998 the school opened. In 2024 there were 35 students.",
                     answer_text="35", subject="英语")
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "In 2024 there were 35 students."
    outcome = evaluate_review(proposal, source, source)
    assert outcome["resolved"] is True
    assert outcome["audit"]["condition_quote_coverage"] == "long_stem_anchor_only"
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "The story describes several families"
    assert evaluate_review(proposal, source, source)["rejection_code"] == "condition_quote_missing_literals"


def test_long_chinese_reading_without_numeric_conditions_keeps_prose_quote_path():
    source = correct(stem_text="请阅读下文，分析人物的心情。" + "她静静地望向窗外，又回头看了看桌上的信。" * 40,
                     subject="语文")
    proposal = review(source=source)
    proposal["judgment_basis"]["condition_quotes"][0]["text"] = "她静静地望向窗外"
    assert evaluate_review(proposal, source, source)["resolved"] is True


def test_changed_option_symbol_cannot_be_hidden_by_non_material_label():
    a = correct(answer_text="A", options=[{"label": "D", "text": "2又1/2 n"}])
    b = correct(answer_text="A", options=[{"label": "D", "text": "2又1/2 π"}])
    result = evaluate_review(review(source=a, resolution="non_material", answer_text="A",
        visual_evidence="学生选A；D无论读作n还是π都保留带分数，两种读法均不规范，不改变选A。"), a, b)
    assert result["resolved"] is False
    assert result["rejection_code"] == "material_condition_change"


@pytest.mark.parametrize("resolution", ["equivalent", "non_material"])
@pytest.mark.parametrize("change", [
    {"answer_text": "-4"}, {"answer_status": "unclear"},
    {"uncertainties": ["负号不清"]},
])
def test_semantic_label_cannot_hide_material_handwriting_conflict(resolution, change):
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
    result = evaluate_review(review(source=correct(), resolution="visual_resolution", **change), correct(), correct())
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
    result = evaluate_review(review(source=source, resolution="visual_resolution"), source, source)
    assert result["resolved"] is False
    assert result["rejection_code"] == "missing_question_text"


@pytest.mark.parametrize("changes", [
    {"stem_text": "当x=[看不清]时，求x+1"},
    {"options": [{"label": "A", "text": "x+[无法辨认]"}]},
])
def test_visual_review_cannot_clear_unreadable_question_or_choice_by_boolean(changes):
    source = correct(**changes)
    result = evaluate_review(review(source=source, resolution="visual_resolution"), source, source)
    assert result["resolved"] is False
    assert result["cause"] == "visual_unreadable"
    assert result["rejection_code"] == "unreadable_question_marker"


def test_answer_echo_cannot_correct_a_mislabeled_source_pointer():
    first, second = correct(answer_text="2a+10"), correct(answer_text="12x")
    result = evaluate_review(review(source=second, resolution="visual_resolution", selected_source="primary",
                                   answer_text="12x"), first, second)
    assert result["resolved"] is False
    assert result["rejection_code"] == "source_id_mismatch"
    # A newly invented third answer still has no authority.
    rejected = evaluate_review(review(source=first, resolution="visual_resolution", answer_text="12a"), first, second)
    assert rejected["resolved"] is False
    assert rejected["rejection_code"] == "new_answer_not_allowed"


def test_literal_fraction_coefficient_layout_does_not_require_visual_guessing():
    first, second = correct(answer_text="1/2ah"), correct(answer_text="1/2 ah")
    result = evaluate_review(review(answer_text="1/2ah", observation_basis="semantic_only"),
                             first, second)
    assert result["resolved"] is True
    assert result["source"]["answer_text"] == "1/2ah"
    assert result["requires_visual_support"] is False


def test_same_answer_different_stem_cannot_be_archived_with_other_sources_judgment():
    first = correct(stem_text="84÷7=____", answer_text="4", verdict="incorrect")
    second = correct(stem_text="28÷7=____", answer_text="4")
    original = deepcopy((first, second))
    unsafe = review(source=first, resolution="visual_resolution")
    unsafe["judgment_basis"]["condition_quotes"][0]["text"] = "28÷7"
    unsafe["judgment_basis"]["summary"] = "28÷7=4，与学生作答一致。"
    result = evaluate_review(unsafe, first, second)
    assert result["resolved"] is False
    assert result["rejection_code"] == "condition_quote_not_bound"
    # The same result is legitimate only with the complete second source.
    safe = review(source=second, selected_source="secondary", resolution="visual_resolution")
    safe["judgment_basis"]["summary"] = "28÷7=4，与学生作答一致。"
    result = evaluate_review(safe, first, second)
    assert result["resolved"] is True
    assert result["source"]["stem_text"] == "28÷7=____"
    assert result["source_id"] == result["audit"]["judgment_basis"]["source_id"]
    assert result["requires_visual_support"] is True
    assert (first, second) == original


@pytest.mark.parametrize("change", [
    {"stem_text": "计算(-3)²。"},
    {"options": [{"label": "A", "text": "zhǐ"}, {"label": "B", "text": "zhí"}]},
    {"answer_text": "-4"},
    {"answer_status": "blank"},
    {"label": "2"},
])
def test_source_id_binds_all_observed_fields_and_question_identity(change):
    first = correct()
    altered = {**first, **change}
    assert source_id("primary", first) != source_id("primary", altered)
    outcome = evaluate_review(review(source=first, resolution="visual_resolution"), altered, correct())
    assert outcome["resolved"] is False
    assert outcome["rejection_code"] == "source_id_mismatch"


def test_judgment_cannot_cite_a_different_source_id_or_option():
    source = correct(options=[{"label": "A", "text": "zhǐ"}, {"label": "B", "text": "zhí"}])
    unsafe = review(source=source)
    unsafe["judgment_basis"]["source_id"] = source_id("secondary", source)
    assert evaluate_review(unsafe, source, source)["rejection_code"] == "judgment_source_mismatch"
    unsafe = review(source=source)
    unsafe["judgment_basis"]["condition_quotes"].append(
        {"field": "option", "option_label": "A", "text": "zhí"})
    assert evaluate_review(unsafe, source, source)["rejection_code"] == "condition_quote_not_bound"


@pytest.mark.parametrize("verdict,correct_answer,method", [
    ("correct", None, None),
    ("incorrect", "8", "按括号中的运算先计算，再完成除法。"),
])
def test_agreed_observation_can_be_rejudged_despite_conflicting_original_verdicts(verdict, correct_answer, method):
    first = correct(stem_text="计算4÷(1-1/2)。", answer_text="8", verdict="incorrect",
                    correct_answer=None, correction_method=None)
    second = {**first, "verdict": "correct"}
    # A review is a new, cited judgment, not a tally of original verdicts.
    if verdict == "incorrect":
        first["answer_text"] = second["answer_text"] = "4"
    revised = review(source=first, verdict=verdict, correct_answer=correct_answer,
                     correction_method=method)
    revised["judgment_basis"]["summary"] = "4÷(1-1/2)=8，对照所选源的有效作答判定。"
    outcome = evaluate_review(revised, first, second)
    assert outcome["resolved"] is True
    assert outcome["verdict"] == verdict
    assert outcome["correct_answer"] == correct_answer
    assert outcome["requires_visual_support"] is False


@pytest.mark.parametrize("first_text,second_text", [
    ("-4", "4"), ("1.04", "1.40"), ("zhǐ", "zhí"), ("x^2", "x2"),
    ("5÷(1-5/6-1/8)", "5÷(1-5/6+1/8)"),
])
def test_semantic_claim_does_not_erase_critical_answer_symbols(first_text, second_text):
    first, second = correct(answer_text=first_text), correct(answer_text=second_text)
    outcome = evaluate_review(review(source=first), first, second)
    assert outcome["resolved"] is False
    assert outcome["rejection_code"] == "material_observation_change"


@pytest.mark.parametrize("first_stem,second_stem", [
    ("计算84÷7", "计算28÷7"),
    ("x=-2，求x+1", "x=2，求x+1"),
    ("标注zhǐ的声调", "标注zhí的声调"),
    ("比较x^2与2", "比较x2与2"),
])
def test_semantic_claim_does_not_erase_critical_printed_conditions(first_stem, second_stem):
    first, second = correct(stem_text=first_stem), correct(stem_text=second_stem)
    outcome = evaluate_review(review(source=first), first, second)
    assert outcome["resolved"] is False
    assert outcome["rejection_code"] == "material_condition_change"


def test_prose_equivalence_is_a_claim_requiring_independent_visual_support():
    first = correct(stem_text="甲比乙多4千米，求乙走的路程。")
    second = correct(stem_text="求乙走的路程，已知甲比乙多4千米。")
    outcome = evaluate_review(review(source=first), first, second)
    assert outcome["resolved"] is True
    assert outcome["requires_visual_support"] is True
    # The code cannot prove prose equivalence; even a wrongly asserted
    # comparison must never bypass the independent visual gate.
    contradictory = correct(stem_text="甲比乙少4千米，求乙走的路程。")
    outcome = evaluate_review(review(source=first), first, contradictory)
    assert outcome["requires_visual_support"] is True


def test_image_review_can_resolve_added_printed_heading_with_same_full_expression():
    first = correct(stem_text="5.4÷10=", answer_text="0.54")
    second = correct(stem_text="列竖式计算：5.4÷10=", answer_text="0.54")
    comparison = {"observations_same": True, "conditions_same": True, "difference_type": "presentation",
                  "explanation": "仅有公共标题的抄写范围差异，原图是同一道竖式计算题。"}
    outcome = evaluate_review(review(source=second, selected_source="secondary", comparison=comparison), first, second)
    assert outcome["resolved"] is True and outcome["requires_visual_support"] is False
    # A changed operand is still material even if the heading is identical.
    changed = {**second, "stem_text": "列竖式计算：5.4÷100="}
    assert evaluate_review(review(source=changed, selected_source="secondary", comparison=comparison),
                           first, changed)["rejection_code"] == "material_condition_change"


def test_layout_annotation_is_not_removed_from_archived_student_text():
    first = correct(answer_text="4/5×6=24/5")
    second = correct(answer_text="左列：4/5×6=24/5")
    outcome = evaluate_review(review(source=first), first, second)
    assert outcome["resolved"] is True
    assert outcome["requires_visual_support"] is True
    assert outcome["audit"]["source_observation"]["answer_text"] == first["answer_text"]
    assert second["answer_text"].startswith("左列：")


def test_expanded_printed_context_must_use_image_resolution_not_fake_literal_equivalence():
    first = correct(stem_text="2.17÷0.07=____", answer_text="31")
    second = {**first, "answer_text": "2.17÷0.07=31"}
    assert evaluate_review(review(source=first), first, second)["resolved"] is False
    direct = review(source=first, resolution="visual_resolution",
                    visual_evidence="横式前半为印刷条件，学生只在等号后的空内写31。")
    outcome = evaluate_review(direct, first, second)
    assert outcome["resolved"] is True
    assert outcome["requires_visual_support"] is True
    assert outcome["source"]["answer_text"] == "31"


def test_archived_third_blind_observation_can_correct_two_shared_reading_errors():
    first = correct(stem_text="选择读音正确的一项。", answer_text="A",
                    options=[{"label": "A", "text": "zhǐ"}])
    second = deepcopy(first)
    visual = {key: deepcopy(first[key]) for key in
              ("label", "stem_text", "answer_text", "answer_status", "options", "uncertainties")}
    visual["options"] = [{"label": "A", "text": "zhí"}]
    original = deepcopy((first, second, visual))
    updated = review(source=visual, selected_source="visual", resolution="visual_resolution",
                     verdict="incorrect", correct_answer="正确读音是zhǐ。",
                     correction_method="重新核对该字在本句中的声调。")
    updated["judgment_basis"]["condition_quotes"].append(
        {"field": "option", "option_label": "A", "text": "zhí"})
    outcome = evaluate_review(updated, first, second, visual)
    assert outcome["resolved"] is True
    assert outcome["source"]["options"] == visual["options"]
    assert outcome["verdict"] == "incorrect"
    assert "verdict" not in outcome["source"]
    assert (first, second, visual) == original
    # A model's claimed visual source is not evidence if the caller did not
    # supply an independently archived observation.
    assert evaluate_review(updated, first, second)["rejection_code"] == "missing_source"


def test_uncertain_third_observation_cannot_be_cleared_by_informed_review():
    visual = correct(uncertainties=["声调仍不清楚"])
    outcome = evaluate_review(review(source=visual, selected_source="visual",
                                    resolution="visual_resolution"), correct(), correct(), visual)
    assert outcome["resolved"] is False
    assert outcome["rejection_code"] == "selected_visual_unclear"


def test_review_target_exposes_only_real_sources_and_preserves_inputs():
    first, second = correct(), correct(stem_text="求(-2)²的值")
    original = deepcopy((first, second))
    target = review_target("1", first, second, ["stem_disagreement"])
    assert [candidate["role"] for candidate in target["candidates"]] == ["primary", "secondary"]
    for candidate, source in zip(target["candidates"], (first, second)):
        assert candidate["source_id"] == source_id(candidate["role"], source)
        assert set(candidate) == {"role", "source_id"}
        assert target[candidate["role"]]["stem_text"] == source["stem_text"]
    target["primary"]["stem_text"] = "mutated"
    assert (first, second) == original
    visual = correct(options=[{"label": "A", "text": "zhí"}])
    target = review_target("1", first, second, ["stem_disagreement"], visual)
    assert target["candidates"][-1] == {"role": "visual", "source_id": source_id("visual", visual)}
    target["visual"]["options"].clear()
    assert visual["options"] == [{"label": "A", "text": "zhí"}]


@pytest.mark.parametrize("missing", ["source_id", "observation_comparison", "judgment_basis"])
def test_legacy_or_partial_protocol_fails_closed(missing):
    incomplete = review()
    del incomplete[missing]
    assert evaluate_review(incomplete, correct(), correct())["resolved"] is False
