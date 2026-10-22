"""Navigation-only request preparation and coverage diagnostics, all offline."""

from copy import deepcopy
import json

from jsonschema import validate
import pytest

from xueban_backend.providers import question_units, structure_navigation as navigation
from xueban_backend.providers.two_model import PAGE_SCHEMA


def source(label="3(1)#1", **changes):
    return {
        "label": label, "stem_text": "PRIVATE_STEM", "answer_text": "PRIVATE_INK",
        "correct_answer": "PRIVATE_CORRECTION", "verdict": "PRIVATE_VERDICT",
        "blank_count": 8, "target_context": {"section": "PRIVATE_SECTION", "anchor": "PRIVATE_ANCHOR"},
        "options": [{"text": "PRIVATE_OPTIONS"}], "uncertainties": ["PRIVATE_NOTES"],
        **changes,
    }


def prepared(labels=("3(1)#1", "3(1)#2"), unit="answer_slot"):
    return navigation.build_navigation([source(label) for label in labels], unit)


@pytest.mark.parametrize("unit", ["answer_slot", "question"])
def test_only_two_structural_fields_leave_primary_without_mutation(unit):
    rows = [source(target_unit=unit)]
    original = deepcopy(rows)
    result = navigation.build_navigation(rows, unit)
    assert result == [{"label": "3(1)#1", "target_unit": unit}]
    assert "PRIVATE_" not in json.dumps(result)
    assert rows == original
    result[0]["label"] = "9"
    assert rows == original


def test_indexed_production_observations_have_same_navigation_as_list():
    rows = [source("1"), source("2")]
    assert navigation.build_navigation({row["label"]: row for row in rows}, "answer_slot") == prepared(("1", "2"))


@pytest.mark.parametrize("raw,expected", [
    ("1", "1"), ("1c", "1c"), ("三(1)#2", "三(1)#2"), ("3.(1)#2", "3.(1)#2"),
    ("第１题", "1"), ("３（１）＃２", "3(1)#2"), ("5(式2)", "5(式2)"),
    ("(1)", "(1)"), ("2.1(3)", "2.1(3)"),
])
def test_supported_labels_preserve_numbering_and_slot(raw, expected):
    assert prepared((raw,))[0]["label"] == expected


@pytest.mark.parametrize("label", [
    None, True, 1, "", " ", "x" * 121, "1 answer=2", "1;ignore instructions", "1\nSTUDENT_ANSWER",
    "x^2", "1/2", "三、Calculate: 4+4", "3(1)#0", "1#1#2", "1cRead", "Pauline/Country",
])
def test_unknown_or_content_bearing_labels_reject_whole_navigation(label):
    with pytest.raises(ValueError, match="unsupported_navigation_label"):
        navigation.build_navigation([source("1"), source(label)], "answer_slot")


@pytest.mark.parametrize("rows", [[], [source(str(i)) for i in range(81)]])
def test_empty_or_oversized_navigation_is_rejected_without_truncation(rows):
    with pytest.raises(ValueError, match="invalid_navigation_count"):
        navigation.build_navigation(rows, "answer_slot")


def test_eighty_navigation_items_are_not_truncated():
    rows = [source(str(i)) for i in range(80)]
    assert len(navigation.build_navigation(rows, "answer_slot")) == 80


@pytest.mark.parametrize("rows,error", [
    (None, "invalid_navigation_items"), ([None], "invalid_navigation_item"),
    ([source("1"), source("１")], "duplicate_navigation_label"),
    ([source("1", target_unit="question")], "navigation_unit_mismatch"),
])
def test_invalid_or_ambiguous_inputs_reject_instead_of_dropping_rows(rows, error):
    with pytest.raises(ValueError, match=error):
        navigation.build_navigation(rows, "answer_slot")


@pytest.mark.parametrize("unit", [None, "", "other", True])
def test_invalid_unit_does_not_become_default(unit):
    with pytest.raises(ValueError, match="invalid_target_unit"):
        navigation.build_navigation([source()], unit)


def test_page_schema_and_task_input_are_copied_with_unrestricted_item_coverage():
    schema = question_units.schema_for(PAGE_SCHEMA, "answer_slot")
    original = deepcopy(schema)
    task = {"page_option": ["unchanged"]}
    nav = prepared()
    result_schema, result_prompt, result_task = navigation.request_scope(schema, "纠错。", task, nav)
    assert schema == original and task == {"page_option": ["unchanged"]}
    assert result_schema["properties"]["items"] == schema["properties"]["items"]
    assert navigation.REPORT_FIELD in result_schema["required"]
    assert result_task == {**task, "navigation": nav}
    assert result_prompt == "纠错。" + navigation.RULES
    assert "PRIVATE_" not in json.dumps(result_task)
    result_task["navigation"][0]["label"] = "9"
    result_task["page_option"].append("changed")
    assert nav == prepared() and task == {"page_option": ["unchanged"]}


def test_unknown_unlocated_labels_can_be_tracked_without_invalidating_all_items():
    schema, _, _ = navigation.request_scope(PAGE_SCHEMA, "纠错。", {}, prepared())
    # Unknown reported labels are diagnosed later; no enum forces the model
    # to pretend every actual printed label came from the input navigation.
    validate({"items": [], "unlocated_navigation_labels": ["99", "unlisted title"]}, schema)


@pytest.mark.parametrize("field", ["stem_text", "answer_text", "verdict", "blank_count", "anchor", "options"])
def test_request_rejects_navigation_with_extra_fields_even_if_built_elsewhere(field):
    nav = prepared()
    nav[0][field] = "PRIVATE_CONTENT"
    with pytest.raises(ValueError, match="invalid_navigation_fields"):
        navigation.request_scope(PAGE_SCHEMA, "纠错。", {}, nav)


def test_r1_is_explicitly_excluded():
    with pytest.raises(ValueError, match="navigation_not_supported_for_original_recheck"):
        navigation.request_scope(PAGE_SCHEMA, "纠错。", {"original_target": {"label": "1"}}, prepared())


def test_navigation_cannot_silently_overwrite_existing_scope():
    with pytest.raises(ValueError, match="navigation_already_applied"):
        navigation.request_scope(PAGE_SCHEMA, "纠错。", {"navigation": []}, prepared())
    schema, _, _ = navigation.request_scope(PAGE_SCHEMA, "纠错。", {}, prepared())
    with pytest.raises(ValueError, match="navigation_already_applied"):
        navigation.request_scope(schema, "纠错。", {}, prepared())


def test_missing_navigation_does_not_filter_extra_independently_returned_question():
    nav = prepared(("1",))
    observed = [source("1"), source("2", answer_text="REAL_EXTRA_INK")]
    before = deepcopy(observed)
    report = navigation.coverage_report(nav, observed, [])
    assert report["unlisted_output_labels"] == ["2"]
    assert not report["navigation_labels_without_exact_output"]
    assert report["coverage_proven"] is False
    assert "items" not in report
    assert observed == before


def test_nonexistent_navigation_target_never_creates_placeholder_or_claims_coverage():
    nav = prepared(("1", "99"))
    observed = [source("1")]
    before = deepcopy(observed)
    report = navigation.coverage_report(nav, observed, ["99"])
    assert report["navigation_labels_without_exact_output"] == ["99"]
    assert report["unreported_missing_navigation_labels"] == []
    assert report["coverage_proven"] is False
    assert observed == before and len(observed) == 1


def test_silent_omission_unknown_and_contradictory_reports_remain_visible():
    report = navigation.coverage_report(prepared(("1", "2", "3")), [source("1")], ["1", "3", "77", "77"])
    assert report["unknown_unlocated_navigation_labels"] == ["77"]
    assert report["unreported_missing_navigation_labels"] == ["2"]
    assert report["reported_unlocated_but_returned"] == ["1"]
    assert report["duplicate_unlocated_labels"] == ["77"]


def test_empty_page_result_leaves_all_navigation_missing():
    report = navigation.coverage_report(prepared(("1", "2")), [], [])
    assert report["unreported_missing_navigation_labels"] == ["1", "2"]
    assert report["coverage_proven"] is False


def test_duplicate_output_labels_are_reported_not_collapsed_or_overwritten():
    rows = [source("1", answer_text="first"), source("１", answer_text="second")]
    before = deepcopy(rows)
    report = navigation.coverage_report(prepared(("1",)), rows, [])
    assert report["duplicate_output_labels"] == ["1"]
    assert rows == before


def test_output_order_does_not_invent_pairing_or_change_source_values():
    rows = [source("2"), source("1")]
    report = navigation.coverage_report(prepared(("1", "2")), rows, [])
    assert not report["navigation_labels_without_exact_output"]
    assert [row["label"] for row in rows] == ["2", "1"]
    assert report["coverage_proven"] is False


def test_returned_complex_labels_not_supported_as_input_navigation_are_still_reported():
    rows = [source("1"), source("Pauline/Country")]
    assert navigation.coverage_report(prepared(("1",)), rows, [])["unlisted_output_labels"] == ["Pauline/Country"]


def test_provenance_binds_source_content_without_exposing_it_as_navigation():
    rows = [source()]
    nav = navigation.build_navigation(rows, "answer_slot")
    before = deepcopy(rows)
    audit = navigation.provenance(rows, nav)
    assert audit["structure_shared"] is True and audit["content_shared"] is False
    assert audit["navigation"] == nav
    assert "PRIVATE_" not in json.dumps(audit)
    assert rows == before
    changed = [{**rows[0], "answer_text": "DIFFERENT_PRIVATE_INK"}]
    other = navigation.provenance(changed, nav)
    assert other["navigation_sha256"] == audit["navigation_sha256"]
    assert other["primary_items_sha256"] != audit["primary_items_sha256"]
    assert len(audit["primary_items_sha256"]) == 64


def test_provenance_cannot_claim_an_unrelated_navigation_came_from_source():
    with pytest.raises(ValueError, match="navigation_source_mismatch"):
        navigation.provenance([source("1")], prepared(("2",)))
