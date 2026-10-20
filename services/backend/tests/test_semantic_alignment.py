"""Text-only regression fixtures for unique atomic-target pairing."""

from copy import deepcopy
import json

import pytest
from jsonschema import validate

from xueban_backend.providers.reconciliation import source_id
from xueban_backend.providers.semantic_alignment import (
    SCHEMA, build_alignment_input, can_align, evaluate_alignment,
)


def observation(label="一、1", stem="写出循环小数的简便写法：0.222…", **changes):
    return {
        "label": label, "stem_text": stem, "options": [],
        "answer_text": "PRIVATE_STUDENT_ANSWER", "answer_status": "present",
        "verdict": "correct", "basis_summary": "PRIVATE_JUDGE_RATIONALE",
        "correct_answer": "PRIVATE_REFERENCE_ANSWER", "correction_method": "PRIVATE_METHOD",
        "knowledge_points": ["PRIVATE_KNOWLEDGE"], "uncertainties": [], **changes,
    }


def pair(a, b, **changes):
    def slot(row):
        return int(row["label"].rsplit("#", 1)[1]) if "#" in row["label"] else None
    return {
        "primary_source_id": source_id("primary", a),
        "secondary_source_id": source_id("secondary", b),
        "same_question": True, "same_target": True, "conditions_same": True,
        "match_type": "same_conditions",
        "image_evidence": "原图中同一印刷数式对应一个独立空位，引用可唯一定位。",
        "primary_target_count": 1, "secondary_target_count": 1,
        "primary_slot": slot(a), "secondary_slot": slot(b),
        "primary_quote": a["stem_text"], "secondary_quote": b["stem_text"],
        "explanation": "必要条件和所问目标相同，题号编排不同。", **changes,
    }


def response(*pairs, split_required=(), unmatched=()):
    return {"pairs": list(pairs), "split_required": list(split_required),
            "unmatched_source_ids": list(unmatched)}


def test_input_omits_answers_judgments_and_explanations():
    a, b = observation(), observation("一#2", "把循环小数用简便写法表示：0.222…=____")
    payload = build_alignment_input([a], [b])
    encoded = json.dumps(payload)
    assert "PRIVATE_" not in encoded
    assert "answer_text" not in encoded and "verdict" not in encoded
    assert payload["primary"][0]["source_id"] == source_id("primary", a)
    assert payload["secondary"][0]["slot_hint"] == 2
    payload["primary"][0]["options"].append({"label": "A", "text": "mutation"})
    assert a["options"] == []


def test_chinese_labels_and_different_reading_order_can_pair_by_unique_printed_target():
    a1 = observation("一、1")
    a2 = observation("一、3", "写出循环小数的简便写法：1.32626…")
    b1 = observation("一#1", "把循环小数用简便写法表示：1.32626…=____")
    b2 = observation("一#2", "把循环小数用简便写法表示：0.222…=____")
    original = deepcopy(([a1, a2], [b1, b2]))
    output = response(pair(a1, b2), pair(a2, b1))
    validate(output, SCHEMA)
    result = evaluate_alignment(output, [a1, a2], [b1, b2])
    assert [(p["primary_label"], p["secondary_label"]) for p in result["pairs"]] == [
        ("一、1", "一#2"), ("一、3", "一#1"),
    ]
    assert not result["unmatched_source_ids"]
    assert result["audit"]["independent_judgments_added"] == 0
    assert ([a1, a2], [b1, b2]) == original


def test_row_column_labels_and_linear_labels_do_not_require_equal_array_positions():
    a = observation("2#3", "28.8÷12=____")
    b = observation("1(7)", "列竖式计算：28.8÷12=____")
    result = evaluate_alignment(response(pair(a, b)), [a], [b])
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["canonical_label"] == "2#3"


def test_missing_provider_is_not_recovered_by_semantic_pairing():
    a, absent = observation(), observation("1")
    assert not can_align([a], [])
    assert not can_align([], [absent])
    result = evaluate_alignment(response(pair(a, absent)), [a], [])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == "unknown_source"
    assert result["unmatched_source_ids"] == [source_id("primary", a)]


@pytest.mark.parametrize("field", ["primary_source_id", "secondary_source_id"])
def test_forged_source_id_is_rejected_locally_without_losing_other_valid_pairs(field):
    a1, b1 = observation(), observation("一#1")
    a2, b2 = observation("一、2", "计算28÷7"), observation("一#2", "计算28÷7")
    invalid = pair(a1, b1, **{field: "invented-source"})
    result = evaluate_alignment(response(invalid, pair(a2, b2)), [a1, a2], [b1, b2])
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["primary_label"] == "一、2"
    assert result["rejected"][0]["code"] == "unknown_source"


def test_reused_source_invalidates_all_conflicting_pairs_not_just_the_second():
    a = observation()
    b1, b2 = observation("1"), observation("2", "计算28÷7")
    result = evaluate_alignment(response(pair(a, b1), pair(a, b2)), [a], [b1, b2])
    assert not result["pairs"]
    assert [r["code"] for r in result["rejected"]] == ["source_reused", "source_reused"]
    assert len(result["unmatched_source_ids"]) == 3


@pytest.mark.parametrize("changes,code", [
    ({"same_question": False}, "target_not_confirmed"),
    ({"same_target": False}, "target_not_confirmed"),
    ({"conditions_same": False}, "inconsistent_condition_claim"),
    ({"primary_slot": 1}, "slot_echo_mismatch"),
    ({"primary_target_count": 2}, "split_required"),
    ({"secondary_target_count": 2}, "split_required"),
    ({"explanation": ""}, "missing_target_evidence"),
    ({"image_evidence": ""}, "missing_target_evidence"),
    ({"primary_quote": "模型自造的题干"}, "target_quote_not_unique"),
])
def test_claims_must_bind_a_unique_single_target(changes, code):
    a, b = observation(), observation("1")
    result = evaluate_alignment(response(pair(a, b, **changes)), [a], [b])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == code


def test_one_combined_observation_cannot_confirm_two_separate_blanks():
    combined = observation("五(2)", "循环小数的循环节是____，简便写法是____。")
    single = observation("五(2)#1", combined["stem_text"])
    result = evaluate_alignment(response(pair(combined, single)), [combined], [single])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == "split_required"
    assert result["split_required"][0]["source_id"] == source_id("primary", combined)
    assert result["split_required"][0]["target_count"] == 2


def test_different_explicit_slots_are_not_paired_even_with_identical_whole_stem_and_answers():
    a = observation("五(2)#1", "循环节是____，简便写法是____。")
    b = observation("5(2)#2", a["stem_text"])
    result = evaluate_alignment(response(pair(a, b)), [a], [b])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == "different_slot"


def test_repeated_identical_printed_targets_are_ambiguous_not_deduplicated():
    a1, a2 = observation("一、1"), observation("一、2")
    b = observation("1")
    result = evaluate_alignment(response(pair(a1, b)), [a1, a2], [b])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == "target_quote_not_unique"
    assert len(result["unmatched_source_ids"]) == 3


@pytest.mark.parametrize("first,second", [
    ("计算84÷7", "计算28÷7"),
    ("x=-2，求x+1", "x=2，求x+1"),
    ("辨别zhǐ的声调", "辨别zhí的声调"),
    ("比较x^2", "比较x2"),
])
def test_material_symbols_cannot_be_declared_equivalent(first, second):
    a, b = observation(stem=first), observation("1", second)
    result = evaluate_alignment(response(pair(a, b)), [a], [b])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == "material_condition_difference"


def test_options_are_complete_printed_conditions_including_chinese_and_tones():
    a = observation(options=[{"label": "A", "text": "正确"}])
    b = observation("1", options=[{"label": "A", "text": "错误"}])
    result = evaluate_alignment(response(pair(a, b)), [a], [b])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == "material_condition_difference"


def test_different_readings_can_only_form_a_provisional_relation_without_consuming_sources():
    a, b = observation(stem="84÷7=____"), observation("1", "28÷7=____")
    proposed = pair(a, b, match_type="reading_disagreement", conditions_same=False,
                    image_evidence="两份源疑指第二行第三列同一横式，但被除数读数不同，需独立核读位置。")
    result = evaluate_alignment(response(proposed), [a], [b])
    assert not result["pairs"]
    assert len(result["provisional_pairs"]) == 1
    pending = result["provisional_pairs"][0]
    assert pending["requires_visual_review"] is True
    assert pending["condition_conflict"] is True
    assert pending["merge_allowed"] is False
    assert len(result["unmatched_source_ids"]) == 2


def test_model_requested_split_keeps_the_original_available_for_independent_rereading():
    a, b = observation(), observation("1")
    split = {"source_id": source_id("primary", a), "target_count": 2,
             "source_quote": a["stem_text"], "reason": "原图中疑有两个独立目标被合成一项。"}
    result = evaluate_alignment(response(pair(a, b), split_required=[split]), [a], [b])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == "split_required"
    assert result["split_required"] == [split]
    assert result["unmatched_source_ids"] == [source_id("secondary", b)]


def test_declaring_a_source_both_paired_and_unmatched_is_rejected():
    a, b = observation(), observation("1")
    result = evaluate_alignment(response(pair(a, b), unmatched=[source_id("primary", a)]), [a], [b])
    assert not result["pairs"]
    assert result["rejected"][0]["code"] == "inconsistent_source_disposition"


def test_no_proposal_does_not_silently_drop_any_source():
    a, b = observation(), observation("1")
    result = evaluate_alignment(response(), [a], [b])
    assert not result["pairs"]
    assert set(result["unmatched_source_ids"]) == {source_id("primary", a), source_id("secondary", b)}
