"""Offline scheduling regressions; no live providers or credentials."""
from copy import deepcopy
import json
from threading import Event

import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers.ark import Attempt, ProviderError
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def blind(source, **changes):
    return {**{key: deepcopy(source[key]) for key in
              ("label", "stem_text", "options", "answer_text", "answer_status", "uncertainties")}, **changes}


def proposal(target, role="visual", *, presentation=False):
    source = target[role]
    wrong = source["answer_text"] not in ("4", "第1空：a^2；第2空：4a")
    result = review(source=source, label=target["label"], selected_source=role,
        resolution="equivalent" if presentation else "visual_resolution",
        verdict="incorrect" if wrong else "correct", correct_answer="4" if wrong else None,
        correction_method="核验原题中的全部条件和有效步骤。" if wrong else None)
    if presentation:
        result["observation_comparison"]["difference_type"] = "presentation"
    return result


def targets(request):
    return json.loads(request["text"])["task_input"]["items"]


def make_flow(first=None, second=None, observed=None, reviewer=None, judgment=None, unit="answer_slot"):
    first = first or [correct()]
    second = second or [{**first[0], "answer_text": "-4"}]
    observed = observed if observed is not None else {"items": [blind(row) for row in first]}
    return TwoModelWholePagePipeline(
        FakeClient("primary", {"items": first}), FakeClient("secondary", {"items": second}),
        reconciliation_client=FakeClient("reviewer", reviewer or
            (lambda request: {"items": [proposal(t) for t in targets(request)]})),
        visual_client=FakeClient("reader", observed), judgment_client=judgment, target_unit=unit)


def test_original_recheck_does_not_force_blind_read_for_reviewed_instruction_translation():
    p = correct(stem_text="Compute: (-24)/6", answer_text="(-24)/6=4", verdict="incorrect", correct_answer="-4")
    s = correct(stem_text="计算：(-24)/6", answer_text="(-24) / 6 = 4", verdict="incorrect", correct_answer="-4")
    def reconcile(request):
        target = targets(request)[0]
        assert "visual" not in target
        return {"items": [review(source=target["primary"], verdict="incorrect", correct_answer="-4",
                                 correction_method="检查除法的符号。") ]}
    strong = FakeClient("strong", ProviderError("must_not_call"))
    flow = make_flow([p], [s], reviewer=reconcile, judgment=strong)
    # This is the flag attached only to the isolated original-image pipeline.
    flow._original_recheck_target = {"label": "1", "target_unit": "answer_slot"}
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["recognition_status"] == "ready" and row["assessment"]["verdict"] == "incorrect"
    assert row["answer_text"] == p["answer_text"] and row["stem_text"] == p["stem_text"]
    assert not flow.visual_client.calls and not strong.calls
    assert len(flow.reconciliation_client.calls) == 1 and len(result.metadata.attempts) == 3
    assert [call["stage"] for call in result.data["multimodel_audit"]["calls"]] == ["primary", "secondary", "reconciliation"]


def test_review_that_actually_saw_blind_instruction_variant_can_keep_bound_old_source():
    from xueban_backend.providers.reconciliation import evaluate_review
    p = correct(stem_text="Compute: (-24)/6")
    s = correct(stem_text="计算：(-24)/6")
    observed = blind(p)
    proposal = review(source=s, selected_source="secondary", resolution="visual_resolution")
    flow = make_flow()
    call = {"code": "ok", "model": "reader", "items": {"1": observed}}
    with_read = evaluate_review(proposal, p, s, visual=observed)
    assert flow._visual_gate(call, "1", with_read)["resolved"] is True
    without_read = evaluate_review(proposal, p, s)
    assert flow._visual_gate(call, "1", without_read)["rejection_code"] == "independent_condition_disagreement"
    changed = blind(p, stem_text="Compute: (-24)/8")
    changed_call = {**call, "items": {"1": changed}}
    unsupported = evaluate_review(proposal, p, s, visual=changed)
    assert flow._visual_gate(changed_call, "1", unsupported)["rejection_code"] == "independent_condition_disagreement"


@pytest.mark.parametrize("role", ["primary", "secondary", "visual"])
def test_material_target_reads_before_exactly_one_adjudication_and_keeps_selected_source(role):
    first, second = correct(), correct(answer_text="-4")
    observed = blind(second if role == "secondary" else first,
                     answer_text="5" if role == "visual" else (second if role == "secondary" else first)["answer_text"])
    seen = []
    def read(request):
        assert json.loads(request["text"])["task_input"] == {"labels": ["1"]}
        seen.append("read")
        return {"items": [observed]}
    def adjudicate(request):
        assert seen == ["read"]
        target = targets(request)[0]
        assert set(("primary", "secondary", "visual")) <= set(target)
        seen.append("review")
        return {"items": [proposal(target, role)]}
    flow = make_flow([first], [second], read, adjudicate)
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert seen == ["read", "review"]
    assert row["recognition_status"] == "ready"
    assert row["answer_text"] == observed["answer_text"]
    calls = result.data["multimodel_audit"]["calls"]
    assert [c["stage"] for c in calls] == ["primary", "secondary", "visual_readback", "visual_adjudication"]
    assert calls[-1]["applications"][0]["resolved_source"] == role
    assert len(result.metadata.attempts) == 4
    assert len(flow.reconciliation_client.calls) == len(flow.visual_client.calls) == 1


def test_old_source_omitting_readable_process_is_not_accepted_or_retried():
    flow = make_flow(observed={"items": [blind(correct(), answer_text="(-2)×(-2)=4")]},
                     reviewer=lambda request: {"items": [proposal(targets(request)[0], "primary")]})
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["knowledge_points"] == []
    assert len(flow.reconciliation_client.calls) == len(flow.visual_client.calls) == 1
    assert result.data["multimodel_audit"]["calls"][-1]["applications"][0]["rejection_code"] == "independent_answer_disagreement"


@pytest.mark.parametrize("same_stem", [True, False])
def test_unclear_readback_skips_review_and_never_shows_a_judge_completed_number(same_stem):
    p = correct(answer_text="36+36+9=81")
    s = correct(answer_text="81")
    obs = blind(p, answer_text="36+[不清]+9\n=81，旁另写36", answer_status="unclear",
                uncertainties=["中间有效步骤被覆盖"], stem_text=p["stem_text"] if same_stem else "另一道共享条件题")
    flow = make_flow([p], [s], {"items": [obs]})
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert not flow.reconciliation_client.calls
    assert row["answer_text"] == (obs["answer_text"] if same_stem else None)
    assert row["answer_status"] == "unclear"
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["knowledge_points"] == []
    calls = result.data["multimodel_audit"]["calls"]
    assert len(calls) == len(result.metadata.attempts) == 3
    assert calls[-1]["applications"][0]["application_kind"] == "readback_gate"
    assert calls[0]["items"][0]["answer_text"] == "36+36+9=81"


def test_format_batch_applies_while_unrelated_visual_read_is_still_blocked():
    read_started, format_applied = Event(), Event()
    p = [correct("1"), correct("2")]
    s = [correct("1", answer_text="-4"), correct("2", stem_text="请计算(-2)²。")]
    def read(request):
        read_started.set()
        assert format_applied.wait(2), "format application waited for unrelated blind result"
        return {"items": [blind(p[0])]}
    def adjudicate(request):
        ts = targets(request)
        return {"items": [proposal(t, "visual" if "visual" in t else "primary",
                                   presentation="visual" not in t) for t in ts]}
    flow = make_flow(p, s, read, adjudicate)
    apply = flow._apply_review
    def tracked(row, *args, **kwargs):
        result = apply(row, *args, **kwargs)
        if row["label"] == "2":
            assert read_started.is_set() and result["resolved"]
            format_applied.set()
        return result
    flow._apply_review = tracked
    result = flow.recognize(images=(picture(),))
    assert all(row["recognition_status"] == "ready" for row in result.data["items"])
    assert len(flow.reconciliation_client.calls) == 2
    assert len(flow.visual_client.calls) == 1


def test_one_failed_visual_request_preserves_format_result_and_paid_attempt():
    p = [correct("1"), correct("2")]
    s = [correct("1", answer_text="-4"), correct("2", stem_text="请计算(-2)²。")]
    error = ProviderError("deadline_exceeded", attempts=(Attempt(1, 200, "deadline_exceeded", 20),))
    flow = make_flow(p, s, error, lambda request: {"items": [proposal(t, "primary", presentation=True) for t in targets(request)]})
    result = flow.recognize(images=(picture(),))
    rows = {r["label"]: r for r in result.data["items"]}
    assert rows["1"]["recognition_status"] == "needs_revision"
    assert "reconciliation_unavailable" in rows["1"]["assessment"]["verification"]["reason_codes"]
    assert "visual_unreadable" not in rows["1"]["assessment"]["verification"]["reason_codes"]
    assert rows["2"]["recognition_status"] == "ready"
    assert len(flow.reconciliation_client.calls) == 1
    assert sorted(a.code for a in result.metadata.attempts) == ["deadline_exceeded", "ok", "ok", "ok"]


def test_one_unclear_target_does_not_poison_the_other_target_in_same_read_request():
    p = [correct("1"), correct("2")]
    s = [correct("1", answer_text="-4"), correct("2", answer_text="-4")]
    flow = make_flow(p, s, {"items": [blind(p[0], answer_text="[不清]", answer_status="unclear",
        uncertainties=["遮挡"]), blind(p[1])]})
    result = flow.recognize(images=(picture(),))
    rows = {r["label"]: r for r in result.data["items"]}
    assert rows["1"]["recognition_status"] == "needs_revision"
    assert rows["2"]["recognition_status"] == "ready"
    assert [t["label"] for t in targets(flow.reconciliation_client.calls[0])] == ["2"]
    assert len(result.metadata.attempts) == 4


def test_review_failure_is_local_to_its_batch_and_is_not_retried():
    p = [correct(str(i)) for i in range(1, 8)]
    s = [{**row, "answer_text": "-4"} for row in p]
    def adjudicate(request):
        ts = targets(request)
        if any(t["label"] == "1" for t in ts):
            raise ProviderError("incomplete_output", attempts=(Attempt(1, 200, "incomplete_output", 20),))
        return {"items": [proposal(t) for t in ts]}
    flow = make_flow(p, s, reviewer=adjudicate)
    result = flow.recognize(images=(picture(),))
    rows = {r["label"]: r for r in result.data["items"]}
    assert all(rows[str(i)]["recognition_status"] == "needs_revision" for i in range(1, 7))
    assert rows["7"]["recognition_status"] == "ready"
    assert len(flow.reconciliation_client.calls) == 2
    assert len(flow.visual_client.calls) == 1
    assert len(result.metadata.attempts) == 5


def test_real_judgment_disagreement_still_routes_to_judgment_model_once():
    p = correct()
    s = correct(answer_text="-4", verdict="incorrect", correct_answer="4")
    strong = FakeClient("strong", lambda request: {"items": [proposal(t) for t in targets(request)]})
    flow = make_flow([p], [s], judgment=strong)
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "ready"
    assert len(strong.calls) == 1 and not flow.reconciliation_client.calls
    assert "visual" in targets(strong.calls[0])[0]


def test_critical_disagreement_stops_before_adjudication_without_majority_vote():
    p = correct(stem_text="下列注音正确的是", subject="语文", answer_text="A",
                options=[{"label": "A", "text": "翘(qiáo)"}])
    obs = blind(p)
    other = blind(p, options=[{"label": "A", "text": "翘(qiào)"}])
    flow = make_flow([p], [p], {"items": [obs]})
    flow.review_client.output = lambda request: {"items": [other if request["role"] == "recognition" else p]}
    result = flow.recognize(images=(picture(),))
    assert not flow.reconciliation_client.calls
    assert len(flow.review_client.calls) == 2
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
    assert len(result.metadata.attempts) == 4
    read = next(c for c in result.data["multimodel_audit"]["calls"] if c["stage"] == "visual_readback")
    assert read["applications"][0]["rejection_code"] == "critical_print_disagreement"


def test_no_source_forgery_can_trigger_a_second_visual_adjudication():
    def adjudicate(request):
        r = proposal(targets(request)[0]); r["source_id"] = "visual:wrong-source"
        return {"items": [r]}
    flow = make_flow(reviewer=adjudicate)
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
    assert len(flow.reconciliation_client.calls) == len(flow.visual_client.calls) == 1


def test_late_visual_discovery_is_bounded_and_may_reuse_original_bound_review():
    p = correct(); s = correct(stem_text="请计算(-2)²。")
    def adjudicate(request):
        t = targets(request)[0]
        return {"items": [proposal(t, "primary")]}
    # A presentation-shaped disagreement gets a visual_resolution from review,
    # which requires one late read. The original decision can then be bound.
    flow = make_flow([p], [s], reviewer=adjudicate)
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "ready"
    assert len(flow.reconciliation_client.calls) == len(flow.visual_client.calls) == 1
    assert len(result.metadata.attempts) == 4


def test_question_unit_keeps_complete_multiblank_identity_and_order():
    from test_question_units import observation
    p = {**correct("3(1)"), **observation()}
    s = {**deepcopy(p), "answer_text": "第1空：a^2；第2空：2a"}
    obs = {key:value for key,value in p.items() if key in
           {"label", "stem_text", "options", "answer_text", "answer_status", "uncertainties",
            "blank_count", "unit_complete", "target_context"}}
    flow = make_flow([p], [s], {"items": [obs]}, unit="question")
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["recognition_status"] == "ready"
    assert row["blank_count"] == 2 and row["answer_text"] == p["answer_text"]
    assert len(flow.reconciliation_client.calls) == len(flow.visual_client.calls) == 1


@pytest.mark.parametrize("difference", ["answer", "options"])
def test_any_visual_adjudication_uses_strong_model_without_an_original_grading_dispute(difference):
    p = correct()
    s = correct(answer_text="-4") if difference == "answer" else correct(
        options=[{"label": "A", "text": "(-2)^2"}])
    strong = FakeClient("strong", lambda request: {"items": [proposal(t) for t in targets(request)]})
    flow = make_flow([p], [s], judgment=strong)
    if difference == "options":
        # Both observations have options so the independent read is complete.
        p["options"] = [{"label": "A", "text": "(-3)^2"}]
        flow.visual_client.output = {"items": [blind(p)]}
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "ready"
    assert len(strong.calls) == 1 and not flow.reconciliation_client.calls
    t = targets(strong.calls[0])[0]
    assert not set(t["reason_codes"]) & {"judgment_disagreement", "correction_disagreement"}
    assert "visual" in t


def test_unclear_read_never_pays_strong_or_format_adjudicator():
    p = correct()
    strong = FakeClient("strong", ProviderError("unexpected_call"))
    flow = make_flow(observed={"items": [blind(p, answer_text="[不清]", answer_status="unclear",
        uncertainties=["遮挡"])]}, judgment=strong)
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
    assert not strong.calls and not flow.reconciliation_client.calls
    assert len(result.metadata.attempts) == 3


def test_format_only_stays_on_reconciliation_model_without_a_visual_request():
    p = correct()
    strong = FakeClient("strong", ProviderError("unexpected_call"))
    flow = make_flow([p], [correct(stem_text="请计算(-2)²。")], judgment=strong,
        reviewer=lambda request: {"items": [proposal(t, "primary", presentation=True) for t in targets(request)]})
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "ready"
    assert not strong.calls and not flow.visual_client.calls
    assert len(flow.reconciliation_client.calls) == 1


def test_dispatch_budget_skip_is_not_counted_as_a_paid_review(monkeypatch):
    monkeypatch.setattr("xueban_backend.providers.two_model.REVIEW_DISPATCH_SECONDS", 0)
    strong = FakeClient("strong", ProviderError("unexpected_call"))
    flow = make_flow(judgment=strong)
    result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["recognition_status"] == "needs_revision"
    assert not strong.calls and not flow.reconciliation_client.calls
    assert len(result.metadata.attempts) == 3
    audit = result.data["multimodel_audit"]["calls"]
    assert audit[-1]["code"] == "review_dispatch_budget_exhausted"
    assert audit[-1]["stage"] == "visual_adjudication" and audit[-1]["model"] == "strong"


@pytest.mark.parametrize("changed_stem", ["计算(-3)²", "不得计算(-2)²"])
def test_visual_cannot_validate_old_source_by_matching_final_answer_only(changed_stem):
    flow = make_flow(observed={"items": [blind(correct(), stem_text=changed_stem)]},
        reviewer=lambda request: {"items": [proposal(t, "primary") for t in targets(request)]})
    result = flow.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["recognition_status"] == "needs_revision"
    assert row["assessment"]["knowledge_points"] == []
    assert len(flow.reconciliation_client.calls) == len(flow.visual_client.calls) == 1
    assert result.data["multimodel_audit"]["calls"][-1]["applications"][0]["rejection_code"] == "independent_condition_disagreement"
