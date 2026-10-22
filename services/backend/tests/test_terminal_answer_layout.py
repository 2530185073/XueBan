"""Answer-line typography cannot erase operands or supply target identity."""
from copy import deepcopy

import pytest

from xueban_backend.providers import reconciliation, review_evidence
from xueban_backend.providers.whole_page import normalize_page_stem


def item(stem, **changes):
    return {"label": "一(7)", "stem_text": stem, "options": [],
            "answer_status": "present", "answer_text": "1.04", "uncertainties": [],
            **changes}


@pytest.mark.parametrize("left,right", [
    ("列竖式计算：57.2÷55=____", "列竖式计算：57.2÷55="),
    ("12÷4=___。", "12÷4=。"),
    ("a+b=__ __", "a+b="),
])
def test_terminal_answer_line_is_layout_without_modifying_raw_source(left, right):
    a, b = item(left), item(right)
    before = deepcopy((a, b))
    assert normalize_page_stem(a) == normalize_page_stem(b)
    assert (a, b) == before
    assert reconciliation.source_id("primary", a) != reconciliation.source_id("primary", b)


@pytest.mark.parametrize("left,right", [
    ("87.2÷55=____", "57.2÷55="),
    ("-4÷2=____", "4÷2="),
    ("1+____=4", "1+=4"),
    ("1+____=____", "1+="),
    ("x_1=____", "x1="),
    ("1+2=_", "1+2="),
    ("1+2=____", "1+2"),
    ("1+2=____=4", "1+2==4"),
    ("填写姓名____", "填写姓名"),
    ("57.2÷55=____（保留一位小数）", "57.2÷55="),
])
def test_substantive_math_or_context_difference_is_not_answer_line_layout(left, right):
    assert normalize_page_stem(item(left)) != normalize_page_stem(item(right))


def test_unnumbered_visual_binds_actual_unchanged_secondary_condition():
    primary = item("87.2÷55=____")
    secondary = item("列竖式计算：57.2÷55=____",
                     visible_locator={"printed_label": "一、列竖式计算", "page_zone": "middle_right"})
    visual = item("列竖式计算：57.2÷55=",
                  answer_text="1.04\n竖式计算过程",
                  visible_locator={"printed_label": "一", "page_zone": "middle_right"})
    outcome = review_evidence.target_link({}, primary, secondary, visual, reconciliation.source_id)
    assert outcome["clear"] is True
    assert outcome["basis"] == "full_original_printed_observation"
    assert outcome["original_source_id"] == reconciliation.source_id("secondary", secondary)
    # Changed operands cannot use the parent heading to claim an identity.
    different = {**visual, "stem_text": "列竖式计算：58.2÷55="}
    assert review_evidence.target_link({}, primary, secondary, different, reconciliation.source_id)["clear"] is False
