"""Offline tests for source identity, typed notes and complete ordered reads."""
from copy import deepcopy
import hashlib
import json

import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers import question_units as units, reconciliation as rec
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def locator(label="1", zone="middle"):
    return {"printed_label": label, "page_zone": zone}


def identity(original, selected, original_role="primary"):
    if not original.get("visible_locator") or not selected.get("visible_locator"):
        return None
    return {"original_source_id": rec.source_id(original_role, original),
            "selected_source_id": rec.source_id("visual", selected),
            "original_quote": original["stem_text"], "selected_quote": selected["stem_text"],
            "original_locator": deepcopy(original.get("visible_locator")),
            "selected_locator": deepcopy(selected.get("visible_locator")),
            "image_evidence": "原图中部同一可见小题号及其题干位置对应；按两份原文逐项核对。"}


def classify(role, source, kind="presentation", resolved=True):
    return [{"source_id": rec.source_id(role, source), "note_index": i, "note_quote": note,
             "kind": kind, "resolved": resolved, "evidence": "逐项核对原图与该说明，记录对应类别；实际原文没有被改写。"}
            for i, note in enumerate(source.get("uncertainties", []))]


def raw_visual(row, **changes):
    return {k: deepcopy(v) for k, v in {**row, **changes}.items() if k in
        {"label", "stem_text", "options", "answer_text", "answer_status", "uncertainties",
         "visible_locator", "blank_count", "unit_complete", "target_context"}}


def question(**changes):
    return {**correct(stem_text="正方形边长a，填写面积和周长。", answer_text="第1空：a²；第2空：4a"),
            "blank_count": 2, "unit_complete": True,
            "target_context": {"section": "3", "row": None, "column": None, "anchor": "正方形边长a"}, **changes}


def run(p, s, visual, classify_kind="presentation", review_change=None, unit="answer_slot"):
    def adjudicate(request):
        t = json.loads(request["text"])["task_input"]["items"][0]
        v = t["visual"]
        notes = [entry for role in ("primary", "secondary", "visual", "critical_visual") if role in t
                 for entry in classify(role, t[role], kind=classify_kind,
                                       resolved=classify_kind in {"presentation", "answer_semantics"})]
        out = review(source=v, label=t["label"], selected_source="visual", resolution="visual_resolution",
                     observation_issue_reviews=notes, target_identity=identity(t["primary"], v))
        if review_change: review_change(out, t)
        return {"items": [out]}
    strong = FakeClient("strong", adjudicate)
    flow = TwoModelWholePagePipeline(FakeClient("a", {"items": [p]}), FakeClient("b", {"items": [s]}),
        reconciliation_client=FakeClient("format", adjudicate), visual_client=FakeClient("reader", {"items": [visual]}),
        judgment_client=strong, target_unit=unit)
    return flow.recognize(images=(picture(),)), flow


@pytest.mark.parametrize("note,kind", [
    ("仅全角括号与空格表示不同，字迹清楚。", "presentation"),
    ("学生这句话语义不合理，但可以逐字看清。", "answer_semantics"),
])
def test_clear_nonvisual_notes_reach_strong_once_and_stay_archived(note, kind):
    p, s = correct(), correct(answer_text="-4")
    visual = raw_visual(p, uncertainties=[note])
    result, flow = run(p, s, visual, kind)
    row = result.data["items"][0]
    assert row["recognition_status"] == "ready"
    assert len(flow.judgment_client.calls) == 1 and not flow.reconciliation_client.calls
    calls = result.data["multimodel_audit"]["calls"]
    archived = next(c for c in calls if c["stage"] == "visual_readback")["items"][0]
    assert archived["uncertainties"] == [note] and visual["uncertainties"] == [note]
    app = calls[-1]["applications"][0]
    assert app["observation_issue_review"]["entries"][0]["note_quote"] == note
    assert len(result.metadata.attempts) == 4


def test_semantic_error_can_be_judged_wrong_without_being_called_blur():
    p = correct(stem_text="Choose a colour.", answer_text="Tuesday")
    s = {**p, "answer_text": "Tuesday."}
    visual = raw_visual(p, uncertainties=["所填内容不是颜色，但字母清楚。"])
    def mark_wrong(out, _):
        out.update(verdict="incorrect", correct_answer="a colour", correction_method="按题目要求填写颜色词。")
    result, flow = run(p, s, visual, "answer_semantics", mark_wrong)
    row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "incorrect" and row["recognition_status"] == "ready"
    assert "visual_unreadable" not in row["assessment"]["verification"]["reason_codes"]
    assert len(flow.judgment_client.calls) == 1


def test_missing_material_never_becomes_correct_or_claims_blurry_writing():
    p, s = correct(), correct(answer_text="-4")
    result, flow = run(p, s, raw_visual(p, uncertainties=["未提供阅读材料。"]), "missing_context")
    row = result.data["items"][0]
    assert row["recognition_status"] == "needs_revision"
    assert "阅读材料" in row["assessment"]["basis_summary"]
    assert "笔迹" not in row["assessment"]["basis_summary"] and "看不清" not in row["assessment"]["basis_summary"]
    assert row["assessment"]["knowledge_points"] == [] and len(flow.judgment_client.calls) == 1
    assert result.data["multimodel_audit"]["calls"][-1]["applications"][0]["rejection_code"] == "missing_context"


def test_missing_material_note_cannot_disappear_by_selecting_a_different_source():
    p, s = correct(uncertainties=["未提供阅读材料。"]), correct(answer_text="-4")
    result, _ = run(p, s, raw_visual(correct()), "missing_context")
    assert result.data["items"][0]["recognition_status"] == "needs_revision"


@pytest.mark.parametrize("kind", ["visual_legibility", "target_location", "unknown"])
def test_classification_cannot_declare_visual_or_unknown_note_resolved(kind):
    p = correct(); visual = raw_visual(p, uncertainties=["需核实该项。"])
    result, _ = run(p, correct(answer_text="-4"), visual, kind,
                    lambda out, _: [note.update(resolved=True) for note in out["observation_issue_reviews"]])
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
    assert result.data["items"][0]["assessment"]["knowledge_points"] == []


@pytest.mark.parametrize("change", [
    lambda out, _: out.update(observation_issue_reviews=[]),
    lambda out, _: out["observation_issue_reviews"][0].update(source_id="visual:forged"),
    lambda out, _: out["observation_issue_reviews"][0].update(note_index=1),
    lambda out, _: out["observation_issue_reviews"][0].update(note_quote="未出现的说明"),
    lambda out, _: out["observation_issue_reviews"].append(deepcopy(out["observation_issue_reviews"][0])),
])
def test_missing_forged_or_duplicate_note_resolution_fails_closed(change):
    p = correct(); result, _ = run(p, correct(answer_text="-4"),
        raw_visual(p, uncertainties=["仅排版说明。"]), review_change=change)
    assert result.data["items"][0]["recognition_status"] == "needs_revision"


@pytest.mark.parametrize("observation", [
    raw_visual(correct(), answer_status="unclear", answer_text="[不清]", uncertainties=["遮挡"]),
    raw_visual(correct(), answer_text="[不清]", uncertainties=[]),
    raw_visual(correct(), stem_text="计算[不清]", uncertainties=[]),
])
def test_explicit_unreadable_still_makes_zero_adjudication_calls(observation):
    result, flow = run(correct(), correct(answer_text="-4"), observation)
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
    assert not flow.judgment_client.calls and len(result.metadata.attempts) == 3


@pytest.mark.parametrize("answer", ["第1空：；第2空：4a", "第1空（甲/面积）：\n；第2空（甲/周长）：4a"])
def test_empty_ordered_slot_is_missing_observation_and_never_ready(answer):
    p = question(answer_text=answer)
    assert "ordered_answer_content_missing" in units.unit_issues(p, "question")
    flow = TwoModelWholePagePipeline(FakeClient("a", {"items": [p]}), FakeClient("b", {"items": [p]}), target_unit="question")
    result = flow.recognize(images=(picture(),)); row = result.data["items"][0]
    assert row["recognition_status"] == "needs_revision" and row["answer_text"] == answer
    assert "[未作答]" not in row["answer_text"] and row["assessment"]["knowledge_points"] == []
    assert result.data["multimodel_audit"]["calls"][0]["raw_items"][0]["answer_text"] == answer


@pytest.mark.parametrize("value", ["0", "-", "-2", "1/2", "[未作答]", "[不清]", "（甲/面积）：a²"])
def test_nonempty_slot_values_are_not_confused_with_absent_transcription(value):
    p = question(answer_text=f"第1空：{value}\n第2空：4a")
    assert "ordered_answer_content_missing" not in units.unit_issues(p, "question")


def test_historical_source_hash_is_unchanged_without_optional_locator():
    p = correct()
    old_record = {k: deepcopy(p.get(k, [] if k == "options" else None))
                  for k in ("stem_text", "options", "answer_text", "answer_status")}
    old_hash = hashlib.sha256(json.dumps({"label": p["label"], "observation": old_record},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert rec.source_id("primary", p) == "primary:" + old_hash
    updated = {**p, "visible_locator": locator()}
    assert rec.source_id("primary", updated) != rec.source_id("primary", p)
    assert rec.observation_record(updated)["visible_locator"] == locator()
    assert "visible_locator" not in rec.observation_record(p)


def test_unrelated_visual_same_requested_label_and_count_is_rejected():
    p = question(stem_text="计算2+2", answer_text="4", blank_count=1,
                 target_context={"section": None,"row":None,"column":None,"anchor":"左栏题2+2"})
    s = {**p, "stem_text":"计算2+3", "answer_text":"5"}
    v = raw_visual(p, stem_text="计算9×9", answer_text="81",
                   target_context={"section":None,"row":None,"column":None,"anchor":"右栏第三题9×9"})
    result, _ = run(p, s, v, unit="question")
    row=result.data["items"][0]
    assert row["recognition_status"] == "needs_revision" and row["stem_text"] == p["stem_text"]
    assert row["assessment"]["knowledge_points"] == []


def test_changed_printed_conditions_require_bound_original_new_and_visible_locator():
    p = correct(stem_text="计算2^3", answer_text="5", visible_locator=locator())
    s = {**p,"stem_text":"计算2^2"}
    v = raw_visual(p, stem_text="计算2×3")
    result,_=run(p,s,v)
    row=result.data["items"][0]
    assert row["recognition_status"] == "ready" and row["stem_text"] == "计算2×3"
    audit=result.data["multimodel_audit"]["calls"][-1]["applications"][0]["evidence_binding"]["target_identity"]
    assert audit["basis"] == "bound_visible_locator_review"
    assert audit["original_source_id"] == rec.source_id("primary", p)
    assert audit["selected_source_id"] == rec.source_id("visual", v)
    assert audit["original_quote"] != audit["selected_quote"]


@pytest.mark.parametrize("change", [
    lambda out,_: out.update(target_identity=None),
    lambda out,_: out["target_identity"].update(original_source_id=out["source_id"]),
    lambda out,_: out["target_identity"].update(selected_source_id="visual:fake"),
    lambda out,_: out["target_identity"].update(original_quote="计算2×3"),
    lambda out,_: out["target_identity"].update(selected_quote="计算2^3"),
    lambda out,_: out["target_identity"].update(original_locator=locator("9")),
])
def test_missing_or_forged_identity_link_never_changes_visible_question(change):
    p = correct(stem_text="计算2^3",answer_text="5",visible_locator=locator())
    s = {**p,"stem_text":"计算2^2"};v=raw_visual(p,stem_text="计算2×3")
    result,_=run(p,s,v,review_change=change)
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
    assert result.data["items"][0]["stem_text"] == p["stem_text"]


@pytest.mark.parametrize("where", [locator("2"),locator("1","bottom_right"),locator(None,None)])
def test_wrong_or_unknown_actual_visual_locator_is_not_repaired_from_requested_label(where):
    p=correct(stem_text="计算2^3",answer_text="5",visible_locator=locator("1", "top_left"))
    s={**p,"stem_text":"计算2^2"};v=raw_visual(p,stem_text="计算2×3",visible_locator=where)
    result,_=run(p,s,v)
    assert result.data["items"][0]["recognition_status"] == "needs_revision"


def test_known_locator_conflict_blocks_even_identical_text_and_answer():
    p=correct(visible_locator=locator());s={**p,"visible_locator":locator("2")}
    assert not units.same_unit_identity(p,s)
    flow=TwoModelWholePagePipeline(FakeClient("a",{"items":[p]}),FakeClient("b",{"items":[s]}))
    assert all(row["recognition_status"] == "needs_revision" for row in flow.recognize(images=(picture(),)).data["items"])


def test_full_printed_target_can_bind_missing_section_without_anchor_string_equality():
    p=question();s={**p,"answer_text":"第1空：a²；第2空：2a"}
    v=raw_visual(p,target_context={"section":None,"row":None,"column":None,"anchor":"填写面积和周长"})
    result,_=run(p,s,v,unit="question")
    assert result.data["items"][0]["recognition_status"] == "ready"
    audit=result.data["multimodel_audit"]["calls"][-1]["applications"][0]["evidence_binding"]["target_identity"]
    assert audit["basis"] == "full_original_printed_observation"


def test_adjacent_coarse_views_are_not_a_new_location_format_gate():
    p=correct(stem_text="计算2^3",answer_text="5",visible_locator=locator("1", "top_left"))
    s={**p,"stem_text":"计算2^2"};v=raw_visual(p,stem_text="计算2×3",visible_locator=locator("１", "middle"))
    result,_=run(p,s,v)
    assert result.data["items"][0]["recognition_status"] == "ready"


def test_classified_original_format_note_can_support_equivalent_comparison_without_mutation():
    p=correct(uncertainties=["仅换行格式不同。"]);s=correct()
    original=deepcopy(p)
    verdict=review(source=p, comparison={"observations_same": True,"conditions_same":True,
        "difference_type":"presentation","explanation":"原图与两份原文的完整条件和笔迹相同，仅换行。"},
        observation_issue_reviews=classify("primary",p))
    outcome=rec.evaluate_review(verdict,p,s)
    assert outcome["resolved"] and p == original
    assert outcome["source"]["uncertainties"] == p["uncertainties"]


def test_critical_peer_nonvisual_note_is_bound_and_classified_before_release():
    p=correct(stem_text="选出注音正确的一项",subject="语文",answer_text="A",
              options=[{"label":"A","text":"山(shān)"}])
    v=raw_visual(p);peer=raw_visual(p,uncertainties=["括号排版为全角，文字清楚。"])
    def adjudicate(request):
        t=json.loads(request["text"])["task_input"]["items"][0]
        assert t["critical_visual"] == peer
        notes=classify("critical_visual",t["critical_visual"])
        return {"items":[review(source=t["visual"],selected_source="visual",resolution="visual_resolution",
                                observation_issue_reviews=notes)]}
    flow=TwoModelWholePagePipeline(FakeClient("a",{"items":[p]}),
        FakeClient("b",lambda request:{"items":[peer if request["role"] == "recognition" else p]}),
        reconciliation_client=FakeClient("format",{}), visual_client=FakeClient("reader",{"items":[v]}),
        judgment_client=FakeClient("strong",adjudicate))
    result=flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "ready"
    assert len(flow.judgment_client.calls)==1 and len(result.metadata.attempts)==5
    notes=result.data["multimodel_audit"]["calls"][-1]["applications"][0]["observation_issue_review"]
    assert notes["entries"][0]["source_id"]==rec.source_id("critical_visual",peer)


def test_missing_ordered_content_is_local_and_not_claimed_to_be_student_blank():
    bad=question(answer_text="第1空：；第2空：4a")
    good={**question(),"label":"2"}
    flow=TwoModelWholePagePipeline(FakeClient("a",{"items":[bad,good]}),
        FakeClient("b",{"items":[bad,good]}),target_unit="question")
    rows=flow.recognize(images=(picture(),)).data["items"]
    assert rows[0]["recognition_status"]=="needs_revision" and rows[0]["answer_text"]==bad["answer_text"]
    assert rows[1]["recognition_status"]=="ready"


def test_note_echo_cost_reduces_batch_size_without_splitting_a_target():
    from xueban_backend.providers.two_model import review_batches
    targets=[{"label":str(i),"primary":{"uncertainties":["读图说明"*60 for _ in range(4)]}} for i in range(4)]
    batches=review_batches(targets)
    assert len(batches)>1 and [target for batch in batches for target in batch]==targets


def test_old_question_unit_source_hash_does_not_gain_a_null_locator():
    p=units.bind_observation(question(),"question","asset_original")
    fields=("target_unit","blank_count","unit_complete","target_context","answer_slots","source_asset_id")
    old={key:deepcopy(p.get(key,[] if key=="options" else None))
         for key in ("stem_text","options","answer_text","answer_status")}
    old.update({key:deepcopy(p[key]) for key in fields if key in p})
    digest=hashlib.sha256(json.dumps({"label":p["label"],"observation":old},ensure_ascii=False,
        sort_keys=True,separators=(",",":")).encode()).hexdigest()
    assert rec.source_id("primary",p)=="primary:"+digest


def test_punctuation_only_visible_sublabels_do_not_duplicate_page_targets():
    # Anonymous form of the observed 3(1) / 3.(1) regression. The visible
    # locator is distinct from the unchanged requested target label.
    p = correct(label="3(1)", visible_locator=locator("3(1)"))
    s = {**p, "visible_locator": locator("3.(1)")}
    originals = deepcopy((p, s))
    flow = TwoModelWholePagePipeline(FakeClient("a", {"items": [p]}), FakeClient("b", {"items": [s]}))
    rows = flow.recognize(images=(picture(),)).data["items"]
    assert len(rows) == 1 and rows[0]["recognition_status"] == "ready"
    assert not rows[0]["assessment"]["verification"]["reason_codes"]
    assert (p, s) == originals


def test_parent_only_printed_heading_does_not_duplicate_or_confirm_a_changed_target():
    p = correct(label="一(1)", stem_text="8.4÷2=____", answer_text="4.2",
                visible_locator=locator("一(1)", "top_left"))
    s = {**p, "stem_text": "列竖式计算：8.4÷2=____",
         "visible_locator": locator("一、列竖式计算", "top_left")}
    flow = TwoModelWholePagePipeline(FakeClient("a", {"items": [p]}), FakeClient("b", {"items": [s]}))
    rows = flow.recognize(images=(picture(),)).data["items"]
    assert len(rows) == 1
    assert "target_identity_conflict" not in rows[0]["assessment"]["verification"]["reason_codes"]
    # No reviewer exists in this fixture. A compatible boundary is not proof
    # of matching observations, so the remaining stem difference stays pending.
    assert rows[0]["recognition_status"] == "needs_revision"


def test_changed_printed_condition_can_use_complete_punctuation_variant_locator():
    p = correct(label="3(1)", stem_text="计算2^3", answer_text="5", visible_locator=locator("3(1)"))
    s = {**p, "stem_text": "计算2^2", "visible_locator": locator("3.(1)")}
    v = raw_visual(p, stem_text="计算2×3", visible_locator=locator("３.（１）"))
    result, _ = run(p, s, v)
    row = result.data["items"][0]
    assert row["recognition_status"] == "ready" and row["stem_text"] == "计算2×3"
    audit = result.data["multimodel_audit"]["calls"][-1]["applications"][0]["evidence_binding"]["target_identity"]
    assert audit["basis"] == "bound_visible_locator_review"
    assert audit["original_locator"] == p["visible_locator"]
    assert audit["selected_locator"] == v["visible_locator"]


@pytest.mark.parametrize("original,readback", [
    ("一(1)", "一、列竖式计算"),
    ("一、列竖式计算", "一、列竖式计算"),
    ("3(1)", "3"), ("3(1)", None), ("3(1)", "3.(2)"),
])
def test_incomplete_parent_or_different_leaf_does_not_authorize_visual_question_replacement(original, readback):
    p = correct(label="3(1)", stem_text="计算2^3", answer_text="5", visible_locator=locator(original))
    s = {**p, "stem_text": "计算2^2"}
    v = raw_visual(p, stem_text="计算2×3", visible_locator=locator(readback))
    result, _ = run(p, s, v)
    row = result.data["items"][0]
    assert row["recognition_status"] == "needs_revision"
    assert row["stem_text"] == p["stem_text"]
    assert row["assessment"]["knowledge_points"] == []
