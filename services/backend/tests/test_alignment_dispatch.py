"""No external requests: necessary-condition scheduling, never fuzzy pairing."""
from copy import deepcopy
import hashlib
import json

import pytest

from test_semantic_alignment import observation, pair, response
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers import semantic_alignment as alignment
from xueban_backend.providers.reconciliation import source_id
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


@pytest.mark.parametrize("first,second", [
    ("计算84÷7", "计算28÷7"), ("x=-2，求x+1", "x=2，求x+1"),
    ("辨别zhǐ的声调", "辨别zhí的声调"), ("比较x^2", "比较x2"),
])
def test_all_materially_incompatible_edges_skip_but_provisional_was_never_consumable(first, second):
    a, b = observation(stem=first), observation("2", second)
    original = deepcopy(([a], [b]))
    audit = alignment.alignment_dispatch([a], [b])
    assert audit["decision"] == "not_dispatched" and audit["reason"] == "no_eligible_printed_edge"
    assert audit["candidate_edges"] == audit["excluded_edges"] == 1
    assert audit["compatible_edges"] == 0
    accepted = alignment.evaluate_alignment(response(pair(a, b)), [a], [b])
    assert not accepted["pairs"] and accepted["rejected"][0]["code"] == "material_condition_difference"
    provisional = alignment.evaluate_alignment(response(pair(a, b, match_type="reading_disagreement",
                                                              conditions_same=False)), [a], [b])
    assert not provisional["pairs"] and len(provisional["provisional_pairs"]) == 1
    assert ([a], [b]) == original


def test_changed_option_text_alone_excludes_edge_but_reordered_options_do_not():
    a = observation(options=[{"label": "A", "text": "正确"}, {"label": "B", "text": "错误"}])
    b = observation("2", options=list(reversed(a["options"])))
    assert alignment.alignment_dispatch([a], [b])["decision"] == "dispatch"
    b["options"] = [{"label": "A", "text": "错误"}, {"label": "B", "text": "错误"}]
    assert alignment.alignment_dispatch([a], [b])["reason"] == "no_eligible_printed_edge"


@pytest.mark.parametrize("first,second", [
    ("计算28÷7=____", "列式计算：28÷7="),
    ("x²", "比较x^2"), ("小明比小红多3岁", "小明比小红少3岁"),
    ("选择合适的词语", "选择不合适的词语"), ("", ""),
])
def test_necessary_signature_does_not_add_new_semantics(first, second):
    a, b = observation("unknown", first), observation("第1题", second,
        answer_text="different", verdict="incorrect", blank_count=8)
    assert alignment.alignment_dispatch([a], [b])["decision"] == "dispatch"


@pytest.mark.parametrize("target_unit", ["answer_slot", "question"])
def test_multiblank_and_slot_conflicts_are_not_new_dispatch_vetoes(target_unit):
    a = observation("1#1", "数是____，数字是____。", blank_count=2)
    b = observation("2#2", a["stem_text"], blank_count=2)
    assert alignment.alignment_dispatch([a], [b], target_unit=target_unit)["decision"] == "dispatch"


def test_every_compatible_edge_is_kept_in_small_cartesian_matrix():
    left = [observation(str(i), f"计算{i}+1") for i in range(1, 5)]
    right = [observation(str(i+10), f"求值{i}+1") for i in (1, 2, 7, 8)]
    audit = alignment.alignment_dispatch(left, right)
    assert audit["decision"] == "dispatch" and audit["candidate_edges"] == 16
    assert audit["compatible_edges"] == 2 and audit["excluded_edges"] == 14
    accepted = 0
    for a in left:
        for b in right:
            # One proposal at a time: batched fake edges would fail source_reused.
            accepted += len(alignment.evaluate_alignment(response(pair(a, b)), left, right)["pairs"])
    assert accepted == 2


def test_80_by_80_precomputes_regex_signatures_once_per_row(monkeypatch):
    count = {"literals": 0, "options": 0}
    literals, options = alignment._printed_literals, alignment._options
    def spy_literals(row):
        count["literals"] += 1
        return literals(row)
    def spy_options(row):
        count["options"] += 1
        return options(row)
    monkeypatch.setattr(alignment, "_printed_literals", spy_literals)
    monkeypatch.setattr(alignment, "_options", spy_options)
    left = [observation(str(i), f"计算{i}+1") for i in range(80)]
    right = [observation(str(i), f"计算{i+100}+1") for i in range(80)]
    audit = alignment.alignment_dispatch(left, right)
    assert audit["candidate_edges"] == audit["excluded_edges"] == 6400
    assert count == {"literals": 160, "options": 160}


def test_evaluator_uses_the_same_signature_and_compatibility_helper(monkeypatch):
    a, b = observation(), observation("2")
    signature = alignment.material_condition_signature
    calls = []
    def record(row):
        calls.append(row)
        return signature(row)
    monkeypatch.setattr(alignment, "material_condition_signature", record)
    monkeypatch.setattr(alignment, "material_conditions_compatible", lambda _a, _b: False)
    assert alignment.alignment_dispatch([a], [b])["decision"] == "not_dispatched"
    result = alignment.evaluate_alignment(response(pair(a, b)), [a], [b])
    assert result["rejected"][0]["code"] == "material_condition_difference"
    assert len(calls) == 4


@pytest.mark.parametrize("malformed", [None, {}, [None], [{"label": "1"}],
    [observation(stem=None)], [observation(options="not a list")],
    [observation(options=[{"label": "A", "text": 1}])]])
def test_malformed_inputs_fall_back_instead_of_claiming_all_vetoed(malformed):
    audit = alignment.alignment_dispatch(malformed, [observation("2", "计算28÷7")])
    if malformed == {}:
        assert audit["decision"] == "not_dispatched" and audit["reason"] == "empty_source_side"
    else:
        assert audit["decision"] == "dispatch" and audit["reason"] == "eligibility_check_unavailable"
        assert "excluded_edges" not in audit


def test_duplicate_source_or_hash_failure_falls_back_without_optimizing(monkeypatch):
    a, b = observation(), observation("2", "计算28÷7")
    assert alignment.alignment_dispatch([a, a], [b])["reason"] == "eligibility_check_unavailable"
    monkeypatch.setattr(alignment, "source_id", lambda *_: (_ for _ in ()).throw(RuntimeError("private")))
    audit = alignment.alignment_dispatch([a], [b])
    assert audit["decision"] == "dispatch" and "private" not in str(audit)


def test_audit_binds_full_order_independent_source_sets_without_raw_content():
    left = [observation("1", "计算28÷7"), observation("2", "计算84÷7")]
    right = [observation("3", "计算12÷3")]
    original = deepcopy((left, right))
    audit = alignment.alignment_dispatch(left, right)
    ids = sorted([source_id("primary", row) for row in left]+[source_id("secondary", row) for row in right])
    expected = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
    assert audit["source_set_sha256"] == expected
    assert audit["input_sources"]["primary"]["count"] == 2 and audit["input_sources"]["secondary"]["count"] == 1
    assert alignment.alignment_dispatch(list(reversed(left)), right) == audit
    assert "PRIVATE_" not in str(audit) and "计算" not in str(audit)
    assert (left, right) == original


def test_skip_records_separate_dispatch_audit_without_call_attempt_or_usage():
    a, b = correct("1", stem_text="计算84÷7"), correct("2", stem_text="计算28÷7")
    client = FakeClient("pairer", RuntimeError("alignment must not dispatch"))
    pipeline = TwoModelWholePagePipeline(FakeClient("primary", {"items": [a]}),
                                        FakeClient("secondary", {"items": [b]}), reconciliation_client=client)
    result = pipeline.recognize(images=(picture(),))
    assert not client.calls
    audit = result.data["multimodel_audit"]
    assert [call["stage"] for call in audit["calls"]] == ["primary", "secondary"]
    assert audit["alignment_dispatches"][0]["decision"] == "not_dispatched"
    assert audit["alignment_dispatches"][0]["reason"] == "no_eligible_printed_edge"
    assert len(result.metadata.attempts) == 2 and result.metadata.usage.total_tokens == 60
    assert audit["calls"][0]["items"] == [a] and audit["calls"][1]["items"] == [b]
    assert len(result.data["items"]) == 2 and all(row["recognition_status"] == "needs_revision" for row in result.data["items"])


def test_one_compatible_edge_sends_every_original_row_without_changing_unique_quote_scope():
    first = [correct("1", stem_text="求28÷7"), correct("2", stem_text="求28÷7，再加1")]
    second = [correct("3", stem_text="计算28÷7"), correct("4", stem_text="计算12÷3")]
    before = deepcopy((first, second))
    client = FakeClient("pairer", response(pair(first[0], second[0])))
    pipeline = TwoModelWholePagePipeline(FakeClient("primary", {"items": first}),
                                        FakeClient("secondary", {"items": second}), reconciliation_client=client)
    result = pipeline.recognize(images=(picture(),))
    assert len(client.calls) == 1
    request = client.calls[0]
    assert json.loads(request["text"])["task_input"] == alignment.build_alignment_input(first, second)
    assert request["images"] == (picture().data_url,)
    audit = result.data["multimodel_audit"]
    assert audit["alignment_dispatches"][0]["compatible_edges"] == 1
    call = next(call for call in audit["calls"] if call["stage"] == "semantic_alignment")
    assert call["alignment"]["rejected"][0]["code"] == "target_quote_not_unique"
    assert not audit["label_alignments"] and len(result.data["items"]) == 4
    assert len(result.metadata.attempts) == 3 and result.metadata.usage.total_tokens == 90
    assert (first, second) == before
