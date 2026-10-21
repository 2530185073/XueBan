"""A narrow copula routing rule still requires the real source-bound review."""
from copy import deepcopy

import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_two_model_reconciliation import pipeline
from test_whole_page_pipeline import picture
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers.reconciliation import evaluate_review, power_property_presentation_same, source_id
from xueban_backend.providers.two_model import _needs_condition_readback


def pair(power="(-1/2)^3", prop="底数", first="是", second=""):
    shared = dict(question_type="fill_blank", answer_text="-1/2", uncertainties=[], options=[])
    return (correct("3#5", stem_text=f"填空：乘方{power}的{prop}{first}____", **shared),
            correct("3#5", stem_text=f"填空：乘方{power}的{prop}{second}____。", **shared))


def proposal(source, **changes):
    item = review(source=source, label=source["label"], **changes)
    item["observation_comparison"]["difference_type"] = "presentation"
    return item


@pytest.mark.parametrize("power", ["6^5", "(-5)^4", "(-1/2)^3", "2^7"])
@pytest.mark.parametrize("prop", ["底数", "指数"])
@pytest.mark.parametrize("first,second", [("是", ""), ("为", ""), ("是", "为")])
def test_numeric_power_property_copula_waits_for_bound_image_review(power, prop, first, second):
    a, b = pair(power, prop, first, second)
    original = deepcopy((a, b)); ids = source_id("primary", a), source_id("secondary", b)
    assert power_property_presentation_same(a, b)
    assert not _needs_condition_readback(a, b)
    result = evaluate_review(proposal(a), a, b)
    assert result["resolved"] and not result["requires_visual_support"]
    assert result["audit"]["condition_presentation_rule"] == "numeric_power_property_copula_v1"
    assert result["source"] == a and result["source_id"] == ids[0]
    assert (a, b) == original and ids == (source_id("primary", a), source_id("secondary", b))


@pytest.mark.parametrize("stem", [
    "填空：乘方(-1/2)^3的指数____", "填空：乘方(-1/2)^4的底数____",
    "填空：乘方(1/2)^3的底数____", "填空：乘方-1/2^3的底数____",
    "填空：乘方(-2/1)^3的底数____", "填空：乘方(-1/2)^3的底数不是____",
    "填空：乘方(-1/2)^3的底数不为____", "填空：乘方(-1/2)^3的底数至少为____",
    "填空：乘方(-1/2)^3的底数是否为____", "填空：乘方(-1/2)^3的底数是正数____",
    "填空：乘方(-1/2)^3的底数的相反数为____", "填空：乘方(-1/2)^3的底数的倒数为____",
    "填空：乘方(-1/2)^3的底数____，指数____", "填空：乘方(-1/2)^3的底数____且取正数",
    "填空：乘方(-1/2)^3的底数____（填入给定值）", "乘方(-1/2)^3的底数____",
    "不是填空：乘方(-1/2)^3的底数____", "填空：乘方(-1/2)^3的底数是为____",
])
def test_copula_rule_preserves_the_entire_condition(stem):
    a, b = pair(); b["stem_text"] = stem
    assert not power_property_presentation_same(a, b)
    assert _needs_condition_readback(a, b)
    result = evaluate_review(proposal(a), a, b)
    assert not result["resolved"] or result["requires_visual_support"]


@pytest.mark.parametrize("change", [
    {"label": "3#6"}, {"label": ""}, {"label": "表格"}, {"label": None},
    {"question_type": "calculation"}, {"target_unit": "question"},
    {"uncertainties": ["所属列不清"]}, {"options": [{"label": "A", "text": "1"}]},
    {"visible_locator": {"printed_label": "4", "page_zone": "top_left"}},
])
def test_table_role_label_protocol_and_uncertainty_are_not_silently_merged(change):
    a, b = pair()
    a["visible_locator"] = {"printed_label": "3", "page_zone": "top_left"}
    b.update(change)
    assert not power_property_presentation_same(a, b)


@pytest.mark.parametrize("resolution,difference", [
    ("non_material", "presentation"), ("equivalent", "paraphrase"), ("equivalent", "none"),
])
def test_only_explicit_equivalent_presentation_can_waive_visual(resolution, difference):
    a, b = pair(); p = proposal(a, resolution=resolution)
    p["observation_comparison"]["difference_type"] = difference
    result = evaluate_review(p, a, b)
    assert result["resolved"] and result["requires_visual_support"]
    assert "condition_presentation_rule" not in result["audit"]


@pytest.mark.parametrize("bad", ["source", "quote", "answer", "conditions", "observations", "same_question"])
def test_copula_never_bypasses_real_review_evidence(bad):
    a, b = pair(); p = proposal(a)
    if bad == "source": p["source_id"] = "primary:" + "0" * 64
    if bad == "quote": p["judgment_basis"]["condition_quotes"] = []
    if bad == "answer": p["answer_text"] = "1/2"
    if bad == "conditions": p["observation_comparison"]["conditions_same"] = False
    if bad == "observations": p["observation_comparison"]["observations_same"] = False
    if bad == "same_question": p["same_question"] = False
    assert not evaluate_review(p, a, b)["resolved"]


def test_selected_visual_still_requires_visual_support():
    a, b = pair(); visual = deepcopy(a)
    result = evaluate_review(proposal(visual, selected_source="visual"), a, b, visual=visual)
    assert result["resolved"] and result["requires_visual_support"]
    assert "condition_presentation_rule" not in result["audit"]


def test_no_direct_ready_and_existing_image_review_is_consumed_without_extra_read():
    a, b = pair(); original = deepcopy((a, b))
    flow = pipeline([a], [b], ProviderError("offline_unavailable"))
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
    assert len(flow.reconciliation_client.calls) == 1 and flow.visual_client.calls == []
    flow = pipeline([a], [b], {"items": [proposal(a)]})
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["recognition_status"] == "ready"
    assert row["assessment"]["verification"]["status"] == "reviewed"
    assert row["answer_text"] == a["answer_text"] and row["stem_text"] == a["stem_text"]
    assert len(flow.reconciliation_client.calls) == 1 and flow.visual_client.calls == []
    calls = result.data["multimodel_audit"]["calls"]
    assert calls[0]["items"] == [a] and calls[1]["items"] == [b]
    assert (a, b) == original
