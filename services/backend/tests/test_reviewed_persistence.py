"""Reconciliation persistence through HTTP, the worker and learning projections.

These tests inject final page outputs; they never call an external model.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401 -- shared isolated DB fixture
from test_learning import knowledge, learning, prepare_latest_math_curriculum
from test_multimodal_worker import assessment, current_case, enable_page_assessments
from xueban_backend.correction_models import Entity, Task
from xueban_backend.learning_models import LearningEvidence
from xueban_backend.learning_reports import build_snapshot
from xueban_backend.models import Student


def reviewed_assessment(verdict="incorrect"):
    result = assessment(verdict, "reviewed")
    result["verification"].update(mode="two_model", reviewed=True)
    result.update(
        knowledge_points=["有理数的加减法"],
        correct_answer="2" if verdict == "incorrect" else None,
        correction_method="两个 1 相加得到 2。" if verdict == "incorrect" else None,
    )
    return result


@pytest.mark.parametrize("verdict", ["correct", "incorrect"])
def test_resolved_reconciliation_persists_as_model_candidate_not_staff_review(flow, verdict):
    flow.worker.settings = replace(flow.worker.settings, auto_check_after_recognition=True)

    def resolved(items):
        items[0].update(
            subject="数学", answer_text="2" if verdict == "correct" else "3",
            recognition_status="ready", uncertainties=[],
            assessment=reviewed_assessment(verdict),
        )

    enable_page_assessments(flow, resolved)
    case = flow.ready()
    decision = case["current_decision"]
    assert decision["verification"] == {
        "mode": "two_model", "status": "reviewed",
        "independent_readback": True, "reason_codes": [],
    }
    assert decision["uncertainties"] == []
    assert decision["verdict"] == verdict
    assert decision["validity"] == "candidate" and decision["origin"] == "model"
    assert decision["confirmed_by"] is None
    assert decision["confirmed_at"] is None
    assert decision["confirmation_source"] is None
    assert case["progress"] == ("pending" if verdict == "correct" else "needs_correction")
    assert case["review_ids"] == [] and case["help_request_ids"] == []
    assert flow.pipeline.calls == ["recognize"]
    assert not flow.worker.run_once()
    assert len(flow.homework()["job_ids"]) == 1


@pytest.mark.parametrize("remaining", ["reason_codes", "assessment_uncertainty",
                                      "item_uncertainty", "not_independent"])
def test_reviewed_status_does_not_override_remaining_evidence_problems(flow, remaining):
    def unresolved(items):
        item = items[0]
        item["assessment"] = reviewed_assessment()
        if remaining == "reason_codes":
            item["assessment"]["verification"]["reason_codes"] = ["visual_unreadable"]
        elif remaining == "assessment_uncertainty":
            item["assessment"]["uncertainties"] = ["末位数字仍看不清。"]
        elif remaining == "item_uncertainty":
            item["uncertainties"] = ["末位数字仍看不清。"]
        else:
            item["assessment"]["verification"]["independent_readback"] = False

    enable_page_assessments(flow, unresolved)
    case = flow.ready()
    decision = case["current_decision"]
    assert decision["validity"] == "needs_review"
    assert decision["verdict"] == "unable_to_judge"
    assert decision["uncertainties"]
    assert decision["correct_answer"] is None and decision["correction_method"] is None
    assert decision["knowledge_points"] == []
    assert decision["origin"] == "model" and decision["confirmed_by"] is None
    assert case["progress"] == "pending"
    assert case["help_request_ids"] == []


@pytest.mark.parametrize("service_reason", ["provider_unavailable", "reconciliation_unavailable"])
@pytest.mark.parametrize("service_status", ["degraded", "conflict"])
def test_same_page_isolates_reviewed_visual_and_service_results_and_teacher_counts(flow, service_reason, service_status):
    prepare_latest_math_curriculum(flow)
    flow.worker.settings = replace(flow.worker.settings, auto_check_after_recognition=True)

    def mixed(items):
        clear = items[0]
        clear.update(subject="数学", label="1", assessment=reviewed_assessment())
        visual = deepcopy(clear)
        visual.update(label="2", stem_text="2 + 2 = ?", recognition_status="needs_revision",
                      answer_status="unclear", uncertainties=["末位数字仍看不清。"])
        visual["assessment"]["verification"].update(status="conflict", reason_codes=["visual_unreadable"])
        visual["assessment"]["uncertainties"] = ["末位数字仍看不清。"]
        unavailable = deepcopy(clear)
        unavailable.update(label="3", stem_text="3 + 3 = ?", recognition_status="needs_revision",
                           uncertainties=["核对服务暂未完成。"])
        unavailable["assessment"]["verification"].update(
            status=service_status, independent_readback=False, reason_codes=[service_reason])
        unavailable["assessment"]["uncertainties"] = ["核对服务暂未完成。"]
        # Unresolved rows deliberately retain the first pass's wrong-answer
        # fields here: the worker must withhold them rather than trust status.
        items[:] = [clear, visual, unavailable]

    enable_page_assessments(flow, mixed)
    flow.ready()
    homework = flow.homework()
    cases = [current_case(flow, case_id) for case_id in homework["case_ids"]]
    decisions = [case["current_decision"] for case in cases]
    clear, visual, unavailable = decisions
    assert [decision["verdict"] for decision in decisions] == ["incorrect", "unable_to_judge", "unable_to_judge"]
    assert [decision["validity"] for decision in decisions] == ["candidate", "needs_review", "needs_review"]
    assert clear["verification"]["status"] == "reviewed"
    assert clear["verification"]["reason_codes"] == [] and clear["uncertainties"] == []
    assert clear["correct_answer"] == "2" and clear["correction_method"]
    assert visual["verification"]["reason_codes"] == ["visual_unreadable"]
    assert unavailable["verification"]["reason_codes"] == [service_reason]
    assert unavailable["verification"]["status"] == service_status
    assert unavailable["reason_code"] == "verification_conflict"
    service_summary = unavailable["basis"][0]["summary"]
    assert "服务暂未完成" in service_summary
    assert "识读仍有疑问" not in service_summary
    for decision in (visual, unavailable):
        assert decision["uncertainties"]
        assert decision["correct_answer"] is None and decision["correction_method"] is None
        assert decision["knowledge_points"] == []
        assert not decision.get("answer_checks")
    assert all(decision["origin"] == "model" and decision["confirmed_by"] is None for decision in decisions)
    assert all(case["review_ids"] == [] and case["help_request_ids"] == [] for case in cases)

    learning(flow).drain()
    states = knowledge(flow)["states"]
    state = next(value for value in states if value["knowledge_id"] == "kp-rational-add-sub")
    assert state["model_evidence_count"] == 1
    assert state["staff_verified_evidence_count"] == 0
    assert state["observed_error_count"] == 1
    assert state["assessment_source"] == "model"
    with Session(flow.database.engine) as session:
        evidence = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        assert len(evidence) == 1
        assert evidence[0].document["decision_id"] == clear["id"]
        assert evidence[0].document["source_role"] == "model"
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind.in_(["review", "help"]))) == 0
        assert session.scalar(select(func.count()).select_from(Task)) == 1
        snapshot = build_snapshot(session, session.get(Student, "student_a"), date(2026, 10, 16),
                                  date(2026, 10, 17), "daily")
    assert snapshot["school_tasks"]["questions"] == 3
    assert snapshot["school_tasks"]["staff_verified"] == 0
    assert snapshot["school_tasks"]["ai_judged"] == 1
    assert snapshot["school_tasks"]["ai_verdicts"]["incorrect"] == 1
    assert flow.pipeline.calls == ["recognize"]
    assert not flow.worker.run_once()
    # Reopening persisted results cannot mix the unresolved rows into the
    # reviewed result or convert the AI provenance into staff confirmation.
    assert [current_case(flow, case["id"])["current_decision"] for case in cases] == decisions
