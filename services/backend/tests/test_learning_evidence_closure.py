"""Regression checks for AI evidence history, attribution and subject revisions."""
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import evidence_count, knowledge, learning, link, prepare_latest_math_curriculum, reviewed
from test_multimodal_worker import assessment, enable_page_assessments
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import LearningEvidence
from xueban_backend.learning_reports import build_snapshot, render_report
from xueban_backend.learning_rules import authoritative_decision, model_decision_usable
from xueban_backend.models import Student


def test_two_model_whole_page_correction_preserves_history_and_daily_progress(flow):
    prepare_latest_math_curriculum(flow)
    current = {"verdict": "incorrect"}

    def evaluate(items):
        for item in items:
            item["subject"] = "数学"
            item["answer_text"] = "2" if current["verdict"] == "correct" else "3"
            item["assessment"] = assessment(current["verdict"])
            item["assessment"]["verification"]["mode"] = "two_model"
            item["assessment"].update(knowledge_points=["有理数的加减法"], correct_answer="2",
                                      correction_method="两个 1 相加得到 2。")

    enable_page_assessments(flow, evaluate)
    case = flow.ready()
    first_decision_id = case["current_decision"]["id"]
    learning(flow).drain()
    assert knowledge(flow)["states"][0]["observed_error_count"] == 1
    asset = flow.uploaded()
    submitted = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-16T08:00:00Z", "reported_help": "none_reported"})
    assert submitted.status_code == 202, submitted.text
    learning(flow).drain()
    assert knowledge(flow)["states"][0]["observed_error_count"] == 1
    current["verdict"] = "correct"
    assert flow.worker.run_once()
    learning(flow).drain()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert case["progress"] == "pending"
    assert case["current_decision"]["verification"]["mode"] == "two_model"
    state = knowledge(flow)["states"][0]
    assert state["learning_status"] == "consolidating"
    assert state["observed_error_count"] == 1
    assert state["model_evidence_count"] == 2
    assert state["staff_verified_evidence_count"] == 0
    assert state["assessment_source"] == "model"
    with Session(flow.database.engine) as session:
        rows = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        assert len(rows) == 2
        assert {row.document["source_role"] for row in rows} == {"model"}
        assert first_decision_id in {row.document["decision_id"] for row in rows}
        assert session.get(Entity, first_decision_id).document["validity"] == "stale"
        for row in rows:
            decision = session.get(Entity, row.document["decision_id"])
            assert decision.document["verification"]["mode"] == "two_model"
            assert decision.annotations["assessment"]["verification"]["mode"] == "two_model"
        snapshot = build_snapshot(session, session.get(Student, "student_a"), date(2026, 10, 16),
                                  date(2026, 10, 17), "daily")
    assert snapshot["school_tasks"]["staff_verified"] == 0
    assert snapshot["school_tasks"]["corrected_resolved"] == 0
    assert snapshot["school_tasks"]["ai_corrected_pending"] == 1
    assert snapshot["school_tasks"]["ai_verdicts"]["correct"] == 1
    [mistake] = snapshot["mistakes"]
    assert mistake["source_role"] == "model" and mistake["verdict"] == "答对"
    assert mistake["correction"] == "已提交订正，AI 初判正确（待老师核实）"
    assert snapshot["focus"][0]["cards"][0]["certainty"] == "AI 初判，尚未经老师核实"
    html = render_report({"student_name": "合成学生", "corrections": [], "report": {
        "cadence": "daily", "snapshot": snapshot, "version": 1, "published_at": None,
        "staff_note": None, "next_step": None}})
    assert "已提交订正，AI 初判正确（待老师核实）" in html
    assert flow.pipeline.calls == ["recognize", "recognize"]


def test_ai_correct_first_attempt_is_not_reported_as_a_correction(flow):
    enable_page_assessments(flow, lambda items: items[0].update(assessment=assessment("correct")))
    flow.ready()
    with Session(flow.database.engine) as session:
        snapshot = build_snapshot(session, session.get(Student, "student_a"), date(2026, 10, 16),
                                  date(2026, 10, 17), "daily")
    assert snapshot["school_tasks"]["ai_verdicts"]["correct"] == 1
    assert snapshot["school_tasks"]["ai_corrected_pending"] == 0
    assert snapshot["mistakes"] == []


def test_uploading_correction_preserves_ai_history_and_records_improvement(flow):
    case = reviewed(flow)
    teacher(flow)
    link(flow, case)
    learning(flow).drain()
    assert knowledge(flow)["states"][0]["observed_error_count"] == 1
    asset = flow.uploaded()
    submitted = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "none_reported"})
    assert submitted.status_code == 202, submitted.text
    learning(flow).drain()
    pending = knowledge(flow)["states"][0]
    assert pending["observed_error_count"] == 1
    assert pending["learning_status"] == "needs_help_now"
    assert pending["model_evidence_count"] == 1
    assert flow.worker.run_once()
    flow.pipeline.verdict = "correct"
    flow.start_check(flow.client.get(f"/v1/cases/{case['id']}").json())
    assert flow.worker.run_once()
    learning(flow).drain()
    state = knowledge(flow)["states"][0]
    assert state["learning_status"] == "consolidating"
    assert state["observed_error_count"] == 1
    assert state["model_evidence_count"] == 2
    assert state["staff_verified_evidence_count"] == 0
    assert state["assessment_source"] == "model"
    detail = flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").json()
    assert [item["source_role"] for item in detail["evidence"]] == ["model", "model"]
    assert [item["event_type"] for item in detail["evidence"]] == ["school_first_attempt", "correction"]


@pytest.mark.parametrize("reason", ["question_revision", "question_subject_revision", "answer_recognition_revision",
                                   "staff_review", "new_check_decision", "layout_repair", None])
def test_only_a_new_attempt_keeps_a_stale_ai_observation(reason):
    decision = {"id": "d1", "created_at": "2026-10-19T00:00:00Z", "origin": "model", "validity": "stale",
                "verdict": "incorrect", "input_versions": {"attempt_id": "a1"}, "invalidated_by_event_id": "e1"}
    assert authoritative_decision("a1", [decision], {"e1": reason}) is None
    assert authoritative_decision("a1", [decision], {"e1": "new_attempt"}) == decision


@pytest.mark.parametrize("patch", [
    {"uncertainties": ["数字可能是 1 或 7"]},
    {"verification": {"status": "conflict", "reason_codes": ["answer_disagreement"]}},
    {"verification": {"status": "degraded", "reason_codes": ["provider_unavailable"]}},
    {"verification": {"status": "matched", "independent_readback": False, "reason_codes": []}},
    {"verification": {"status": "matched", "independent_readback": None, "reason_codes": []}},
    {"verification": {"status": "matched", "reason_codes": []}},
    {"verification": {"status": "reviewed", "reason_codes": ["answer_disagreement"]}},
])
def test_uncertain_ai_decisions_do_not_become_knowledge_evidence(flow, patch):
    case = reviewed(flow)
    teacher(flow)
    link(flow, case)
    with Session(flow.database.engine) as session:
        row = session.get(Entity, case["current_decision"]["id"])
        row.document = {**row.document, **patch}
        stored_case = session.get(Entity, case["id"])
        stored_case.document = {**stored_case.document, "current_decision": row.document}
        session.commit()
    learning(flow).drain()
    assert evidence_count(flow) == 0
    overview = flow.client.get("/v1/students/student_a/variant-practice").json()
    assert overview["groups"] == []
    assert overview["pending_count"] == 1


def test_safe_independent_review_is_model_evidence_not_teacher_confirmation():
    assert model_decision_usable({"verification": {"status": "reviewed", "independent_readback": True,
                                                   "reason_codes": []}, "uncertainties": []})
    assert model_decision_usable({})  # Legacy decisions keep their model attribution.


@pytest.mark.parametrize("cadence", ["daily", "half_month"])
def test_parent_reports_label_ai_source_without_claiming_staff_verification(flow, cadence):
    case = reviewed(flow)
    teacher(flow)
    link(flow, case)
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        snapshot = build_snapshot(session, session.get(Student, "student_a"), date(2026, 10, 16),
                                  date(2026, 10, 17) if cadence == "daily" else date(2026, 11, 1), cadence)
        [evidence] = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        assert evidence.document["source_role"] == "model"
    assert snapshot["school_tasks"]["staff_verified"] == 0
    assert snapshot["school_tasks"]["verdicts"]["incorrect"] == 0
    assert snapshot["school_tasks"]["ai_judged"] == 1
    assert snapshot["school_tasks"]["ai_verdicts"]["incorrect"] == 1
    card = snapshot["focus"][0]["cards"][0]
    assert card["source_role"] == "model"
    assert card["certainty"] == "AI 初判，尚未经老师核实"
    assert card["staff_explanation"] is None
    body = {"student_name": "合成学生", "corrections": [], "report": {
        "cadence": cadence, "snapshot": snapshot, "version": 1, "published_at": None,
        "staff_note": None, "next_step": None}}
    html = render_report(body)
    assert "AI 初判" in html and "1 次已核实作答" not in html and "1 次核实作答" not in html
    if cadence == "daily":
        assert snapshot["mistakes"][0]["source_role"] == "model"
        assert snapshot["mistakes"][0]["certainty"] == "AI 初判，尚未经老师核实"


def test_rejected_latest_link_cannot_be_restored_by_practice_label_fallback(flow):
    case = reviewed(flow)
    teacher(flow)
    current = link(flow, case)
    link(flow, case, status="rejected", expected=current["id"])
    with Session(flow.database.engine) as session:
        stored = session.get(Entity, case["id"])
        stored.document = {**stored.document, "current_decision": {
            **stored.document["current_decision"], "knowledge_points": ["有理数加减运算"]}}
        session.commit()
    overview = flow.client.get("/v1/students/student_a/variant-practice").json()
    assert all(group["knowledge_id"] != "kp-rational-add-sub" for group in overview["groups"])
    response = flow.client.post("/v1/students/student_a/practice-sessions", json={
        "purpose": "variant", "willing": True, "knowledge_id": "kp-rational-add-sub",
        "source_case_id": case["id"]})
    assert response.status_code == 409 and response.json()["code"] == "practice_not_needed"


def test_subject_change_increments_revision_rejects_old_screen_and_supersedes_queued_check(flow):
    prepare_latest_math_curriculum(flow)
    case = flow.ready()
    job = flow.start_check(case)
    subject_url = f"/v1/questions/{case['question_id']}/subject"
    body = {"expected_question_revision": 1, "subject": "数学"}
    changed = flow.client.put(subject_url, json=body)
    assert changed.status_code == 200, changed.text
    assert changed.json()["question_revision"] == 2
    stale = flow.client.put(subject_url, json={**body, "subject": "语文"})
    assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "superseded"
    fresh = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert fresh["current_question_revision"] == 2 and fresh["current_decision"] is None
    flow.start_check(fresh)
    assert flow.worker.run_once()
    decision = flow.client.get(f"/v1/cases/{case['id']}").json()["current_decision"]
    assert decision["input_versions"]["question_revision"] == 2


def test_subject_change_fences_already_running_check(flow):
    prepare_latest_math_curriculum(flow)
    case = flow.ready()
    job = flow.start_check(case)
    claimed = flow.worker.claim()
    kind, context = flow.worker._load(*claimed)
    result = flow.pipeline.check(**context)
    changed = flow.client.put(f"/v1/questions/{case['question_id']}/subject",
                              json={"expected_question_revision": 1, "subject": "数学"})
    assert changed.status_code == 200, changed.text
    assert flow.worker.finish(*claimed, result=result)
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "superseded"
    assert flow.client.get(f"/v1/cases/{case['id']}").json()["current_decision"] is None
