"""Only unique full printed content can repair a shifted main question number."""

from copy import deepcopy
import json

import pytest

from test_reconciliation import review, acknowledge_notes
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, item, picture
from xueban_backend.providers.two_model import TwoModelWholePagePipeline, _align_secondary_labels


def expression(label="5#1", **changes):
    return correct(label, stem_text="当a=-6，b=3时，求代数式a^2-2ab+b^2的值", **changes)


def test_unique_full_content_repairs_only_shifted_main_number_and_preserves_source():
    primary = {"5#1": expression()}
    secondary = {"6#1": expression("6#1", answer_text="-4")}
    before = deepcopy(secondary)
    aligned, mappings = _align_secondary_labels(primary, secondary)
    assert list(aligned) == ["5#1"]
    assert aligned["5#1"]["label"] == "6#1"
    assert aligned["5#1"]["answer_text"] == "-4"  # not matched using answer agreement
    assert secondary == before
    assert mappings[0]["secondary_label"] == "6#1"


def test_fixed_heading_normalization_preserves_complete_expression_and_assignment():
    a = correct("4(1)", stem_text="根据x的值，求代数式-5x+9的值：x=0.5")
    b = correct("5(1)", stem_text="根据下列x的值，分别求代数式-5x+9的值：(1)x=0.5")
    aligned, mappings = _align_secondary_labels({"4(1)": a}, {"5(1)": b})
    assert mappings and list(aligned) == ["4(1)"]
    for changed in (
        "根据下列x的值，分别求代数式-5x+9的值：(1)x=-2",
        "根据下列x的值，分别求代数式5x+9的值：(1)x=0.5",
        "根据下列x的值，分别求代数式-5x+9的值：(1)x=0.5且x是整数",
        "求代数式-5x+9的值：x=0.5",
    ):
        assert not _align_secondary_labels({"4(1)": a}, {"5(1)": {**b, "stem_text": changed}})[1]


@pytest.mark.parametrize("change", [
    {"label": "6#2"},
    {"stem_text": "当a=6，b=3时，求代数式a^2-2ab+b^2的值"},
    {"options": [{"label": "A", "text": "不同选项"}]},
    {"stem_text": "当a=[不清]，b=3时，求代数式a^2-2ab+b^2的值"},
    {"subject": "物理"},
])
def test_different_conditions_choices_or_blank_slot_are_not_aligned(change):
    b = {**expression("6#1"), **change}
    assert not _align_secondary_labels({"5#1": expression()}, {b["label"]: b})[1]


def test_shared_full_stems_are_never_resolved_by_order_or_answer():
    stem = "三个连续偶数中间为2n，其余两个数为____、____"
    primary = {label: correct(label, stem_text=stem) for label in ("3(6)#1", "3(6)#2")}
    secondary = {label: correct(label, stem_text=stem) for label in ("4(6)#1", "4(6)#2")}
    assert _align_secondary_labels(primary, secondary)[1] == []
    # Even a duplicate that already has a matching label prevents guessing.
    primary["4(6)#1"] = correct("4(6)#1", stem_text=stem)
    assert _align_secondary_labels(primary, secondary)[1] == []


@pytest.mark.parametrize("first_label,second_label", [("4(1)", "4#1"), ("4#1", "4(1)")])
def test_unique_full_content_allows_single_slot_notation_difference(first_label, second_label):
    a = expression(first_label)
    b = expression(second_label, answer_text="-4")
    aligned, mappings = _align_secondary_labels({first_label: a}, {second_label: b})
    assert len(mappings) == 1 and list(aligned) == [first_label]
    assert aligned[first_label]["answer_text"] == "-4"
    assert aligned[first_label]["label"] == second_label


def test_single_slot_notation_does_not_collapse_other_numbers_composite_or_shared_stems():
    for first_label, second_label in (("4(1)", "4#2"), ("4(1)#2", "4#2"), ("4(1)#2", "4(2)#1")):
        assert not _align_secondary_labels(
            {first_label: expression(first_label)}, {second_label: expression(second_label)})[1]
    primary = {label: expression(label) for label in ("4(1)", "4(2)")}
    secondary = {label: expression(label) for label in ("4#1", "4#2")}
    assert not _align_secondary_labels(primary, secondary)[1]


def test_single_blank_label_can_keep_matching_printed_subquestion_marker_in_fixed_heading():
    a = correct("4(1)", stem_text="根据x的值，求代数式-5x+9的值：x=0.5")
    b = correct("4#1", stem_text="根据下列x的值，分别求代数式-5x+9的值：(1)x=0.5")
    assert _align_secondary_labels({"4(1)": a}, {"4#1": b})[1]
    mismatched_marker = {**b, "stem_text": b["stem_text"].replace("(1)", "(2)")}
    assert not _align_secondary_labels({"4(1)": a}, {"4#1": mismatched_marker})[1]


def test_alignment_preserves_audit_labels_and_routes_visual_review_to_primary_anchor():
    first = expression()
    second = expression("6#1", uncertainties=["该处笔迹尚未确认"])
    flow = TwoModelWholePagePipeline(
        FakeClient("deepseek", {"items": [first]}), FakeClient("glm", {"items": [second]}),
        reconciliation_client=FakeClient("reconciler", {"items": [
            review(source=first, label="5#1", resolution="visual_resolution",
                   observation_issue_reviews=acknowledge_notes({"primary": first, "secondary": second}))]}),
        visual_client=FakeClient("astra", {"items": [item("5#1", stem_text=first["stem_text"], answer_text="4", options=[])]}))
    result = flow.recognize(images=(picture(),))
    assert len(result.data["items"]) == 1
    row = result.data["items"][0]
    assert row["label"] == "5#1" and row["assessment"]["verdict"] == "correct"
    audit = result.data["multimodel_audit"]
    assert audit["calls"][0]["items"][0] == first
    assert audit["calls"][1]["items"][0] == second
    assert audit["label_alignments"][0]["secondary_label"] == "6#1"
    assert json.loads(flow.visual_client.calls[0]["text"])["task_input"]["labels"] == ["5#1"]
    target = json.loads(flow.reconciliation_client.calls[0]["text"])["task_input"]["items"][0]
    assert target["label"] == "5#1" and target["secondary"]["label"] == "6#1"


def test_semantic_pairing_still_reviews_changed_stem_even_when_both_judges_agree():
    from test_semantic_alignment import pair, response
    first = correct("2#3", stem_text="57.2÷55=____", answer_text="1.04")
    second = correct("1(7)", stem_text="列竖式计算：57.2÷55=____", answer_text="1.04")
    def respond(request):
        task = json.loads(request["text"])["task_input"]
        if "primary" in task:
            return response(pair(first, second))
        assert "stem_disagreement" in task["items"][0]["reason_codes"]
        return {"items": [review(source=first, label="2#3", comparison={
            "observations_same": True, "conditions_same": True, "difference_type": "presentation",
            "explanation": "完整短题干被另一源包含，只有公共标题范围不同。"})]}
    client = FakeClient("pairer", respond)
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}), FakeClient("b", {"items": [second]}),
        reconciliation_client=client)
    result = flow.recognize(images=(picture(),))
    assert len(result.data["items"]) == 1
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "correct"
    assert row["assessment"]["verification"]["independent_judgments"] == 2
    assert len(client.calls) == 2
    assert row["assessment"]["verification"]["status"] == "reviewed"
    payload = json.loads(client.calls[0]["text"])["task_input"]
    assert "answer_text" not in json.dumps(payload) and "verdict" not in json.dumps(payload)
    audit = result.data["multimodel_audit"]
    assert audit["calls"][0]["items"][0] == first
    assert audit["calls"][1]["items"][0] == second
    assert audit["label_alignments"][0]["basis"] == "model_same_atomic_target"
    assert [call["stage"] for call in audit["calls"]] == [
        "primary", "secondary", "semantic_alignment", "reconciliation"]


def test_semantic_pairing_cannot_release_chinese_condition_conflict_without_readback():
    from test_semantic_alignment import pair, response
    first = correct("一(1)", stem_text="小红10岁，小明比小红多3岁，求小明年龄。", answer_text="13")
    second = correct("二(1)", stem_text="小红10岁，小明比小红少3岁，求小明年龄。", answer_text="13")
    def respond(request):
        task = json.loads(request["text"])["task_input"]
        if "primary" in task:
            # A mistaken pairing claim must not become an answer judgment.
            return response(pair(first, second))
        return {"items": [review(source=first, label=first["label"])]}
    blind = item(first["label"], stem_text=first["stem_text"], answer_text="[不清]",
                 answer_status="unclear", uncertainties=["字迹不清"], options=[])
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}), FakeClient("b", {"items": [second]}),
        reconciliation_client=FakeClient("pairer", respond),
        visual_client=FakeClient("reader", {"items": [blind]}))
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert row["recognition_status"] == "needs_revision"
    assert "stem_disagreement" in row["assessment"]["verification"]["reason_codes"]
    assert [call["stage"] for call in result.data["multimodel_audit"]["calls"]] == [
        "primary", "secondary", "semantic_alignment", "visual_readback"]
    assert len(flow.reconciliation_client.calls) == 1  # pairing only; unreadable has no adjudication


def test_semantic_pairing_failure_preserves_both_unmatched_observations():
    first = correct("2#3", stem_text="57.2÷55=____", answer_text="1.04")
    second = correct("1(7)", stem_text="列竖式计算：57.2÷55=", answer_text="1.04")
    flow = TwoModelWholePagePipeline(
        FakeClient("a", {"items": [first]}), FakeClient("b", {"items": [second]}),
        reconciliation_client=FakeClient("pairer", {"not_a_pairing_response": True}))
    result = flow.recognize(images=(picture(),))
    assert len(result.data["items"]) == 2
    assert all(row["assessment"]["verdict"] == "unable_to_judge" for row in result.data["items"])
    assert result.metadata.attempts[-1].code == "invalid_output"
