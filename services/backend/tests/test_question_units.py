"""Experimental unit protocol, using synthetic text and no provider/network."""
from copy import deepcopy

import pytest
from jsonschema import ValidationError, validate

from xueban_backend.providers import question_units as units, reconciliation, two_model, visual_readback, whole_page


def observation(label="3(1)", **changes):
    return {
        "label": label, "stem_text": "正方形边长为a，面积____，周长____。", "options": [],
        "answer_text": "第1空：a^2；第2空：4a", "answer_status": "present", "verdict": "correct",
        "uncertainties": [], "blank_count": 2, "unit_complete": True,
        "target_context": {"section": "三、用代数式表示", "row": None, "column": None, "anchor": "正方形边长为a"},
        **changes,
    }


def bound(label="3(1)", asset="page-a", **changes):
    return units.bind_observation(observation(label, **changes), "question", asset)


def test_new_prompt_replaces_both_inherited_and_local_per_blank_rules():
    original = two_model.PAGE_PROMPT
    prompt = units.page_prompt(original, whole_page.PAGE_RULES, "question")
    for conflict in ("每个独立空格单独一项", "不要把多个空的答案拼成一项",
                     "同一题多个空使用#1、#2", "空位次序仅用label的#1、#2", "correct_answer只写该空最终答案"):
        assert conflict in original and conflict not in prompt
    assert units.QUESTION_RULES in prompt
    assert "逐一核算" in prompt and "负号" in prompt and "[未作答]" in prompt
    assert two_model.PAGE_PROMPT == original
    assert units.page_prompt(original, whole_page.PAGE_RULES, "answer_slot") == original + units.LOCATOR_RULES + units.LEGACY_POSITION_RULES


def test_prompt_drift_is_a_setup_failure_not_silent_conflicting_instructions():
    with pytest.raises(ValueError, match="unit_prompt_base_changed"):
        units.page_prompt(two_model.PAGE_PROMPT.replace(whole_page.PAGE_RULES, ""), whole_page.PAGE_RULES, "question")


def test_blind_and_review_prompts_keep_new_unit_scope_consistent():
    prompt = units.visual_prompt(visual_readback.PROMPT, "question")
    assert "label保持原题号与空位编号" not in prompt
    assert "只有全部位置未写" in prompt and "不解题、不计算、不判对错" in prompt
    prompt = units.review_prompt(reconciliation.PROMPT, "question")
    assert "每题一项，不能合并相邻空" not in prompt
    assert "不能跨输入目标合并或拆分" in prompt and "source_id还绑定完整空数" in prompt


@pytest.mark.parametrize("value", ["question", "answer_slot"])
def test_explicit_and_historical_unit_selection(value):
    assert units.validate_unit(value) == value
    assert units.stored_unit({"target_unit": value}) == value
    assert units.stored_unit(None) == "answer_slot"
    assert units.stored_unit({}) == "answer_slot"


@pytest.mark.parametrize("value", [None, "", "question_slots", True, 1, []])
def test_invalid_units_fail_closed(value):
    with pytest.raises(ValueError, match="invalid_target_unit"):
        units.validate_unit(value)


def test_question_schema_adds_required_observed_count_and_complete_locator_without_mutation():
    original = deepcopy(two_model.PAGE_SCHEMA)
    schema = units.schema_for(original, "question")
    item = schema["properties"]["items"]["items"]
    assert set(units.UNIT_PROPERTIES) <= set(item["required"])
    assert item["properties"]["blank_count"] == {"type": "integer", "minimum": 0, "maximum": 32}
    assert original == two_model.PAGE_SCHEMA
    assert "blank_count" not in original["properties"]["items"]["items"]["properties"]


@pytest.mark.parametrize("count", [0, 1, 2, 32])
def test_new_count_is_explicit_and_never_inferred_from_label_or_underscores(count):
    value = observation("legacy-looking#29", blank_count=count)
    validate(count, units.UNIT_PROPERTIES["blank_count"])
    assert units.public_fields(value, "question")["blank_count"] == count


@pytest.mark.parametrize("count", [-1, 33, True, None, "2"])
def test_invalid_or_truncated_counts_are_not_accepted(count):
    value = observation(blank_count=count)
    with pytest.raises((ValueError, ValidationError)):
        validate(count, units.UNIT_PROPERTIES["blank_count"])
        units.public_fields(value, "question")
    assert "invalid_blank_count" in units.unit_issues(value, "question")


def test_ordered_text_retains_all_six_positions_in_a_continuous_reply():
    all_six = "；".join(f"第{i}空：word{i}" for i in range(1, 7))
    row = observation("2b", blank_count=6, answer_text=all_six)
    assert units.unit_issues(row, "question") == []
    missing = {**row, "answer_text": "；".join(all_six.split("；")[:4])}
    assert "ordered_answers_incomplete" in units.unit_issues(missing, "question")
    duplicate = {**row, "answer_text": all_six.replace("第6空", "第5空")}
    assert "ordered_answers_incomplete" in units.unit_issues(duplicate, "question")
    assert row["answer_text"] == all_six


def test_partial_blank_is_preserved_and_cannot_be_claimed_all_correct():
    row = observation(answer_text="第1空：a^2；第2空：[未作答]")
    assert "partial_blank_marked_correct" in units.unit_issues(row, "question")
    assert units.unit_issues({**row, "verdict": "partially_correct"}, "question") == []
    assert units.unit_issues(observation(answer_text=None, answer_status="blank", verdict="unanswered"), "question") == []


def test_one_unclear_position_keeps_the_whole_question_unclear():
    row = observation(answer_text="第1空：a^2；第2空：[不清]", answer_status="unclear")
    assert "unclear_question_unit" in units.unit_issues(row, "question")
    # Target identity is independent of answer content; pairing must not inspect
    # the answer. The separate safety gate still blocks this whole observation.
    assert units.same_unit_identity(bound(), units.bind_observation(row, "question", "page-a"))


def test_signs_are_not_transferred_between_ordered_positions_or_normalized_away():
    wrong = bound(answer_text="第1空：-；第2空：5", verdict="incorrect")
    correct = bound(answer_text="第1空：-；第2空：-5")
    assert units.unit_issues(wrong, "question") == []  # shape is not a math proof
    assert units.extension_record(wrong) == units.extension_record(correct)
    assert wrong["answer_text"] != correct["answer_text"]  # downstream source hash also binds answer_text


def test_same_label_in_different_sections_or_table_cells_is_not_identity():
    a = bound("1")
    b = bound("1", target_context={**a["target_context"], "section": "普通光学显微镜的使用"})
    assert not units.same_unit_identity(a, b)
    pauline = bound("1c", target_context={"section": "1c", "row": "Pauline", "column": None, "anchor": "Pauline"})
    peter = bound("1c", target_context={"section": "1c", "row": "Peter", "column": None, "anchor": "Peter"})
    assert not units.same_unit_identity(pauline, peter)
    country = bound("1c", target_context={"section": "1c", "row": None, "column": "Country", "anchor": "Country"})
    assert not units.same_unit_identity(pauline, country)


def test_different_pages_are_not_same_target_and_server_binding_does_not_mutate_raw():
    raw = observation()
    original = deepcopy(raw)
    a = units.bind_observation(raw, "question", "page-a")
    b = units.bind_observation(raw, "question", "page-b")
    assert raw == original and "target_unit" not in raw
    assert not units.same_unit_identity(a, b)
    assert units.same_unit_identity(a, b, require_page=False)


def test_short_anchor_variation_is_evidence_not_an_identity_equality_gate():
    a = bound(target_context={"section": None, "row": None, "column": None,
                              "anchor": "计算(-2)^2"})
    b = bound(target_context={**a["target_context"], "anchor": "(-2)^2"})
    assert units.same_unit_identity(a, b)
    assert units.extension_record(a) != units.extension_record(b)


def test_complete_same_multi_blank_question_can_match_but_not_a_partial_observation():
    a, b = bound(), bound("三(1)")
    assert units.same_unit_identity(a, b)
    assert not units.same_unit_identity(a, {**b, "unit_complete": False})
    assert not units.same_unit_identity(a, {**b, "blank_count": 1})
    assert not units.same_unit_identity(a, observation("3(1)#2", blank_count=1))


def test_every_optional_observation_extension_is_bound_verbatim_including_slots():
    item = {**bound(), "answer_slots": [{"index": 1, "answer_text": "a^2"}],
            "visible_locator": {"printed_label": "3(1)", "page_zone": "middle"}}
    original = deepcopy(item)
    extension = units.extension_record(item)
    assert set(extension) == set(units.BOUND_FIELDS)
    extension["answer_slots"][0]["answer_text"] = "private mutation"
    assert item == original
    assert units.extension_record({"stem_text": "old"}) == {}


def test_old_second_blank_cannot_bind_a_whole_question_or_other_slot():
    question = {"label": "3(1)#2", "blank_count": 1}
    annotations = {"label": "3(1)#2"}  # historical record: no new marker
    canonical = whole_page.canonical_label
    assert units.compatible_saved_target(question, annotations, observation("3(1)#2"), canonical)
    assert not units.compatible_saved_target(question, annotations, observation("3(1)#1"), canonical)
    assert not units.compatible_saved_target(question, annotations, bound(), canonical)
    assert not units.compatible_saved_target(question, annotations, observation("3(1)"), canonical)
    assert not units.compatible_saved_target(
        question, {"label": "３（１）＃２"}, observation("3(1)"), canonical)


def test_new_question_rephoto_keeps_original_unit_and_locator_across_asset_change():
    old = bound()
    question = {"label": old["label"], "blank_count": 2}
    annotations = {"label": old["label"], **units.annotation_fields(old)}
    corrected = bound(asset="new-photo", answer_text="第1空：a^2；第2空：4a")
    assert units.compatible_saved_target(question, annotations, corrected, whole_page.canonical_label)
    assert not units.compatible_saved_target(
        question, annotations, {**corrected, "blank_count": 1}, whole_page.canonical_label)


@pytest.mark.parametrize("short,heading", [
    ("1c", "1c Read again and complete the table with the information from the posts."),
    ("1d", "1d Compare either Pauline or Peter with yourself."),
    ("2a", "2a Write some sentences about yourself using the structures below."),
    ("2b", "2b Complete your reply to Pauline or Peter to make friends."),
    ("１c", "1c. Read again"),
    ("一", "一、生物特征"),
    ("（一）", "(一) 生物特征"),
    ("3(1)", "3(1)：用代数式表示"),
])
def test_bare_visible_question_number_and_same_number_plus_heading_are_compatible(short, heading):
    a = bound(target_context={"section": short, "row": None, "column": None, "anchor": "visible anchor"})
    b = bound(target_context={**a["target_context"], "section": heading})
    original = deepcopy((a, b))
    identities = [reconciliation.source_id("primary", a), reconciliation.source_id("secondary", b)]
    assert units.sections_compatible(short, heading)
    assert units.sections_compatible(heading, short)
    assert units.same_unit_identity(a, b)
    assert (a, b) == original
    assert identities == [reconciliation.source_id("primary", a), reconciliation.source_id("secondary", b)]
    # A relation between locators never rewrites either archived observation.
    assert reconciliation.observation_record(a)["target_context"]["section"] == short
    assert reconciliation.observation_record(b)["target_context"]["section"] == heading


@pytest.mark.parametrize("left,right", [
    ("1", "10 Read again"), ("1", "1c Read again"),
    ("1c", "1cc Read again"), ("1c", "1cRead again"),
    ("1", "1(2) Read again"), ("1.2", "1.20 Read again"),
    ("1c Read the table", "1c Read the paragraph"),
    ("一、生物特征", "一、普通光学显微镜的使用"),
    ("1c x=2", "1c x=-2"), ("1c 87.2÷55", "1c 57.2÷55"),
    ("1c 读音zhǐ", "1c 读音zhí"),
    ("Read the table", "Read the table with extra conditions"),
    ("1c", "2c Read again"),
])
def test_prefix_similarity_cannot_erase_a_real_number_title_or_condition_difference(left, right):
    assert not units.sections_compatible(left, right)
    assert not units.sections_compatible(right, left)


def test_heading_expansion_does_not_hide_table_row_or_column_conflict():
    a = bound(target_context={"section": "1c", "row": "Pauline", "column": "Country", "anchor": "Pauline"})
    for change in ({"row": "Peter"}, {"column": "Age"}, {"row": None}):
        b = bound(target_context={**a["target_context"], "section": "1c Read again and complete the table.", **change})
        assert not units.same_unit_identity(a, b)


def test_missing_visual_section_is_unknown_but_does_not_change_scope_or_source_binding():
    a = bound("2a", blank_count=7,
              target_context={"section": "2a", "row": None, "column": None, "anchor": "Write some sentences"})
    visual = {**deepcopy(a), "target_context": {**a["target_context"], "section": None}}
    original = deepcopy(visual)
    identity = reconciliation.source_id("visual", visual)
    assert units.sections_compatible(None, "2a") and units.sections_compatible("2a", None)
    assert units.same_unit_identity(a, visual)
    assert visual == original and visual["target_context"]["section"] is None
    assert identity == reconciliation.source_id("visual", visual)
    # Filling the absent title from another model is still a different source.
    assert identity != reconciliation.source_id("visual", {**visual, "target_context": a["target_context"]})
    for change in ({"blank_count": 6}, {"unit_complete": False}, {"source_asset_id": "other-page"}):
        assert not units.same_unit_identity(a, {**visual, **change})
    row_conflict = {**visual, "target_context": {**visual["target_context"], "row": "Peter"}}
    assert not units.same_unit_identity(a, row_conflict)


def test_omitted_section_does_not_relax_full_observation_comparison_or_answers():
    a = bound("2a", blank_count=0,
              stem_text="My favourite place is ____.", answer_text="Chinese chess",
              target_context={"section": "2a", "row": None, "column": None, "anchor": "My favourite place"})
    visual = {**deepcopy(a), "target_context": {**a["target_context"], "section": None}}
    assert two_model._same_observation(a, visual)
    # The boundary gate cannot erase a different question or replace a readable
    # category error with a plausible place. Correctness requires AI judgment.
    assert not two_model._same_observation(a, {**visual, "stem_text": "My favourite sport is ____."})
    assert not two_model._same_observation(a, {**visual, "answer_text": "my home"})


def test_complete_table_is_an_experimental_unit_without_a_row_claim():
    text = "；".join(f"第{i}空（合成行{i}/Country）：word{i}" for i in range(1, 15))
    a = bound("1c", blank_count=14, answer_text=text,
              target_context={"section": "1c", "row": None, "column": None, "anchor": "Information"})
    b = bound("1c", blank_count=14, answer_text=text,
              target_context={**a["target_context"], "section": "1c Read again and complete the table."})
    assert units.same_unit_identity(a, b) and units.unit_issues(a, "question") == []
    assert units.public_fields(a, "question")["blank_count"] == 14
    assert units.public_fields(a, "question")["target_context"]["row"] is None
    assert "允许同一可见小题要求填写的一张完整表格" in units.QUESTION_RULES
    assert "表格按原有一行" not in units.QUESTION_RULES


def test_blank_and_readable_grammar_errors_do_not_mean_incomplete_or_unreadable():
    row = observation(answer_text="第1空：I live in chess；第2空：[未作答]", verdict="partially_correct")
    assert units.shape_issues(row, "question") == []
    assert units.unit_issues(row, "question") == []
    assert units.unit_issues(observation(answer_text=None, answer_status="blank", verdict="unanswered"), "question") == []
    assert "空白不等于单元不完整" in units.QUESTION_RULES
    assert "语法错误或语义不合理不等于视觉疑点" in units.QUESTION_RULES
    assert "缺少阅读材料导致事实不能判定" in units.QUESTION_RULES
    # Do not auto-delete genuine model uncertainty or auto-promote incomplete
    # observations: prompts guide future reads, they do not rewrite old ones.
    unclear = {**row, "unit_complete": False, "answer_status": "unclear"}
    assert "incomplete_question_unit" in units.unit_issues(unclear, "question")
    assert "unclear_question_unit" in units.unit_issues(unclear, "question")


@pytest.mark.parametrize("left,right", [
    ("3(1)", "3.(1)"), ("3（1）", "３.（１）"),
    ("3(1)", "3. (1)"), ("1", "1."),
    ("3(1)", "3.(1)：计算"),
])
def test_visible_number_decoration_is_not_an_identity_conflict(left, right):
    a = bound(visible_locator={"printed_label": left, "page_zone": "middle_left"})
    b = bound(visible_locator={"printed_label": right, "page_zone": "middle"})
    originals = deepcopy((a, b))
    hashes = (reconciliation.source_id("primary", a), reconciliation.source_id("secondary", b))
    assert units.printed_label_relation(left, right) == "same"
    assert not units.visible_locators_conflict(a, b)
    assert units.same_unit_identity(a, b)
    assert units.complete_printed_labels_match(left, right)
    assert (a, b) == originals
    assert hashes == (reconciliation.source_id("primary", a), reconciliation.source_id("secondary", b))


@pytest.mark.parametrize("left,right", [
    ("一(1)", "一、列竖式计算"), ("一(20)", "一、列竖式计算"),
    ("3(1)", "3"), ("3(1)(2)", "3(1)"),
])
def test_visible_parent_heading_is_unknown_for_child_identity_not_a_conflict(left, right):
    a = bound(visible_locator={"printed_label": left, "page_zone": "top_left"})
    b = bound(visible_locator={"printed_label": right, "page_zone": "top_left"})
    assert units.printed_label_relation(left, right) == "related"
    assert not units.visible_locators_conflict(a, b)
    assert not units.visible_locators_conflict(b, a)
    assert units.same_unit_identity(a, b)  # necessary boundary only
    assert not units.complete_printed_labels_match(left, right)
    assert not units.complete_printed_labels_match(right, left)


@pytest.mark.parametrize("left,right", [
    ("3(1)", "3.(2)"), ("3(1)", "4(1)"), ("1", "2."),
    ("一(1)", "一(2)"), ("一(1)", "二、列竖式计算"),
    ("3.1", "3.2"), ("1c", "1d"),
])
def test_different_explicit_same_level_numbers_still_block_identity(left, right):
    a = bound(visible_locator={"printed_label": left, "page_zone": "middle"})
    b = bound(visible_locator={"printed_label": right, "page_zone": "middle"})
    assert units.printed_label_relation(left, right) == "conflict"
    assert units.visible_locators_conflict(a, b) and units.visible_locators_conflict(b, a)
    assert not units.same_unit_identity(a, b)
    assert not units.complete_printed_labels_match(left, right)


@pytest.mark.parametrize("left,right", [
    (None, "1"), ("", "1"), ("计算", "填空"),
    ("(1)", "3(1)"), ("一", "1"), ("1cRead", "1dRead"),
    ("一、列竖式计算", "一、列竖式计算"),
    ("1 计算", "1"),
])
def test_absence_of_visible_number_conflict_never_proves_a_complete_locator(left, right):
    a = {"visible_locator": {"printed_label": left, "page_zone": "middle"}}
    b = {"visible_locator": {"printed_label": right, "page_zone": "middle"}}
    assert not units.visible_locators_conflict(a, b)
    assert not units.complete_printed_labels_match(left, right)
