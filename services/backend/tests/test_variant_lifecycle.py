"""Notebook resume/regeneration/retry with isolated DBs and injected models only."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_practice import clock, new_item, open_window, start as start_optional  # noqa: F401
from test_student_variants import Client, candidate, get, queue, ready, run, source, start, verification
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import (LearningOutbox, PracticeItem, PracticeResponse, PracticeSession,
                                           StudentKnowledgeState, VariantGeneration)
from xueban_backend.learning_rules import model_decision_usable
from xueban_backend.models import StudentGrant
from xueban_backend.practice_generation import _valid_candidate, snapshot_current


def another_candidate():
    value = candidate()
    value.update(stem_text="计算 8+6=____", solution_text="8+6=14。")
    value["field_answers"][0]["answer_values"] = ["14"]
    return value


def submit(flow, activity, answer="8"):
    return flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": activity["items"][0]["id"], "answers": [{"field_id": "blank1", "answer_text": answer}]})


def retry(flow, activity, key="notebook-retry-1"):
    return flow.client.post(f"/v1/practice-sessions/{activity['id']}/retry", json={},
                            headers={"Idempotency-Key": key} if key else {})


def test_regeneration_tokens_resume_latest_and_keep_evidence_binding(flow, clock):
    case, first, _ = ready(flow)
    original = start(flow, first).json()
    second = queue(flow, case, regenerate_token="new-question-1")
    assert second["job_id"] != first["job_id"]
    assert queue(flow, case, regenerate_token="new-question-1")["job_id"] == second["job_id"]
    assert queue(flow, case)["job_id"] == second["job_id"]
    value = another_candidate()
    client = Client([value, verification(value)])
    assert run(flow, second, client)["status"] == "ready"
    assert len(client.calls) == 2
    assert candidate()["stem_text"] in client.calls[0]["text"]
    assert "answer_values" not in client.calls[1]["text"] and "solution_text" not in client.calls[1]["text"]
    current = start(flow, second).json()
    assert current["id"] != original["id"] and current["no_expiry"] is True
    assert start(flow, first).json()["id"] == original["id"]
    assert queue(flow, case)["job_id"] == second["job_id"]
    with Session(flow.database.engine) as session:
        rows = list(session.scalars(select(VariantGeneration)))
        assert len(rows) == 2 and len({r.source_fingerprint for r in rows}) == 2
        assert all(snapshot_current(session, r) for r in rows)
        assert session.scalar(select(func.count()).select_from(LearningOutbox).where(
            LearningOutbox.kind == "generate_student_variant")) == 2
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "stem_text": "计算 9+8=____"}
        session.commit()
    assert get(flow, second)["status"] == "superseded"
    changed = queue(flow, case, regenerate_token="new-question-1")
    assert changed["job_id"] not in (first["job_id"], second["job_id"])


def test_repeated_generated_question_rejected_and_default_keeps_valid_item(flow, monkeypatch):
    case, first, _ = ready(flow)
    task = queue(flow, case, regenerate_token="repeat-attempt")
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    client = Client([candidate(), candidate()])
    assert run(flow, task, client)["status"] == "failed"
    assert len(client.calls) == 2  # Duplicate candidates never reach blind solving.
    assert queue(flow, case)["job_id"] == first["job_id"]
    assert queue(flow, case, regenerate_token="repeat-attempt")["job_id"] == task["job_id"]
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 1


def test_default_resume_skips_retired_items_and_never_revives_an_invalid_generation(flow):
    case, first, _ = ready(flow)
    second = queue(flow, case, regenerate_token="retired-example")
    value = another_candidate()
    assert run(flow, second, Client([value, verification(value)]))["status"] == "ready"
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, second["job_id"])
        session.get(PracticeItem, row.item_id).status = "retired"
        session.commit()
    assert queue(flow, case)["job_id"] == first["job_id"]
    with Session(flow.database.engine) as session:
        session.get(VariantGeneration, first["job_id"]).status = "superseded"
        session.commit()
    response = flow.client.post("/v1/students/student_a/variant-generations", json={
        "willing": True, "source_case_id": case["id"]})
    assert response.status_code == 409 and response.json()["code"] == "stale_source"


@pytest.mark.parametrize("token", ["", "   ", "x" * 129, 123])
def test_invalid_regeneration_token_rejected(flow, token):
    case = source(flow)
    result = flow.client.post("/v1/students/student_a/variant-generations", json={
        "willing": True, "source_case_id": case["id"], "regenerate_token": token})
    assert result.status_code == 422


def test_new_variant_sessions_do_not_expire_or_block_each_other(flow, clock):
    case, first, _ = ready(flow)
    original = start(flow, first).json()
    assert original["no_expiry"] is True and "gate" not in original
    second = queue(flow, case, regenerate_token="another-one")
    value = another_candidate()
    assert run(flow, second, Client([value, verification(value)]))["status"] == "ready"
    other = start(flow, second)
    assert other.status_code == 201 and other.json()["id"] != original["id"]
    clock["t"] += timedelta(days=30)
    assert flow.client.get(f"/v1/practice-sessions/{original['id']}").json()["status"] == "active"
    assert start(flow, first).json()["id"] == original["id"]
    listed = flow.client.get("/v1/students/student_a/practice-sessions").json()["items"]
    assert all(item["status"] == "active" and item["no_expiry"] for item in listed)
    assert submit(flow, original, "7").json()["result"] == "correct"


@pytest.mark.parametrize("status", ["active", "time_up"])
@pytest.mark.parametrize("entry", ["start", "get", "list", "answer", "hint"])
def test_legacy_unanswered_generated_session_is_safely_resumed(flow, clock, status, entry):
    _, task, _ = ready(flow)
    activity = start(flow, task).json()
    with Session(flow.database.engine) as session:
        row = session.get(PracticeSession, activity["id"])
        row.gate = {k: v for k, v in row.gate.items() if k != "no_expiry"}
        row.budget_ends_at = clock["t"] - timedelta(minutes=1)
        row.status = status
        row.ended_at = row.budget_ends_at if status == "time_up" else None
        session.commit()
    if entry == "start":
        response = start(flow, task)
    elif entry == "get":
        response = flow.client.get(f"/v1/practice-sessions/{activity['id']}")
    elif entry == "list":
        response = flow.client.get("/v1/students/student_a/practice-sessions")
    elif entry == "answer":
        response = submit(flow, activity, "7")
    else:
        response = flow.client.post(f"/v1/practice-sessions/{activity['id']}/items/{activity['items'][0]['id']}/hint")
    assert response.status_code in (200, 201), response.text
    with Session(flow.database.engine) as session:
        row = session.get(PracticeSession, activity["id"])
        assert row.gate["no_expiry"] is True
        assert row.status == ("completed" if entry == "answer" else "active")
        if entry != "answer":
            assert row.ended_at is None
        assert session.scalar(select(func.count()).select_from(PracticeSession)) == 1


@pytest.mark.parametrize("invalidation", ["source", "item", "response", "exited", "binding"])
def test_legacy_invalidated_or_answered_sessions_are_not_resurrected(flow, clock, invalidation):
    case, task, _ = ready(flow)
    activity = start(flow, task).json()
    if invalidation == "response":
        assert submit(flow, activity).status_code == 201
    with Session(flow.database.engine) as session:
        row = session.get(PracticeSession, activity["id"])
        row.gate = {k: v for k, v in row.gate.items() if k != "no_expiry"}
        row.budget_ends_at = clock["t"] - timedelta(minutes=1)
        row.status, row.ended_at = "time_up", row.budget_ends_at
        if invalidation == "source":
            session.get(Entity, case["question_id"]).revision += 1
        elif invalidation == "item":
            session.get(PracticeItem, row.item_ids[0]).status = "retired"
        elif invalidation == "exited":
            row.status, row.exit_reason = "exited", "not_now"
        elif invalidation == "binding":
            row.source_case_id = "other-case"
        session.commit()
    restored = flow.client.get(f"/v1/practice-sessions/{activity['id']}").json()
    assert restored["no_expiry"] is False
    assert restored["status"] == ("exited" if invalidation == "exited" else "time_up")


def test_legacy_upgrade_rechecks_completion_after_lock(flow, clock, monkeypatch):
    from xueban_backend import practice_generation
    _, task, _ = ready(flow)
    activity = start(flow, task).json()
    original_lock = practice_generation.lock_source

    def completed_while_waiting(session, generation):
        original_lock(session, generation)
        row = session.get(PracticeSession, activity["id"])
        row.status, row.ended_at = "completed", clock["t"]
        session.flush()

    monkeypatch.setattr(practice_generation, "lock_source", completed_while_waiting)
    with Session(flow.database.engine) as session:
        row = session.get(PracticeSession, activity["id"])
        row.gate = {k: v for k, v in row.gate.items() if k != "no_expiry"}
        row.status, row.ended_at = "time_up", row.budget_ends_at
        session.flush()
        practice_generation.upgrade_generated_session(session, row)
        assert row.status == "completed" and "no_expiry" not in row.gate


@pytest.mark.parametrize("field,value", [("source_case_id", "another-case"), ("knowledge_id", "kp-rational-add-sub")])
def test_resume_validates_requested_source_and_knowledge(flow, field, value):
    _, task, _ = ready(flow)
    start(flow, task)
    response = flow.client.post("/v1/students/student_a/practice-sessions", json={
        "purpose": "variant", "willing": True, "generation_job_id": task["job_id"], field: value})
    assert response.status_code == 409 and response.json()["code"] == "stale_source"


@pytest.mark.parametrize("result", ["incorrect", "unscorable"])
def test_retry_preserves_original_response_and_is_idempotent(flow, clock, result):
    _, task, _ = ready(flow)
    activity = start(flow, task).json()
    assert submit(flow, activity).json()["result"] == "incorrect"
    with Session(flow.database.engine) as session:
        response = session.scalar(select(PracticeResponse).where(PracticeResponse.session_id == activity["id"]))
        response.result = result
        original_id, original_text = response.id, response.answer_text
        session.commit()
    retried = retry(flow, activity)
    assert retried.status_code == 201, retried.text
    current = retried.json()
    assert current["id"] != activity["id"] and current["status"] == "active" and current["no_expiry"] is True
    assert current["items"][0]["id"] == activity["items"][0]["id"] and "response" not in current["items"][0]
    assert retry(flow, activity).json() == current
    assert retry(flow, activity, "another-retry-key").json()["id"] == current["id"]
    assert retry(flow, activity, None).json()["id"] == current["id"]
    assert get(flow, task)["session_id"] == current["id"]
    assert start(flow, task).json()["id"] == current["id"]
    assert submit(flow, current, "7").json()["result"] == "correct"
    assert retry(flow, current).status_code == 409
    with Session(flow.database.engine) as session:
        original = session.get(PracticeResponse, original_id)
        assert (original.answer_text, original.result) == (original_text, result)
        assert session.scalar(select(func.count()).select_from(PracticeResponse)) == 2
        assert session.scalar(select(func.count()).select_from(PracticeSession)) == 2
        assert session.get(PracticeSession, activity["id"]).status == "completed"


def test_retry_requires_completion_and_valid_source(flow):
    case, task, _ = ready(flow)
    activity = start(flow, task).json()
    assert retry(flow, activity).status_code == 409
    assert submit(flow, activity).status_code == 201
    with Session(flow.database.engine) as session:
        session.get(Entity, case["question_id"]).revision += 1
        session.commit()
    denied = retry(flow, activity)
    assert denied.status_code == 409 and denied.json()["code"] == "stale_source"


def test_retry_and_generation_enforce_tenant_and_student_ownership(flow):
    case, task, _ = ready(flow)
    activity = start(flow, task).json()
    assert submit(flow, activity).status_code == 201
    other_tenant = {"Authorization": "Bearer " + flow.signing.token(subject="subject-b")}
    assert flow.client.post(f"/v1/practice-sessions/{activity['id']}/retry", json={}, headers=other_tenant).status_code == 404
    assert flow.client.get(f"/v1/variant-generations/{task['job_id']}", headers=other_tenant).status_code == 404
    assert flow.client.post("/v1/students/student_ungranted/variant-generations", json={
        "willing": True, "source_case_id": case["id"]}).status_code == 404
    assert retry(flow, activity).status_code == 201
    with Session(flow.database.engine) as session:
        grant = session.scalar(select(StudentGrant).where(StudentGrant.principal_id == "actor_a",
                                                        StudentGrant.student_id == "student_a"))
        grant.active = False
        session.commit()
    assert retry(flow, activity).status_code == 404  # Even a stored idempotent response requires access.


def test_uncertainty_flags_alone_allow_usable_wrong_verdict(flow):
    case = source(flow)
    with Session(flow.database.engine) as session:
        for identifier in (case["question_id"], case["current_attempt_id"]):
            row = session.get(Entity, identifier)
            row.document = {**row.document, "recognition_uncertainties": ["非关键文字备注"]}
        session.commit()
    assert queue(flow, case)["status"] == "queued"


def test_conflict_marked_wrong_can_generate_without_promoting_original_verdict(flow):
    case = source(flow)
    with Session(flow.database.engine) as session:
        row = session.get(Entity, case["id"])
        decision = {**row.document["current_decision"], "reason_code": "verification_conflict",
                    "uncertainties": ["首轮与独立复核结论不一致，需要核对。"]}
        assert decision["validity"] == "candidate" and decision["verdict"] == "incorrect"
        assert model_decision_usable(decision) is False
        session.get(Entity, decision["id"]).document = decision
        row.document = {**row.document, "current_decision": decision}
        session.commit()
    task = queue(flow, case)
    value = candidate()
    client = Client([value, verification(value)])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 2 and "answer_values" not in client.calls[1]["text"]
    activity = start(flow, task).json()
    assert submit(flow, activity, "7").json()["result"] == "correct"
    with Session(flow.database.engine) as session:
        assert session.get(Entity, decision["id"]).document == decision
        assert session.get(Entity, case["id"]).document["current_decision"] == decision
        assert model_decision_usable(session.get(Entity, decision["id"]).document) is False
        assert session.scalar(select(func.count()).select_from(StudentKnowledgeState)) == 0


@pytest.mark.parametrize("change", ["unclear_answer", "not_ready", "correct", "blank_stem"])
def test_uncertainty_relaxation_keeps_real_eligibility_checks(flow, change):
    case = source(flow)
    with Session(flow.database.engine) as session:
        attempt = session.get(Entity, case["current_attempt_id"])
        if change == "unclear_answer":
            attempt.document = {**attempt.document, "answer_status": "unclear"}
        elif change == "not_ready":
            attempt.document = {**attempt.document, "recognition_status": "needs_revision"}
        elif change == "blank_stem":
            question = session.get(Entity, case["question_id"])
            question.document = {**question.document, "stem_text": "   "}
        else:
            row = session.get(Entity, case["id"])
            decision = {**row.document["current_decision"], "verdict": "correct"}
            session.get(Entity, decision["id"]).document = decision
            row.document = {**row.document, "current_decision": decision}
        session.commit()
    response = flow.client.post("/v1/students/student_a/variant-generations", json={
        "willing": True, "source_case_id": case["id"]})
    assert response.status_code == 409 and response.json()["code"] == "source_not_eligible"


def test_preview_keeps_budget_and_single_activity_limit_with_notebook_open(flow, clock):
    _, task, _ = ready(flow)
    notebook = start(flow, task).json()
    teacher(flow)
    flow.client.put("/v1/students/student_a/learning-profile", json={
        "expected_revision": 0, "current_knowledge_ids": ["kp-linear-eq-brackets"]})
    new_item(flow, knowledge_id="kp-linear-eq-brackets")
    open_window(flow, confirm=True)
    preview = start_optional(flow, purpose="preview", with_quiz=True)
    assert preview.status_code == 201, preview.text
    assert preview.json()["no_expiry"] is False
    blocked = start_optional(flow, purpose="preview")
    assert blocked.status_code == 409 and blocked.json()["code"] == "practice_active"
    # A stray flag must never exempt preview sessions from their time budget.
    with Session(flow.database.engine) as session:
        row = session.get(PracticeSession, preview.json()["id"])
        row.gate = {**row.gate, "no_expiry": True}
        session.commit()
    clock["t"] += timedelta(minutes=11)
    assert flow.client.get(f"/v1/practice-sessions/{preview.json()['id']}").json()["status"] == "time_up"
    assert flow.client.get(f"/v1/practice-sessions/{notebook['id']}").json()["status"] == "active"
    assert retry(flow, preview.json()).status_code == 409


def test_distinctness_rejects_number_swap_word_problems_but_allows_arithmetic():
    value = candidate("application")
    value["stem_text"] = "小明买了8本练习册，每本6元，共花多少元？"
    snapshot = {"question_type": "application", "question_kind": "solution", "source_question": {
        "stem_text": "小明买了3本练习册，每本4元，共花多少元？", "options": []}}
    assert not _valid_candidate(value, snapshot)
    value["stem_text"] = "一个长方形花园长8米，宽6米，求面积。"
    assert _valid_candidate(value, snapshot)
    arithmetic = candidate("calculation")
    snapshot = {"question_type": "calculation", "question_kind": "solution", "source_question": {
        "stem_text": "计算8+6，写出必要步骤", "options": []}}
    assert _valid_candidate(arithmetic, snapshot)
    choice = candidate("single_choice")
    snapshot = {"question_type": "single_choice", "question_kind": "choice", "source_question": {
        "stem_text": "6+1等于几？", "options": deepcopy(choice["options"])}}
    assert not _valid_candidate(choice, snapshot)
