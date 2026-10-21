"""Bound arithmetic in retained equality chains, not an image-accuracy benchmark."""
from copy import deepcopy
import json

import pytest

from test_correction_flow import flow  # noqa: F401
from test_reconciliation import identity_evidence, review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers import reconciliation
from xueban_backend.providers.equality_chain import (
    check_equality_chain, check_matched_equality_chain, check_reviewed_equality_chain)
from xueban_backend.providers.two_model import TwoModelWholePagePipeline

STEM = "计算：(-15/7)÷(-1.2)÷(-5/7)。"
ANSWER = "=(15×5×7)/(7×6×5)\n=-5/2"


def observed(stem=STEM, answer=ANSWER, **changes):
    return {"label": "14(2)", "stem_text": stem, "answer_text": answer,
            "answer_status": "present", "uncertainties": [], "options": [], **changes}


def checked(source):
    return check_equality_chain(source, source_id=reconciliation.source_id("secondary", source))


def test_correct_final_does_not_hide_positive_intermediate_and_raw_spans_are_immutable():
    source = observed(); original = deepcopy(source); result = checked(source)
    assert result["verdict"] == "incorrect" and result["correct_answer"] == "-5/2"
    assert "最终结果-5/2正确" in result["basis_summary"]
    assert "answer_checks" not in result  # Calculation steps are not printed answer slots.
    assert result["audit"]["final_value_matches"] is True
    assert [s["value"] for s in result["audit"]["steps"]] == ["5/2", "-5/2"]
    assert [s["equals_previous_value"] for s in result["audit"]["steps"]] == [False, False]
    assert source == original and result["audit"]["observation_rewritten"] is False
    references = [result["audit"]["printed_expression"], *[ref for step in result["audit"]["steps"]
                  for ref in (step["expression"], step["equals"]) if ref is not None]]
    for ref in references:
        assert source[ref["field"]][ref["start"]:ref["end"]] == ref["text"]


@pytest.mark.parametrize("stem,answer,expected", [
    (STEM, "(-15/7)÷(-1.2)÷(-5/7)\n=15/7×5/6×7/5\n=-5/2", "-5/2"),
    (STEM, "＝(15×5×7)/(7×6×5)\n＝-5/2", "-5/2"),
    ("计算：0.1+0.2", "0.1+0.2=.4=.3", "3/10"),
    ("求值：２＋２。", "２＋２\r\n＝５\r\n＝４", "4"),
    ("计算：(-4)÷(-3/7)÷(4/3)", "=8=7", "7"),
])
def test_bounded_constants_explicit_equals_and_supported_typography(stem, answer, expected):
    result = checked(observed(stem, answer))
    assert result["verdict"] == "incorrect" and result["correct_answer"] == expected


@pytest.mark.parametrize("stem,answer", [
    (STEM, "=-(15×5×7)/(7×6×5)\n=-5/2"),
    ("0.1+0.2", "0.1+0.2=.3=3/10"),
    ("1+1", "1+1=2\n3+3=7"),
    ("1+1", "1+1\n3\n2"),
    ("1+1", "1+1\n=3\n\n=2"),
    ("1+1", "1+1=3；旁注=2"),
    ("1+1", "1+1=3（划掉）=2"),
    ("1+1", "原1+1=3，改为2"),
    ("1+1", "1+1=~~3~~=2"),
    ("1+1", "2×1=3=2"),  # Same value does not bind a different first expression.
    ("1+1", "1+1=3=2cm"),
    ("1+1", "1+1=3≈2"),
    ("x+1", "x+1=3=2"),
    ("1+1", "1+1=x=2"),
    ("1+1", "1+1=3=2%"),
    ("2²", "2²=22=4"),
    ("计算：1+1，结果取负数", "=3=2"),
    ("取倒数：1+1", "=3=2"),
    ("1+1=____=____", "第1空：3；第2空：2"),
    ("12", "=1 2=12"),
    ("5/2", "=2 1/2=2.5"),
    ("16÷(1/2-3/8)", "=16÷4/8-3/8\n=16÷1/8\n=128"),
    ("1/0", "=3=2"),
    ("2(3)", "=7=6"),
    ("1+1", "=2"),
    ("1+1", "=2==3"),
    ("1+1", "=3="),
    ("1+1", "=3\n=9999999999999"),
    ("1+1", "=3" + "=2" * 8),
    ("1+1", "=3\n=" + "(" * 70 + "2" + ")" * 70),
])
def test_unsupported_and_wholly_correct_noop_without_rewriting(stem, answer):
    source = observed(stem, answer); original = deepcopy(source)
    assert checked(source) is None and source == original


@pytest.mark.parametrize("changes", [
    {"uncertainties": ["中间项被划掉"]}, {"answer_status": "unclear"},
    {"subject": "物理"}, {"question_type": "fill_blank"}, {"target_unit": "question"},
    {"label": "14(2)#1"}, {"options": [{"label": "A", "text": "-5/2"}]},
])
def test_not_a_reading_or_general_question_solver(changes):
    assert checked(observed(**changes)) is None


def synthetic_flow(*, answer=ANSWER, verdict="correct", selected="secondary", label="14(2)",
                   bad_source=False, bad_quote=False, missing_identity=False, visual_unclear=False):
    locator = {"printed_label": label, "page_zone": "middle_left"}
    first = correct(label, stem_text=STEM, answer_text="(-15/7)÷(-1.2)÷(-5/7)\n=15/7×5/6×7/5\n=-5/2",
                    verdict="incorrect", correct_answer="-5/2", correction_method="保留过程符号。", visible_locator=locator)
    second = correct(label, stem_text=STEM, answer_text=answer, visible_locator=locator)
    visual = observed(answer=answer, label=label, visible_locator=locator,
                      **({"uncertainties": ["过程看不清"], "answer_status": "unclear"} if visual_unclear else {}))
    if missing_identity:
        # A genuinely changed printed observation needs the existing explicit
        # identity protocol; this is synthetic, not added to any old record.
        visual["stem_text"] = "计算：(-15/7)÷(-1.2)÷(-5/7)+0。"
    def adjudicate(request):
        target = json.loads(request["text"])["task_input"]["items"][0]
        proposal = review(label=label, source=target[selected], selected_source=selected,
            resolution="visual_resolution", verdict=verdict,
            correct_answer="旧答案" if verdict == "incorrect" else None,
            correction_method="旧方法" if verdict == "incorrect" else None)
        if selected == "visual" and not missing_identity:
            proposal["target_identity"] = identity_evidence(target["primary"], target["visual"])
        if bad_source: proposal["source_id"] = "secondary:" + "0" * 64
        if bad_quote: proposal["judgment_basis"]["condition_quotes"][0]["text"] = "99+88"
        return {"items": [proposal]}
    return TwoModelWholePagePipeline(FakeClient("first", {"items": [first]}),
        FakeClient("second", {"items": [second]}), visual_client=FakeClient("visual", {"items": [visual]}),
        reconciliation_client=FakeClient("review", adjudicate))


def application(result):
    return [a for call in result.data["multimodel_audit"]["calls"] for a in call.get("applications", [])][-1]


def test_bound_review_is_repaired_with_raw_source_and_review_retained_and_no_more_calls():
    pipeline = synthetic_flow(); result = pipeline.recognize(images=(picture(),)); row = result.data["items"][0]
    assert row["recognition_status"] == "ready" and row["assessment"]["verdict"] == "incorrect"
    assert row["answer_text"] == ANSWER and row["assessment"]["correct_answer"] == "-5/2"
    assert "answer_checks" not in row["assessment"]
    assert len(result.metadata.attempts) == 4
    assert [len(c.calls) for c in (pipeline.client, pipeline.review_client, pipeline.visual_client, pipeline.reconciliation_client)] == [1]*4
    proposal = result.data["multimodel_audit"]["calls"][-1]["items"][0]
    assert proposal["verdict"] == "correct" and proposal["answer_text"] == ANSWER
    audit = application(result)["equality_chain_arithmetic"]
    assert audit["previous_review_verdict"] == "correct" and audit["applied_verdict"] == "incorrect"
    assert audit["source_id"] == proposal["source_id"]


@pytest.mark.parametrize("changes,reason", [
    ({"bad_source": True}, "source_id_mismatch"),
    ({"bad_quote": True}, "condition_quote_not_bound"),
    ({"selected": "visual", "missing_identity": True}, "target_identity_unconfirmed"),
    ({"visual_unclear": True}, "visual_reading_unclear"),
])
def test_existing_gates_reject_before_arithmetic(changes, reason):
    result = synthetic_flow(**changes).recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "unable_to_judge"
    assert application(result)["rejection_code"] == reason
    assert "equality_chain_arithmetic" not in application(result)


def test_correct_chain_never_upgrades_old_incorrect_judgment():
    result = synthetic_flow(answer="=-(15×5×7)/(7×6×5)\n=-5/2", verdict="incorrect").recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "incorrect"
    assert result.data["items"][0]["assessment"]["correct_answer"] == "旧答案"
    assert "equality_chain_arithmetic" not in application(result)


def test_initial_matched_pair_is_checked_independently_without_more_calls():
    first = correct("14(2)", stem_text=STEM, answer_text=ANSWER)
    pipeline = TwoModelWholePagePipeline(FakeClient("first", {"items": [first]}), FakeClient("second", {"items": [first]}))
    result = pipeline.recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "incorrect"
    assert result.data["items"][0]["assessment"]["verification"]["status"] == "matched"
    assert len(result.metadata.attempts) == 2
    assert all(not call.get("applications") for call in result.data["multimodel_audit"]["calls"])
    audit = result.data["multimodel_audit"]["matched_equality_chain_arithmetic"][0]
    assert [s["source_id"] for s in audit["sources"]] == [reconciliation.source_id(role, first) for role in ("primary", "secondary")]
    assert all(s["calculation"]["authority"] == "bounded_arithmetic_after_matched_dual_source_gates" for s in audit["sources"])
    assert all(call["items"][0]["verdict"] == "correct" for call in result.data["multimodel_audit"]["calls"])
    audit["sources"][0]["observation"]["answer_text"] = "edited"
    assert first["answer_text"] == ANSWER and result.data["items"][0]["answer_text"] == ANSWER


@pytest.mark.parametrize("changes", [
    {"answer_text": "15/7×5/6×7/5\n=-5/2"},  # Initial scratch expression not printed target.
    {"answer_text": "=(15×5×7)/(7×6×5)\n-5/2"},  # Missing equality is not inferred.
    {"answer_text": "=6/2\n=-5/2"},  # Both wrong, but different original steps.
    {"answer_text": "=-(15×5×7)/(7×6×5)\n=-5/2"},
    {"stem_text": "求值：(-15/7)÷(-1.2)÷(-5/7)。"},
    {"label": "14(3)"}, {"verdict": "incorrect"}, {"answer_status": "unclear"},
    {"uncertainties": ["笔迹有划改"]}, {"target_unit": "question"},
])
def test_matched_requires_both_complete_identical_original_chains(changes):
    first = correct("14(2)", stem_text=STEM, answer_text=ANSWER)
    second = {**deepcopy(first), **changes}; before = deepcopy((first, second))
    assert check_matched_equality_chain(first, second) is None
    assert (first, second) == before


def test_incomplete_page_does_not_gain_matched_chain_authority():
    from test_partial_output_persistence import make_client
    first = correct("14(2)", stem_text=STEM, answer_text=ANSWER)
    malformed = {**first, "label": "bad", "answer_text": {"bad": "shape"}}
    a, acalls = make_client([first, malformed], "first")
    b, bcalls = make_client([first, malformed], "second")
    pipeline = TwoModelWholePagePipeline(a, b, partial_output_enabled=True)
    try:
        result = pipeline.recognize(images=(picture(),))
        assert result.data["items"][0]["assessment"]["verdict"] == "correct"
        assert "matched_equality_chain_arithmetic" not in result.data["multimodel_audit"]
        assert len(acalls) == len(bcalls) == 1
    finally:
        pipeline.close()


def test_supported_pair_can_differ_only_in_outer_layout_not_in_original_expressions():
    first = correct("14(2)", stem_text=STEM, answer_text=ANSWER)
    second = {**first, "answer_text": "  =(15×5×7)/(7×6×5)  \n   =-5/2  "}
    before = deepcopy((first, second))
    result = check_matched_equality_chain(first, second)
    assert result["verdict"] == "incorrect" and (first, second) == before
    for source in result["audit"]["sources"]:
        for step in source["calculation"]["steps"]:
            for ref in (step["expression"], step["equals"]):
                if ref:
                    assert source["observation"][ref["field"]][ref["start"]:ref["end"]] == ref["text"]


def test_matched_wrong_initial_and_r1_synthetic_paths_reuse_two_reads_only(flow):
    from test_recognition_recheck import recheck, recovery, attempt_for
    first = correct("14(2)", stem_text=STEM, answer_text=ANSWER, knowledge_points=[])
    def matched():
        return TwoModelWholePagePipeline(FakeClient("first", {"items": [first]}), FakeClient("second", {"items": [first]}))
    flow.worker.pipeline = synthetic_flow(bad_source=True)
    case = flow.ready(); before = attempt_for(flow, case)
    pipeline = matched(); flow.worker.pipeline = pipeline
    response = recheck(flow, case); assert response.status_code == 202
    assert flow.worker.run_once()
    current = flow.client.get("/v1/cases/" + case["id"]).json()
    after = attempt_for(flow, current)
    assert recovery(flow, current)["last_outcome"] == "applied"
    assert after["answer_text"] == ANSWER and after["recognition_revision"] == before["recognition_revision"] + 1
    assert current["current_decision"]["verdict"] == "incorrect"
    assert current["current_decision"]["validity"] == "candidate"
    assert len(pipeline.client.calls) == len(pipeline.review_client.calls) == 1
    assert current["attempt_ids"] == case["attempt_ids"]


def text_review_flow(*, selected="primary", second_answer=ANSWER, bad_quote=False):
    first = correct("14(2)", stem_text=STEM, answer_text=ANSWER, verdict="incorrect",
                    correct_answer="-5/2", correction_method="保留过程中的负号。")
    second = correct("14(2)", stem_text=STEM, answer_text=second_answer)
    proposal = review(label="14(2)", source={"primary": first, "secondary": second}[selected], selected_source=selected)
    if bad_quote:
        proposal["judgment_basis"]["condition_quotes"][0]["text"] = "99+88"
    return TwoModelWholePagePipeline(FakeClient("first", {"items": [first]}),
        FakeClient("second", {"items": [second]}),
        reconciliation_client=FakeClient("review", {"items": [proposal]}))


@pytest.mark.parametrize("selected", ["primary", "secondary"])
def test_resolved_text_review_cannot_hide_same_error_in_two_complete_original_chains(selected):
    pipeline = text_review_flow(selected=selected); result = pipeline.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["recognition_status"] == "ready" and row["assessment"]["verdict"] == "incorrect"
    assert row["answer_text"] == ANSWER and len(result.metadata.attempts) == 3
    audit = application(result)["equality_chain_arithmetic"]
    assert audit["selected_source"] == selected and audit["previous_review_verdict"] == "correct"
    assert [s["original_verdict"] for s in audit["sources"]] == ["incorrect", "correct"]
    assert all(s["calculation"]["authority"] == "bounded_arithmetic_after_reviewed_dual_source_gates" for s in audit["sources"])
    assert result.data["multimodel_audit"]["calls"][-1]["items"][0]["verdict"] == "correct"


def test_nonvisual_review_still_needs_bound_conditions_and_both_calculable_sources():
    result = text_review_flow(bad_quote=True).recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "unable_to_judge"
    assert application(result)["rejection_code"] == "condition_quote_not_bound"
    assert "equality_chain_arithmetic" not in application(result)
    # An overlong second expression must not borrow the first read's arithmetic
    # authority or waive the existing requirement for visual support.
    answer = "=(15" + " " * 130 + "×5×7)/(7×6×5)\n=-5/2"
    result = text_review_flow(second_answer=answer).recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "unable_to_judge"
    assert application(result)["rejection_code"] == "independent_readback_required"
    assert "equality_chain_arithmetic" not in application(result)
    assert len(result.metadata.attempts) == 3


def test_reviewed_dual_source_proof_cannot_use_a_third_or_mismatched_selected_source():
    first = correct("14(2)", stem_text=STEM, answer_text=ANSWER, verdict="incorrect")
    second = {**first, "verdict": "correct"}
    for role, identifier in [("visual", reconciliation.source_id("visual", first)),
                             ("primary", reconciliation.source_id("secondary", first)),
                             ("primary", "primary:" + "0" * 64)]:
        assert check_reviewed_equality_chain(first, second, selected_source=role, selected_source_id=identifier) is None


def test_actual_partial_page_with_judgment_conflict_has_no_text_review_or_chain_application():
    from test_partial_output_persistence import make_client
    first = correct("14(2)", stem_text=STEM, answer_text=ANSWER, verdict="incorrect")
    second = {**first, "verdict": "correct"}
    malformed = {**first, "label": "bad", "answer_text": {"bad": "shape"}}
    a, acalls = make_client([first, malformed], "first")
    b, bcalls = make_client([second], "second")
    reviewer = FakeClient("review", RuntimeError("partial pages cannot dispatch review"))
    pipeline = TwoModelWholePagePipeline(a, b, reconciliation_client=reviewer, partial_output_enabled=True)
    try:
        result = pipeline.recognize(images=(picture(),))
        assert result.data["items"][0]["assessment"]["verdict"] == "unable_to_judge"
        assert not reviewer.calls and len(acalls) == len(bcalls) == 1
        assert "matched_equality_chain_arithmetic" not in result.data["multimodel_audit"]
        assert all(not call.get("applications") for call in result.data["multimodel_audit"]["calls"])
    finally:
        pipeline.close()


def test_original_recheck_can_apply_same_dual_source_text_review_without_new_calls(flow):
    from test_recognition_recheck import recheck, recovery, attempt_for
    flow.worker.pipeline = synthetic_flow(bad_source=True)
    case = flow.ready(); before = attempt_for(flow, case)
    pipeline = text_review_flow(); flow.worker.pipeline = pipeline
    assert recheck(flow, case).status_code == 202 and flow.worker.run_once()
    current = flow.client.get("/v1/cases/" + case["id"]).json()
    after = attempt_for(flow, current)
    assert recovery(flow, current)["last_outcome"] == "applied"
    assert after["answer_text"] == ANSWER and after["recognition_revision"] == before["recognition_revision"] + 1
    assert current["current_decision"]["verdict"] == "incorrect"
    assert len(pipeline.client.calls) == len(pipeline.review_client.calls) == len(pipeline.reconciliation_client.calls) == 1


@pytest.mark.parametrize("route,expected_calls", [("non_material", 3), ("late_visual_equivalent", 5)])
def test_same_dual_source_proof_is_not_lost_on_other_existing_resolved_routes(route, expected_calls):
    pipeline = text_review_flow(); first = deepcopy(pipeline.client.output["items"][0])
    if route == "non_material":
        pipeline.reconciliation_client.output["items"][0]["resolution"] = "non_material"
    else:
        pipeline.visual_client = FakeClient("visual", {"items": [observed()]})
        seen = []
        def response(request):
            seen.append(1)
            proposal = review(label="14(2)", source=first)
            if len(seen) == 1:
                proposal.update(resolution="unresolved", unresolved_cause="model_disagreement")
            return {"items": [proposal]}
        pipeline.reconciliation_client = FakeClient("review", response)
    result = pipeline.recognize(images=(picture(),))
    row = result.data["items"][0]
    assert row["recognition_status"] == "ready" and row["assessment"]["verdict"] == "incorrect"
    assert row["answer_text"] == ANSWER and len(result.metadata.attempts) == expected_calls
    audit = application(result)["equality_chain_arithmetic"]
    assert audit["protocol"] == "reviewed-dual-source-constant-equality-chain.v1"
    assert all(s["calculation"]["authority"] == "bounded_arithmetic_after_reviewed_dual_source_gates" for s in audit["sources"])


def test_real_worker_persists_incorrect_process_with_unchanged_answer_and_no_fake_slots(flow):
    from sqlalchemy.orm import Session
    from xueban_backend.correction_models import Entity, Task
    flow.worker.pipeline = synthetic_flow()
    _, job = flow.recognize(); assert flow.worker.run_once()
    case = flow.client.get("/v1/cases/" + flow.homework()["case_ids"][0]).json()
    decision = flow.client.get("/v1/decisions/" + case["current_decision"]["id"]).json()["record"]
    attempt = flow.client.get("/v1/attempts/" + case["current_attempt_id"]).json()["record"]
    assert decision["verdict"] == "incorrect" and decision["validity"] == "candidate"
    assert case["progress"] == "needs_correction" and attempt["answer_text"] == ANSWER
    assert decision.get("answer_checks", []) == [] and decision["correct_answer"] == "-5/2"
    with Session(flow.database.engine) as session:
        assert session.get(Entity, decision["id"]).document == decision
        task = session.get(Task, job["id"])
        assert len(task.audit["executions"][-1]["metadata"]["attempts"]) == 4


@pytest.mark.parametrize("changes,expected", [
    ({}, "applied"), ({"bad_source": True}, "unchanged_pending"),
    ({"label": "14(3)"}, "target_unconfirmed"),
])
def test_real_original_recheck_still_uses_existing_source_and_saved_target_gates(flow, changes, expected):
    from test_recognition_recheck import attempt_for, recheck, recovery
    flow.worker.pipeline = synthetic_flow(bad_source=True)
    case = flow.ready(); before = attempt_for(flow, case)
    assert case["current_decision"]["verdict"] == "unable_to_judge"
    pipeline = synthetic_flow(**changes); flow.worker.pipeline = pipeline
    response = recheck(flow, case); assert response.status_code == 202, response.text
    assert flow.worker.run_once()
    current = flow.client.get("/v1/cases/" + case["id"]).json()
    after = attempt_for(flow, current)
    assert recovery(flow, current)["last_outcome"] == expected
    assert current["attempt_ids"] == case["attempt_ids"]
    if expected == "applied":
        assert after["recognition_revision"] == before["recognition_revision"] + 1
        assert after["answer_text"] == ANSWER and current["current_decision"]["verdict"] == "incorrect"
    else:
        assert after == before and current["current_decision"] == case["current_decision"]
    for client in (pipeline.client, pipeline.review_client):
        request = client.calls[0]
        assert json.loads(request["text"])["task_input"]["original_target"]["label"] == "14(2)"
        assert ANSWER not in request["text"]
    assert len(pipeline.client.calls) == len(pipeline.review_client.calls) == 1
