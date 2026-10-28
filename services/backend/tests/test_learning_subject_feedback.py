"""Graph-free feedback over isolated HTTP/DB fixtures; no live models/services."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_learning import knowledge
from test_practice import clock  # noqa: F401
from test_student_variants import Client, candidate, queue, run, source, start, verification
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import (CurriculumGraph, LearningEvidence, LearningProjection,
    PracticeItem, PracticeResponse, PracticeSession, StudentKnowledgeState, VariantGeneration)
from xueban_backend.learning_projection import dirty_students, project_student
from xueban_backend.models import Homework


def prepared(flow, *, with_graph=False):
    case = source(flow)
    if not with_graph:
        with Session(flow.database.engine) as session, session.begin():
            session.get(CurriculumGraph, "test-bnup-math-g7-latest").status = "draft"
    task = queue(flow, case)
    value = candidate()
    assert run(flow, task, Client([value, verification(value)]))["status"] == "ready"
    response = start(flow, task)
    assert response.status_code == 201, response.text
    return case, task, response.json()


def math_feedback(flow):
    return next(item for item in knowledge(flow)["subjects"] if item["subject"] == "数学")


def answer(flow, activity, value="7"):
    result = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": activity["items"][0]["id"], "answers": [{"field_id": "blank1", "answer_text": value}]})
    assert result.status_code == 201, result.text
    return result.json()


def reveal(flow, activity):
    response = flow.client.post(f"/v1/practice-sessions/{activity['id']}/items/{activity['items'][0]['id']}/solution", json={})
    assert response.status_code == 200, response.text


def test_missing_reviewed_graph_shows_real_samples_and_stops_noop_projection_loop(flow):
    _, _, activity = prepared(flow)
    assert activity["graph_version"] is None
    body = knowledge(flow)
    assert body["graph_version"] is None and body["nodes"] == body["edges"] == body["states"] == []
    assert body["graph_unavailable_reason"] == "no_reviewed_graph"
    assert body["projection"]["pending"] is False
    sample = math_feedback(flow)
    assert sample["scorable_count"] == sample["incorrect_count"] == 1
    assert sample["score_percent"] == 0 and sample["graph_ready"] is False
    assert sample["homework_feedback"] == {"model_count": 1, "staff_count": 0, "pending_count": 0}
    assert sample["practice_feedback"]["total"] == 0  # Starting or revealing is not answering.
    with Session(flow.database.engine) as session, session.begin():
        assert ("tenant_a", "student_a") in dirty_students(session)
        assert project_student(session, "tenant_a", "student_a")["status"] == "no_published_graph"
    with Session(flow.database.engine) as session:
        assert ("tenant_a", "student_a") not in dirty_students(session)
        projection = session.get(LearningProjection, "student_a")
        assert projection.evidence_revision == projection.state_version == 0
        assert session.scalar(select(func.count()).select_from(LearningEvidence)) == 0
        assert session.scalar(select(func.count()).select_from(StudentKnowledgeState)) == 0
        assert session.get(CurriculumGraph, "pep-math-junior-pilot-1").review_status == "pilot_seed_unreviewed"


def test_wrong_then_retry_correct_preserves_first_result_without_inventing_mastery(flow, clock):
    _, _, activity = prepared(flow)
    assert answer(flow, activity, "8")["result"] == "incorrect"
    clock["t"] += timedelta(minutes=1)
    response = flow.client.post(f"/v1/practice-sessions/{activity['id']}/retry", json={})
    assert response.status_code == 201, response.text
    assert answer(flow, response.json())["result"] == "correct"
    feedback = math_feedback(flow)["practice_feedback"]
    assert feedback["total"] == 2 and feedback["first_unassisted_count"] == 1
    assert feedback["first_unassisted_correct"] == 0 and feedback["first_unassisted_incorrect"] == 1
    assert feedback["retry_count"] == 1 and feedback["assisted_count"] == 0
    assert [(item["kind"], item["result"]) for item in feedback["items"]] == [
        ("retry", "correct"), ("first_unassisted", "incorrect")]
    assert knowledge(flow)["nodes"] == knowledge(flow)["states"] == []
    assert math_feedback(flow)["score_percent"] == 0  # Practice does not rewrite homework.
    # Tied timestamps must not reorder the original behind a retry's UUID.
    with Session(flow.database.engine) as session, session.begin():
        rows = session.scalars(select(PracticeResponse).order_by(PracticeResponse.occurred_at)).all()
        rows[1].occurred_at = rows[0].occurred_at
    assert math_feedback(flow)["practice_feedback"] == {**feedback, "items": [
        {**feedback["items"][0], "occurred_at": feedback["items"][1]["occurred_at"]}, feedback["items"][1]]}


@pytest.mark.parametrize("before", [True, False])
def test_solution_view_uses_submission_snapshot_and_never_rewrites_first_result(flow, before):
    _, _, activity = prepared(flow)
    if before:
        reveal(flow, activity)
        assert math_feedback(flow)["practice_feedback"]["total"] == 0
    assert answer(flow, activity)["result"] == "correct"
    if not before:
        reveal(flow, activity)
    feedback = math_feedback(flow)["practice_feedback"]
    assert feedback["first_unassisted_correct"] == (0 if before else 1)
    assert feedback["assisted_count"] == (1 if before else 0)
    assert feedback["retry_count"] == 0
    assert feedback["items"][0]["hint_used"] is before
    assert "未记录" in knowledge(flow)["notice"]


@pytest.mark.parametrize("change", ["verification", "fingerprint", "owner", "session_owner", "generation_owner", "failed_generation",
    "question_revision", "blocked", "curriculum", "response_graph"])
def test_invalid_or_out_of_scope_practice_is_not_advertised_as_verified_feedback(flow, change):
    case, task, activity = prepared(flow)
    answer(flow, activity)
    with Session(flow.database.engine) as session, session.begin():
        item = session.get(PracticeItem, activity["items"][0]["id"])
        if change == "verification":
            item.verification = {"status": "failed"}
        elif change == "fingerprint":
            item.generation = {**item.generation, "source_fingerprint": "stale"}
        elif change == "owner":
            item.owner_student_id = "student_ungranted"
        elif change == "session_owner":
            session.get(PracticeSession, activity["id"]).student_id = "student_ungranted"
        elif change == "generation_owner":
            session.get(VariantGeneration, task["job_id"]).student_id = "student_ungranted"
        elif change == "failed_generation":
            session.get(VariantGeneration, task["job_id"]).status = "failed"
        elif change == "question_revision":
            session.get(Entity, case["question_id"]).revision += 1
        elif change == "blocked":
            session.get(Homework, flow.homework_id).processing_blocked = True
        elif change == "curriculum":
            homework = session.get(Homework, flow.homework_id)
            homework.curriculum = {**homework.curriculum, "publisher_code": "PEP"}
        elif change == "response_graph":
            response = session.scalar(select(PracticeResponse).where(PracticeResponse.session_id == activity["id"]))
            response.graph_version = "pep-math-junior-pilot-1"
    assert math_feedback(flow)["practice_feedback"]["total"] == 0


@pytest.mark.parametrize("change", ["blank", "recognition_uncertainty", "stale_revision", "broken_pointer"])
def test_uncertain_or_stale_homework_waits_for_verification_outside_sample_score(flow, change):
    case, _, _ = prepared(flow)
    with Session(flow.database.engine) as session, session.begin():
        attempt = session.get(Entity, case["current_attempt_id"])
        if change == "blank":
            attempt.document = {**attempt.document, "answer_text": "", "answer_status": "blank"}
        elif change == "recognition_uncertainty":
            attempt.document = {**attempt.document, "recognition_uncertainties": ["unclear_handwriting"]}
        elif change == "stale_revision":
            attempt.document = {**attempt.document, "recognition_revision": attempt.document["recognition_revision"] + 1}
        else:
            current = session.get(Entity, case["id"])
            current.document = {**current.document, "current_decision": {**current.document["current_decision"], "id": "missing"}}
    sample = math_feedback(flow)
    assert sample["scorable_count"] == 0 and sample["score_percent"] is None
    assert sample["homework_feedback"] == {"model_count": 0, "staff_count": 0, "pending_count": 1}


def test_practice_feedback_does_not_bypass_student_authorization(flow):
    _, _, activity = prepared(flow)
    answer(flow, activity)
    assert flow.client.get("/v1/students/student_ungranted/knowledge").status_code == 404
    assert flow.client.get("/v1/students/student_b/knowledge").status_code == 404


def test_reviewed_graph_still_keeps_practice_separate_from_formal_evidence_and_state(flow):
    _, _, activity = prepared(flow, with_graph=True)
    with Session(flow.database.engine) as session, session.begin():
        before = project_student(session, "tenant_a", "student_a")
    assert answer(flow, activity)["result"] == "correct"
    feedback = math_feedback(flow)
    assert feedback["graph_ready"] is True
    assert knowledge(flow)["graph_unavailable_reason"] is None
    assert feedback["practice_feedback"]["first_unassisted_correct"] == 1
    with Session(flow.database.engine) as session, session.begin():
        after = project_student(session, "tenant_a", "student_a")
        assert after["evidence_revision"] == before["evidence_revision"]
        assert after["state_version"] == before["state_version"]
        assert after["added"] == after["invalidated"] == 0


@pytest.mark.parametrize("reference,expected_total", [("+3", 0), ("-3", 1)])
@pytest.mark.parametrize("stored_result", ["correct", "incorrect"])
def test_false_numeric_reference_excludes_legacy_verified_responses_without_rewriting_history(
        flow, reference, expected_total, stored_result):
    _, _, activity = prepared(flow)
    answer(flow, activity)
    item_id = activity["items"][0]["id"]
    submitted = reference if stored_result == "correct" else ("-3" if reference == "+3" else "+3")
    # Reproduce an already persisted legacy item. New generation/grading
    # guards must not prevent us from testing historical bad references.
    with Session(flow.database.engine) as session, session.begin():
        item = session.get(PracticeItem, item_id)
        item.stem_text = "(-24)÷8=____"
        item.answer_fields = [{"id": "blank1", "label": "第1空"}]
        item.answer_values = []
        item.field_answers = [{"field_id": "blank1", "answer_kind": "number", "answer_values": [reference]}]
        item.solution_text = f"(-24)÷8={reference}。"
        item.verification = {"source": "ai", "status": "verified", "generator_model": "generator",
            "verifier_model": "verifier", "heterogeneous_models": True}
        response = session.scalar(select(PracticeResponse).where(PracticeResponse.item_id == item_id))
        response.answer_text, response.result = f"blank1: {submitted}", stored_result
        response_id = response.id

    sample = math_feedback(flow)
    feedback = sample["practice_feedback"]
    assert feedback["total"] == feedback["first_unassisted_count"] == expected_total
    assert feedback["first_unassisted_correct"] == (expected_total if stored_result == "correct" else 0)
    assert feedback["first_unassisted_incorrect"] == (expected_total if stored_result == "incorrect" else 0)
    assert len(feedback["items"]) == expected_total
    assert feedback["assisted_count"] == feedback["retry_count"] == 0
    assert sample["scorable_count"] == sample["incorrect_count"] == 1  # Original homework unchanged.
    with Session(flow.database.engine) as session:
        item = session.get(PracticeItem, item_id)
        response = session.get(PracticeResponse, response_id)
        assert item.status == "ai_verified" and item.verification["status"] == "verified"
        assert item.field_answers[0]["answer_values"] == [reference]
        assert response.answer_text == f"blank1: {submitted}" and response.result == stored_result
        assert session.scalar(select(func.count()).select_from(PracticeResponse)) == 1
        assert session.scalar(select(func.count()).select_from(LearningEvidence)) == 0
        assert session.scalar(select(func.count()).select_from(StudentKnowledgeState)) == 0


@pytest.mark.parametrize("change", ["blank_count", "answer_mapping", "missing_fields", "duplicate_answers", "top_level_answers"])
def test_legacy_malformed_fillblank_reference_is_excluded_without_repairing_saved_items(flow, change):
    _, _, activity = prepared(flow)
    answer(flow, activity)
    item_id = activity["items"][0]["id"]
    with Session(flow.database.engine) as session, session.begin():
        item = session.get(PracticeItem, item_id)
        if change == "blank_count":
            item.stem_text = "3+4=____；5+6=____"  # Two blanks, only one stored answer field.
        elif change == "answer_mapping":
            item.field_answers = [{**item.field_answers[0], "field_id": "missing-blank"}]
        elif change == "missing_fields":
            item.answer_fields = None
        elif change == "duplicate_answers":
            item.field_answers = [*item.field_answers, item.field_answers[0]]
        else:
            item.answer_values = ["7"]  # Legacy conflicting top-level and per-field representations.
        saved = {name: getattr(item, name) for name in (
            "stem_text", "answer_fields", "field_answers", "answer_values", "status", "verification")}
        response = session.scalar(select(PracticeResponse).where(PracticeResponse.item_id == item_id))
        response_id, response_text, response_result = response.id, response.answer_text, response.result

    sample = math_feedback(flow)
    assert sample["practice_feedback"] == {
        "total": 0, "first_unassisted_count": 0, "first_unassisted_correct": 0,
        "first_unassisted_incorrect": 0, "assisted_count": 0, "retry_count": 0,
        "items": [], "truncated": False,
    }
    assert sample["scorable_count"] == sample["incorrect_count"] == 1
    with Session(flow.database.engine) as session:
        item = session.get(PracticeItem, item_id)
        assert {name: getattr(item, name) for name in saved} == saved
        response = session.get(PracticeResponse, response_id)
        assert (response.answer_text, response.result) == (response_text, response_result)
        assert session.scalar(select(func.count()).select_from(PracticeResponse)) == 1
        assert session.scalar(select(func.count()).select_from(LearningEvidence)) == 0
        assert session.scalar(select(func.count()).select_from(StudentKnowledgeState)) == 0
