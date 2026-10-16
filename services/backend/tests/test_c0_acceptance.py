"""C0 correction acceptance (blueprint-v3/10 §6): system behaviour per scenario.

These run the real API, worker and learning projection with a synthetic model.
Model quality for the same rows (false-wrong rate, first-error localisation,
coverage, hint leakage) is measured on the frozen labelled split by
evals/correction and is not claimed here.

| C0 scenario | Automated system checks |
|---|---|
| correct / equivalent answer | test_correct_answer_is_not_sent_back_for_correction |
| blurry, missing, answer only | test_unjudgeable_work_goes_to_staff_and_yields_no_evidence |
| hint then correction passes | test_hint_then_correction_is_linked_kept_and_never_independent |
| "done" without a valid correction | test_student_cannot_mark_resolved_without_a_checked_correction |
| still wrong, help, pause | test_help_and_pause_keep_the_case_unresolved_and_add_no_work |
| AI misjudgment or appeal | test_staff_replace_withdraws_evidence_hints_memories_and_corrects_reports (+ test_learning_reports) |
| school work unfinished, budget 0 | test_correction_and_help_never_wait_for_the_optional_gate |
| interrupted upload/check retried | test_correction_flow: idempotency, lease recovery, rollback tests |
"""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401  (pytest fixture reuse)
from test_learning import evidence_count, knowledge, learning, link, review, reviewed
from xueban_backend.learning_models import PracticeSession


def case_of(flow, case):
    return flow.client.get(f"/v1/cases/{case['id']}").json()


def request_hint(flow, case):
    source = case["current_decision"]
    response = flow.post(f"/v1/cases/{case['id']}/hints", {"expected_revision": case["revision"], "attempt_id": case["current_attempt_id"],
                                                           "source_decision_id": source["id"], "source_decision_revision": source["revision"],
                                                           "level": "direction"})
    assert response.status_code == 202, response.text
    assert flow.worker.run_once()
    job = flow.client.get(f"/v1/jobs/{response.json()['job']['id']}").json()
    assert job["status"] == "succeeded", job
    hint = flow.client.get(f"/v1/hints/{job['result']['resource_id']}").json()
    assert flow.post(f"/v1/hints/{hint['id']}/views", {"expected_revision": hint["revision"], "shown_at": "2026-10-17T01:00:00Z"}).status_code == 200
    return hint


def submit_correction(flow, case, verdict):
    asset = flow.uploaded()
    case = case_of(flow, case)
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "none_reported"})
    assert response.status_code == 202, response.text
    assert flow.worker.run_once()
    flow.pipeline.verdict = verdict
    flow.start_check(case_of(flow, case))
    assert flow.worker.run_once()
    return case_of(flow, case)


def test_correct_answer_is_not_sent_back_for_correction(flow):
    case = reviewed(flow, verdict="correct")
    assert case["current_decision"]["verdict"] == "correct" and case["progress"] == "pending"
    assert case["hint_ids"] == []
    teacher(flow)
    case = review(flow, case)
    assert case["progress"] == "verified_resolved"
    link(flow, case)
    learning(flow).drain()
    [state] = knowledge(flow)["states"]
    assert state["observed_error_count"] == 0 and state["learning_status"] == "no_judgment"


def test_unjudgeable_work_goes_to_staff_and_yields_no_evidence(flow):
    case = reviewed(flow, verdict="unable_to_judge")
    assert case["current_decision"]["validity"] == "needs_review" and case["progress"] != "verified_resolved"
    teacher(flow)
    [item] = flow.client.get("/v1/review-queue").json()["items"]
    assert item["case_id"] == case["id"] and item["reason"] == "unable_to_judge"
    link(flow, case)
    learning(flow).drain()
    assert evidence_count(flow) == 0


def test_hint_then_correction_is_linked_kept_and_never_independent(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    link(flow, case)
    hint = request_hint(flow, case)
    case = submit_correction(flow, case, "correct")
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["parent_attempt_id"] == case["root_attempt_id"] and attempt["help_condition"] == "hint_used"
    assert flow.client.get(f"/v1/hints/{hint['id']}").json()["validity"] == "stale"
    assert case["progress"] != "verified_resolved"
    independent = flow.client.post(f"/v1/attempts/{attempt['id']}/condition",
                                   json={"assistance_level": "independent", "expected_condition_id": None})
    assert independent.status_code == 409 and independent.json()["code"] == "help_recorded"
    case = review(flow, case)
    assert case["progress"] == "verified_resolved"
    learning(flow).drain()
    [state] = knowledge(flow)["states"]
    assert state["learning_status"] == "consolidating"
    assert state["reason_codes"] == ["corrected_with_help_awaiting_new_school_item"]
    assert state["independent_evidence_count"] == 0
    detail = flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").json()
    assert {(e["event_type"], e["assistance_level"]) for e in detail["evidence"]} == {
        ("school_first_attempt", "unknown"), ("correction", "direction")}


def test_student_cannot_mark_resolved_without_a_checked_correction(flow):
    case = reviewed(flow)
    assert case["progress"] == "needs_correction"
    marked = flow.post(f"/v1/cases/{case['id']}/progress", {"expected_revision": case["revision"], "action": "verified_resolved"})
    assert marked.status_code == 422
    paused = flow.post(f"/v1/cases/{case['id']}/progress", {"expected_revision": case["revision"], "action": "pause"})
    assert paused.status_code == 200
    resumed = flow.post(f"/v1/cases/{case['id']}/progress", {"expected_revision": paused.json()["revision"], "action": "resume"})
    assert resumed.status_code == 200 and resumed.json()["progress"] == "needs_correction"
    decision = case["current_decision"]
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    body = {"expected_revision": resumed.json()["revision"], "target_decision_id": decision["id"],
            "target_decision_revision": decision["revision"], "action": "replace", "verdict": "correct", "reason": "我觉得对了",
            "basis": [{"kind": "staff_observation", "summary": "自评", "source_ref": None, "regions": []}],
            "attempt_id": attempt["id"], "question_revision": case["current_question_revision"],
            "attempt_recognition_revision": attempt["recognition_revision"]}
    assert flow.post(f"/v1/cases/{case['id']}/reviews", body).status_code == 403
    assert case_of(flow, case)["progress"] == "needs_correction"


def test_help_and_pause_keep_the_case_unresolved_and_add_no_work(flow):
    case = reviewed(flow)
    case = submit_correction(flow, case, "incorrect")
    assert case["progress"] == "needs_correction"
    response = flow.post(f"/v1/cases/{case['id']}/help-requests", {"expected_revision": case["revision"], "attempt_id": case["current_attempt_id"],
                                                                   "kind": "question", "note": "订正后还是不对"})
    assert response.status_code == 201
    case = case_of(flow, case)
    paused = flow.post(f"/v1/cases/{case['id']}/progress", {"expected_revision": case["revision"], "action": "pause"}).json()
    assert paused["progress"] == "paused"
    teacher(flow)
    assert [h["id"] for h in flow.client.get("/v1/help-requests").json()["items"]] == [response.json()["id"]]
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeSession)) == 0
    assert flow.client.get("/v1/students/student_a/optional-activities").json()["gate"]["open"] is False


def test_staff_replace_withdraws_evidence_hints_memories_and_corrects_reports(flow):
    case = reviewed(flow)
    hint = request_hint(flow, case)
    teacher(flow)
    case = review(flow, case_of(flow, case))
    link(flow, case)
    learning(flow).drain()
    evidence_id = flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").json()["evidence"][0]["id"]
    memory = flow.client.post("/v1/students/student_a/memories", json={
        "category": "recurring_difficulty", "content": "异号相加符号易错", "knowledge_id": "kp-rational-add-sub",
        "evidence_ids": [evidence_id]}).json()
    case = review(flow, case_of(flow, case), action="replace", verdict="correct")
    assert case["progress"] == "verified_resolved"
    assert flow.client.get(f"/v1/hints/{hint['id']}").json()["validity"] == "stale"
    learning(flow).drain()
    assert evidence_count(flow) == 1 and evidence_count(flow, "invalidated") == 1
    items = {m["id"]: m for m in flow.client.get("/v1/students/student_a/memories?include_inactive=true").json()["items"]}
    assert items[memory["id"]]["status"] == "invalidated"
    [state] = knowledge(flow)["states"]
    assert state["observed_error_count"] == 0


def test_correction_and_help_never_wait_for_the_optional_gate(flow):
    case = flow.ready()
    gate = flow.client.get("/v1/students/student_a/optional-activities").json()["gate"]
    assert gate["open"] is False and "no_extra_budget" in gate["reasons"]
    flow.start_check(case)
    assert flow.worker.run_once()
    case = case_of(flow, case)
    assert case["current_decision"] is not None
    request_hint(flow, case)
    help_request = flow.post(f"/v1/cases/{case['id']}/help-requests", {"expected_revision": case_of(flow, case)["revision"],
                                                                       "attempt_id": case["current_attempt_id"], "kind": "question",
                                                                       "note": "请老师看一下"})
    assert help_request.status_code == 201
    started = flow.client.post("/v1/students/student_a/practice-sessions", json={"purpose": "preview", "willing": True})
    assert started.status_code == 409 and started.json()["code"] == "optional_gate_closed"
