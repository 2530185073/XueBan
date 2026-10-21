"""Complete numeric conditions can bind a fuller read without deleting method instructions."""
from copy import deepcopy

import pytest

from test_review_evidence import raw_visual, run
from test_two_model_pipeline import correct
from xueban_backend.providers import reconciliation as rec, review_evidence


def observations():
    primary = correct(label="一(7)", stem_text="87.2÷55=____", answer_text="1.04",
        visible_locator={"printed_label": "一(7)", "page_zone": "middle_left"})
    secondary = correct(label="一(7)", stem_text="57.2÷55=____", answer_text="1.04",
        visible_locator={"printed_label": None, "page_zone": "middle_right"})
    selected = raw_visual(secondary, stem_text="列竖式计算：57.2÷55=",
        answer_text="商：1.04；竖式：55，2.20，2.20，0",
        visible_locator={"printed_label": "一", "page_zone": "middle_right"})
    return primary, secondary, selected


def test_full_numeric_body_binds_secondary_with_retained_selected_heading():
    primary, secondary, selected = observations()
    before = deepcopy((primary, secondary, selected))
    hashes = [rec.source_id(role, row) for role, row in
              zip(("primary", "secondary", "visual"), before)]
    result = review_evidence.target_link({}, primary, secondary, selected, rec.source_id)
    assert result["clear"] is True
    assert result["basis"] == "full_original_calculation_with_retained_heading"
    assert result["original_source_id"] == hashes[1]
    assert result["selected_source_id"] == hashes[2]
    assert result["printed_expression"] == "57.2÷55="
    assert result["retained_selected_heading"] == "列竖式计算"
    assert (primary, secondary, selected) == before
    assert [rec.source_id(role, row) for role, row in
            zip(("primary", "secondary", "visual"), before)] == hashes


def test_pipeline_still_needs_bound_visual_judgment_and_preserves_full_process():
    primary, secondary, selected = observations()
    result, flow = run(primary, secondary, selected)
    row = result.data["items"][0]
    assert row["recognition_status"] == "ready"
    assert row["stem_text"] == selected["stem_text"]
    assert row["answer_text"] == selected["answer_text"]
    assert len(flow.judgment_client.calls) == 1
    binding = result.data["multimodel_audit"]["calls"][-1]["applications"][0]["evidence_binding"]
    assert binding["target_identity"]["original_source_id"] == rec.source_id("secondary", secondary)


@pytest.mark.parametrize("changes", [
    {"stem_text": "列竖式计算：57.2÷56="},
    {"stem_text": "列竖式计算：-57.2÷55="},
    {"stem_text": "列竖式计算：55÷57.2="},
    {"stem_text": "列竖式计算：57.2×55="},
    {"stem_text": "列竖式计算：57.2÷55=（保留一位小数）"},
    {"stem_text": "列竖式计算并取倒数：57.2÷55="},
    {"stem_text": "列竖式计算，结果取负：57.2÷55="},
    {"stem_text": "列竖式计算：57.2÷55=，求结果的平方"},
    {"stem_text": "列竖式计算57.2÷55="},
    {"stem_text": "不是列竖式计算：57.2÷55="},
    {"stem_text": "估算：57.2÷55="},
    {"options": [{"label": "A", "text": "1.04"}]},
    {"visible_locator": {"printed_label": "一", "page_zone": "middle_left"}},
    {"visible_locator": {"printed_label": "一", "page_zone": None}},
    {"visible_locator": {"printed_label": "二", "page_zone": "middle_right"}},
    {"visible_locator": None},
    {"source_asset_id": "different-page"},
])
def test_changed_conditions_unknown_location_and_other_page_cannot_borrow_identity(changes):
    primary, secondary, selected = observations()
    secondary["source_asset_id"] = "original-page"
    # A known different heading is comparable only when independently present.
    secondary["visible_locator"]["printed_label"] = "一"
    selected.update(changes)
    assert not review_evidence.target_link({}, primary, secondary, selected, rec.source_id)["clear"]


@pytest.mark.parametrize("original,selected", [
    ("用竖式计算：57.2÷55=", "计算：57.2÷55="),
    ("列竖式计算：57.2÷55=", "57.2÷55="),
    ("估算：57.2÷55=", "计算：57.2÷55="),
    ("57.2÷55=（保留一位小数）", "计算：57.2÷55="),
    ("57.2÷55=1.04", "计算：57.2÷55=1.04"),
    ("a÷55=", "计算：a÷55="),
    ("57.2÷____=", "计算：57.2÷____="),
    ("1 2+3=", "计算：1 2+3="),
    ("1+(2=", "计算：1+(2="),
    ("1+2+=", "计算：1+2+="),
    ("57.2=", "计算：57.2="),
    ("1+2=", "计算：(1+2)="),
    ("1+2=", "计算：3="),
])
def test_no_method_deletion_answer_evaluation_or_incomplete_expression_matching(original, selected):
    primary, secondary, visual = observations()
    secondary["stem_text"] = original
    visual["stem_text"] = selected
    assert not review_evidence.target_link({}, primary, secondary, visual, rec.source_id)["clear"]


def test_student_answer_is_never_used_as_an_identity_key():
    primary, secondary, visual = observations()
    first = review_evidence.target_link({}, primary, secondary, visual, rec.source_id)
    secondary["answer_text"] = "99"
    visual["answer_text"] = "完全不同的学生笔迹"
    second = review_evidence.target_link({}, primary, secondary, visual, rec.source_id)
    assert first["clear"] and second["clear"]
    assert first["printed_expression"] == second["printed_expression"]
    assert first["original_source_id"] != second["original_source_id"]


@pytest.mark.parametrize("change", [
    lambda out, _: out.update(same_question=False),
    lambda out, _: out.update(observation_basis="semantic_only"),
    lambda out, _: out.update(answer_text="1.04"),
    lambda out, _: out.update(source_id="visual:unbound"),
])
def test_numeric_identity_never_bypasses_existing_review_authority(change):
    result, _ = run(*observations(), review_change=change)
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
