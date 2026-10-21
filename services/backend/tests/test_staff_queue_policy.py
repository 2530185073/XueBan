"""Actual human tasks stay distinct from AI evidence and AI service failures."""
from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import review
from xueban_backend.correction_models import Entity
from xueban_backend.staff import case_queue_reason, needs_staff


def documents(verdict="correct", **decision_changes):
    decision = {"origin": "model", "validity": "candidate", "verdict": verdict,
                "reason_code": None, "uncertainties": [], **decision_changes}
    case = {"lifecycle": "active", "progress": "pending", "current_decision": decision, "check_ids": ["check_1"]}
    attempt = {"recognition_status": "ready", "answer_status": "present", "recognition_uncertainties": []}
    return case, attempt, {"source": "recognizer", "recognition_uncertainties": []}


@pytest.mark.parametrize("verdict", ["correct", "incorrect", "partially_correct", "unanswered"])
@pytest.mark.parametrize("status", [None, "matched", "reviewed"])
def test_readable_ai_initial_is_not_a_required_staff_task(verdict, status):
    case, attempt, question = documents(verdict)
    if status:
        case["current_decision"]["verification"] = {"mode": "two_model", "status": status,
            "independent_readback": True, "reason_codes": []}
    if verdict == "unanswered":
        attempt["answer_status"] = "blank"
    before = deepcopy((case, attempt, question))
    assert needs_staff(case, attempt, question) is None
    assert case_queue_reason(case, attempt, question) == "ai_initial"
    assert (case, attempt, question) == before  # No confirmation or mastery mutation.


@pytest.mark.parametrize("codes", [
    ["provider_unavailable"], ["reconciliation_unavailable"], ["target_binding_conflict"],
    ["answer_disagreement", "model_disagreement"], ["question_unit_incomplete"],
])
def test_machine_failures_do_not_become_human_image_tasks(codes):
    case, attempt, question = documents("unable_to_judge", validity="needs_review", reason_code="recognition_conflict",
        verification={"mode": "two_model", "status": "conflict", "independent_readback": False, "reason_codes": codes})
    attempt.update(answer_status="unclear", recognition_status="needs_revision")
    assert needs_staff(case, attempt, question) is None
    assert case_queue_reason(case, attempt, question) == "ai_pending"


def test_explicit_independent_visual_unreadability_remains_a_staff_task():
    case, attempt, question = documents("unable_to_judge", validity="needs_review", reason_code="verification_conflict",
        verification={"mode": "two_model", "status": "conflict", "independent_readback": False,
                      "reason_codes": ["answer_disagreement", "visual_unreadable"]})
    assert needs_staff(case, attempt, question) == "unable_to_judge"


@pytest.mark.parametrize("reason", ["unclear_image", "incomplete_question", "insufficient_process", "needs_staff"])
def test_legacy_explicit_visual_or_staff_request_still_visible(reason):
    args = documents("unable_to_judge", validity="needs_review", reason_code=reason)
    assert needs_staff(*args) == "unable_to_judge"


@pytest.mark.parametrize("reason", ["incomplete_question", "insufficient_process", "unsupported_type"])
def test_candidate_marker_cannot_hide_explicit_missing_context(reason):
    args = documents("incorrect", reason_code=reason)
    assert needs_staff(*args) == "unable_to_judge"
    assert case_queue_reason(*args) == "unable_to_judge"


def test_failed_recognition_is_unfinished_ai_work_not_bad_handwriting():
    case, attempt, question = documents()
    case["current_decision"] = None
    attempt["recognition_status"] = "failed"
    assert needs_staff(case, attempt, question) is None
    assert case_queue_reason(case, attempt, question) == "ai_pending"


def test_candidate_with_unfinished_recognition_is_not_labeled_usable():
    case, attempt, question = documents()
    attempt["recognition_status"] = "pending"
    assert needs_staff(case, attempt, question) is None
    assert case_queue_reason(case, attempt, question) == "ai_pending"


def test_staff_revision_followup_is_not_a_task_while_check_running():
    case, attempt, question = documents()
    case.update(current_decision=None, progress="rechecking")
    question["source"] = "staff_revision"
    assert needs_staff(case, attempt, question) is None
    case["progress"] = "pending"
    assert needs_staff(case, attempt, question) == "awaiting_check"


def test_all_work_retains_clear_candidate_and_allows_voluntary_review_and_appeal(flow):
    case = flow.ready()
    flow.pipeline.verdict = "correct"
    flow.start_check(case)
    assert flow.worker.run_once()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    original = deepcopy(case["current_decision"])
    appeal = flow.post(f"/v1/cases/{case['id']}/help-requests", {
        "expected_revision": case["revision"], "attempt_id": case["current_attempt_id"],
        "kind": "appeal", "note": "我想请老师再看一下。"})
    assert appeal.status_code == 201
    assert flow.client.get("/v1/review-queue?view=all").status_code == 403
    teacher(flow)
    assert flow.client.get("/v1/review-queue").json()["items"] == []
    all_work = flow.client.get("/v1/review-queue?view=all").json()["items"]
    assert len(all_work) == 1 and all_work[0]["case_id"] == case["id"]
    assert all_work[0]["reason"] == "ai_initial"
    assert flow.client.get("/v1/help-requests").json()["items"][0]["id"] == appeal.json()["id"]
    current = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert current["current_decision"] == original and original["validity"] == "candidate"
    reviewed = review(flow, current)
    assert reviewed["current_decision"]["origin"] == "staff"
    assert reviewed["progress"] == "verified_resolved"
    assert flow.client.get("/v1/review-queue").json()["items"] == []
    assert flow.client.get("/v1/review-queue?view=all").json()["items"][0]["reason"] == "staff_verified"


def test_all_work_keeps_scope_and_active_lifecycle_filter(flow):
    case = flow.ready()
    teacher(flow)
    assert [item["student_id"] for item in flow.client.get("/v1/review-queue?view=all").json()["items"]] == ["student_a"]
    assert flow.client.get("/v1/students/student_b/homeworks").status_code == 404
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(Entity, case["id"])
        row.document = {**row.document, "lifecycle": "superseded"}
    assert flow.client.get("/v1/review-queue?view=all").json()["items"] == []
