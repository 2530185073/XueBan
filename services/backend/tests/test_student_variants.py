"""Student-owned generation, blind validation, stale evidence and optional answers."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_learning import reviewed
from test_practice import clock  # noqa: F401
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import (LearningOutbox, PracticeItem, PracticeResponse, PracticeSession,
                                           StudentKnowledgeState, VariantGeneration)
from xueban_backend.models import Homework
from xueban_backend.practice_generation import generate_student_variant
from xueban_backend.providers import ProviderError
from xueban_backend.providers.ark import Metadata


def candidate(kind="fill_blank"):
    choice = kind in ("single_choice", "multiple_choice")
    return {"question_type": kind, "question_kind": "choice" if choice else "fill_blank" if kind == "fill_blank" else "solution",
        "stem_text": "计算 3+4=____" if kind == "fill_blank" else "计算3+4，写出必要步骤" if not choice else "3+4等于几？",
        "options": [{"label": k, "text": v} for k, v in (("A", "6"), ("B", "7"), ("E", "8"))] if choice else [],
        "answer_fields": [{"id": "blank1", "label": "第1空"}] if kind == "fill_blank" else [],
        "answer_kind": "choice" if choice else "text",
        "answer_values": ["B"] if choice else [] if kind == "fill_blank" else ["7"],
        "field_answers": [{"field_id": "blank1", "answer_kind": "number", "answer_values": ["7"]}] if kind == "fill_blank" else [],
        "hint_text": "把两个数合起来。", "solution_text": "3+4=7。"}


def verification(c):
    return {**{k: deepcopy(c[k]) for k in ("answer_kind", "answer_values", "field_answers", "solution_text")},
            "self_contained": True, "same_type": True, "unique_answer": True, "reason": "已独立计算，条件齐全。"}


class Client:
    def __init__(self, outputs):
        self.settings = SimpleNamespace(enabled=True, api_key="test-private-token")
        self.outputs, self.calls = list(outputs), []

    def generate(self, **kw):
        self.calls.append(kw)
        value = self.outputs.pop(0)
        if callable(value):
            value = value()
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(data=deepcopy(value), metadata=Metadata("test-model", "test-model", None, None, None, 1, ()))


def source(flow, kind="fill_blank", blanks=1):
    case = reviewed(flow)
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "question_type": kind,
                             "blank_count": blanks if kind == "fill_blank" else None,
                             "options": [{"label": "A", "text": "1"}, {"label": "B", "text": "2"}] if "choice" in kind else []}
        session.commit()
    return case


def queue(flow, case, **body):
    response = flow.client.post("/v1/students/student_a/variant-generations", json={
        "willing": True, "source_case_id": case["id"], **body})
    assert response.status_code == 202, response.text
    return response.json()


def run(flow, task, client):
    with Session(flow.database.engine) as session, session.begin():
        generation = session.get(VariantGeneration, task["job_id"])
        job = session.get(LearningOutbox, generation.outbox_id)
        result = generate_student_variant(session, job, SimpleNamespace(client=client))
        job.status = "succeeded"
    return result


def get(flow, task):
    return flow.client.get(f"/v1/variant-generations/{task['job_id']}").json()


def start(flow, task):
    return flow.client.post("/v1/students/student_a/practice-sessions", json={
        "purpose": "variant", "willing": True, "generation_job_id": task["job_id"], "count": 1})


def solution_agreement():
    return {"equivalent": True, "answer_a_steps_valid": True, "answer_b_steps_valid": True,
            "reason": "两份必要步骤及结论都完整正确。"}


def ready(flow, kind="fill_blank"):
    case = source(flow, kind)
    task = queue(flow, case)
    c = candidate(kind)
    outputs = [c, verification(c)]
    if c["question_kind"] == "solution":
        outputs.append(solution_agreement())
    client = Client(outputs)
    assert run(flow, task, client)["status"] == "ready"
    return case, task, client


def test_generate_empty_bank_blind_verify_and_answer_does_not_create_mastery(flow, clock):
    case, task, client = ready(flow)
    assert len(client.calls) == 2
    blind = client.calls[1]["text"]
    assert "answer_values" not in blind and "solution_text" not in blind and "hint_text" not in blind
    assert "student_id" not in blind and "current_decision" not in blind
    status = get(flow, task)
    assert status["status"] == "ready" and status["knowledge_id"] is None
    begun = start(flow, task)
    assert begun.status_code == 201, begun.text
    activity = begun.json()
    item = activity["items"][0]
    assert item["knowledge_id"] is None and activity["knowledge_ids"] == []
    assert "answer_values" not in item and "solution_text" not in item
    assert item["verification"]["source"] == "ai" and item["verification"]["heterogeneous_models"] is False
    answer = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": item["id"], "answers": [{"field_id": "blank1", "answer_text": "14/2"}]})
    assert answer.status_code == 201, answer.text
    assert answer.json()["result"] == "correct" and answer.json()["session_status"] == "completed"
    assert start(flow, task).json()["id"] == activity["id"]
    assert get(flow, task)["session_id"] == activity["id"]
    assert queue(flow, case)["job_id"] == task["job_id"]
    with Session(flow.database.engine) as session:
        record = session.get(PracticeItem, item["id"])
        assert record.status == "ai_verified" and record.reviewed_by is None and record.reviewed_at is None
        assert record.owner_student_id == "student_a"
        assert session.scalar(select(func.count()).select_from(StudentKnowledgeState)) == 0
        assert session.scalar(select(func.count()).select_from(PracticeSession)) == 1


def test_multi_choice_uses_dynamic_options_and_exact_set(flow):
    _, task, _ = ready(flow, "multiple_choice")
    with Session(flow.database.engine) as session:
        item = session.get(PracticeItem, get(flow, task)["item_id"])
        item.answer_values = ["B", "E"]
        session.commit()
    activity = start(flow, task).json()
    url = f"/v1/practice-sessions/{activity['id']}/responses"
    item_id = activity["items"][0]["id"]
    invalid = flow.client.post(url, json={"item_id": item_id, "selected_options": ["Z"]})
    assert invalid.status_code == 422
    answer = flow.client.post(url, json={"item_id": item_id, "selected_options": ["E", "B"]})
    assert answer.json()["result"] == "correct"


def run_assessment(flow, assessment_id, client):
    from xueban_backend.learning_models import PracticeAssessment
    from xueban_backend.practice_generation import assess_student_practice
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(PracticeAssessment, assessment_id)
        job = session.get(LearningOutbox, row.outbox_id)
        outcome = assess_student_practice(session, job, SimpleNamespace(client=client))
        job.status = "succeeded"
        return outcome


def test_service_failure_does_not_record_wrong_answer_or_expose_solution_and_retry_works(flow, monkeypatch):
    # Production API environment intentionally has no model credentials.
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    _, task, _ = ready(flow, "calculation")
    activity = start(flow, task).json()
    url = f"/v1/practice-sessions/{activity['id']}/responses"
    body = {"item_id": activity["items"][0]["id"], "answer_text": "先3+3=6，再加1=7。"}
    submitted = flow.client.post(url, json=body)
    assert submitted.status_code == 202 and submitted.json()["result"] == "pending"
    assessment_id = submitted.json()["assessment_id"]
    assert flow.client.post(url, json=body).json()["assessment_id"] == assessment_id
    restored = flow.client.get(f"/v1/practice-sessions/{activity['id']}").json()
    assert restored["items"][0]["assessment"]["assessment_id"] == assessment_id
    assert "response" not in restored["items"][0]
    assert run_assessment(flow, assessment_id, Client([ProviderError("upstream_unavailable")]))["status"] == "failed"
    failed = flow.client.get(f"/v1/practice-assessments/{assessment_id}").json()
    assert failed["retryable"] is True and failed["result"] == "unscorable" and failed["solution_text"] is None
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeResponse)) == 0
        assert session.get(PracticeSession, activity["id"]).status == "active"
    retried = flow.client.post(url, json=body)
    next_id = retried.json()["assessment_id"]
    assert next_id != assessment_id
    assert run_assessment(flow, next_id, Client([{"result": "correct", "feedback": "这个等价拆分方法正确。"}]))["status"] == "completed"
    restored = flow.client.get(f"/v1/practice-sessions/{activity['id']}").json()
    assert restored["items"][0]["response"]["answer_text"] == body["answer_text"]
    assert restored["items"][0]["response"]["result"] == "correct"


@pytest.mark.parametrize("change", ["question", "attempt", "decision", "curriculum"])
def test_changed_source_before_generation_costs_no_call(flow, change):
    case = source(flow)
    task = queue(flow, case)
    with Session(flow.database.engine) as session:
        if change == "question":
            row = session.get(Entity, case["question_id"])
            row.revision += 1
        elif change == "attempt":
            row = session.get(Entity, case["current_attempt_id"])
            row.document = {**row.document, "answer_text": "new answer"}
        elif change == "decision":
            row = session.get(Entity, case["id"])
            row.document = {**row.document, "current_decision": {**row.document["current_decision"], "verdict": "correct"}}
        else:
            row = session.get(Homework, flow.homework_id)
            row.curriculum = {**row.curriculum, "publisher_code": "PEP"}
        session.commit()
    client = Client([])
    assert run(flow, task, client)["status"] == "superseded"
    assert client.calls == [] and start(flow, task).status_code == 409


def test_source_changed_during_verification_cannot_publish(flow):
    case = source(flow)
    task = queue(flow, case)
    c = candidate()
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(VariantGeneration, task["job_id"])
        job = session.get(LearningOutbox, row.outbox_id)
        def change():
            q = session.get(Entity, case["question_id"])
            q.document = {**q.document, "stem_text": "已替换的新题"}
            session.flush()
            return verification(c)
        client = Client([c, change])
        assert generate_student_variant(session, job, SimpleNamespace(client=client))["status"] == "superseded"
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


def test_ready_source_changed_cannot_start_and_list_is_superseded(flow):
    case, task, _ = ready(flow)
    with Session(flow.database.engine) as session:
        q = session.get(Entity, case["question_id"])
        q.revision += 1
        session.commit()
    assert get(flow, task)["status"] == "superseded"
    assert start(flow, task).status_code == 409


def test_verification_failure_has_one_bounded_regeneration_and_explicit_retry_only(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    case = source(flow)
    task = queue(flow, case)
    c = candidate()
    wrong = verification(c)
    wrong["field_answers"][0]["answer_values"] = ["8"]
    client = Client([c, wrong, c, wrong])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 4 and get(flow, task)["status"] == "failed"
    flow.client.get("/v1/students/student_a/variant-generations")
    assert len(client.calls) == 4
    with Session(flow.database.engine) as session:
        previous = session.get(VariantGeneration, task["job_id"]).outbox_id
    retried = queue(flow, case)
    assert retried["job_id"] == task["job_id"] and retried["status"] == "queued"
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        assert row.outbox_id != previous and len(row.audit["executions"]) == 1


def test_text_reference_equivalence_is_explicit_non_blind_third_stage(flow):
    case = source(flow, "calculation")
    task = queue(flow, case)
    c = candidate("calculation")
    v = verification(c)
    v["answer_values"] = ["七"]
    client = Client([c, v, {**solution_agreement(), "reason": "两份完整正确解法中的7和七表示同一数值。"}])
    assert run(flow, task, client)["status"] == "ready"
    assert "answer_a" not in client.calls[1]["text"] and "answer_a" in client.calls[2]["text"]


def test_pending_and_wrong_student_cannot_generate(flow):
    case = source(flow)
    queue(flow, case)
    assert flow.client.post("/v1/students/student_ungranted/variant-generations", json={
        "willing": True, "source_case_id": case["id"]}).status_code == 404
    with Session(flow.database.engine) as session:
        row = session.get(Entity, case["id"])
        row.document = {**row.document, "current_decision": {**row.document["current_decision"], "validity": "needs_review"}}
        session.commit()
    response = flow.client.post("/v1/students/student_a/variant-generations", json={"willing": True, "source_case_id": case["id"]})
    assert response.status_code == 409 and response.json()["code"] == "source_not_eligible"


def test_credential_echo_never_publish_or_persist(flow):
    case = source(flow)
    task = queue(flow, case)
    c = candidate()
    c["solution_text"] = "test-private-token"
    client = Client([c])
    assert run(flow, task, client)["status"] == "failed"
    with Session(flow.database.engine) as session:
        assert "test-private-token" not in str(session.get(VariantGeneration, task["job_id"]).audit)
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


def test_multiblank_missing_rejected_and_one_numeric_error_cannot_be_overridden(flow):
    case = source(flow, blanks=2)
    task = queue(flow, case)
    c = candidate()
    c["stem_text"] = "计算3+4=____，写出所用运算名称____。"
    c["answer_fields"].append({"id": "blank2", "label": "第2空"})
    c["field_answers"].append({"field_id": "blank2", "answer_kind": "text", "answer_values": ["加法"]})
    assert run(flow, task, Client([c, verification(c)]))["status"] == "ready"
    activity = start(flow, task).json()
    url = f"/v1/practice-sessions/{activity['id']}/responses"
    item_id = activity["items"][0]["id"]
    assert flow.client.post(url, json={"item_id": item_id, "answers": [{"field_id": "blank1", "answer_text": "7"}]}).status_code == 422
    client = Client([])
    flow.client.app.state.variant_answer_client = client
    result = flow.client.post(url, json={"item_id": item_id, "answers": [
        {"field_id": "blank1", "answer_text": "8"}, {"field_id": "blank2", "answer_text": "相加"}]}).json()
    assert result["result"] == "incorrect" and client.calls == []


def test_foreign_tenant_and_ungranted_student_cannot_read_start_or_list(flow):
    _, task, _ = ready(flow)
    headers = {"Authorization": "Bearer " + flow.signing.token("subject-b")}
    assert flow.client.get(f"/v1/variant-generations/{task['job_id']}", headers=headers).status_code == 404
    assert flow.client.get("/v1/students/student_a/variant-generations", headers=headers).status_code == 404
    assert flow.client.post("/v1/students/student_b/practice-sessions", json={
        "purpose": "variant", "willing": True, "generation_job_id": task["job_id"]}, headers=headers).status_code == 403
    with Session(flow.database.engine) as session:
        generation = session.get(VariantGeneration, task["job_id"])
        generation.student_id = "student_ungranted"
        session.commit()
    assert flow.client.get(f"/v1/variant-generations/{task['job_id']}").status_code == 404


def test_purge_removes_generated_items_and_private_call_audit(flow):
    from xueban_backend.practice import purge_practice
    _, task, _ = ready(flow)
    start(flow, task)
    with Session(flow.database.engine) as session, session.begin():
        purge_practice(session, "tenant_a", "student_a")
        for model in (VariantGeneration, PracticeItem, PracticeSession, PracticeResponse):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_real_outbox_worker_does_not_automatically_retry_paid_provider_failure(flow):
    from xueban_backend.learning_projection import LearningWorker
    case = source(flow)
    task = queue(flow, case)
    client = Client([ProviderError("upstream_unavailable")])
    worker = LearningWorker(flow.database.engine, client=client)
    worker.scan = lambda *a, **kw: None
    assert worker.run_once()
    assert get(flow, task)["status"] == "failed"
    assert worker.run_once() is False
    assert len(client.calls) == 1
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        job = session.get(LearningOutbox, row.outbox_id)
        assert job.status == "succeeded" and job.tries == 1


def test_interrupted_execution_needs_explicit_retry_without_another_paid_call(flow):
    case = source(flow)
    task = queue(flow, case)
    client = Client([])
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(VariantGeneration, task["job_id"])
        job = session.get(LearningOutbox, row.outbox_id)
        job.tries = 2
        assert generate_student_variant(session, job, SimpleNamespace(client=client))["status"] == "failed"
        assert row.audit["interrupted_execution"] is True
    assert client.calls == []


def test_invalid_blind_reference_cannot_be_saved_even_with_matching_generated_answer(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    case = source(flow, "single_choice")
    task = queue(flow, case)
    c = candidate("single_choice")
    v = verification(c)
    v["field_answers"] = [{"field_id": "invented", "answer_kind": "text", "answer_values": ["7"]}]
    assert run(flow, task, Client([c, v]))["failure_code"] == "verification_failed"
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


def test_assessment_stale_exit_and_changed_item_never_write_a_response(flow):
    from xueban_backend.learning_models import PracticeAssessment
    from xueban_backend.practice_generation import assess_student_practice
    _, task, _ = ready(flow, "calculation")
    activity = start(flow, task).json()
    url = f"/v1/practice-sessions/{activity['id']}/responses"
    body = {"item_id": activity["items"][0]["id"], "answer_text": "3+4=7"}
    aid = flow.client.post(url, json=body).json()["assessment_id"]
    assert flow.client.post(url, json={**body, "answer_text": "3+4=8"}).json()["code"] == "assessment_pending"
    with Session(flow.database.engine) as session, session.begin():
        assessment = session.get(PracticeAssessment, aid)
        job = session.get(LearningOutbox, assessment.outbox_id)
        def change():
            item = session.get(PracticeItem, body["item_id"])
            item.stem_text = "已经是另一个题目"
            session.flush()
            return {"result": "correct", "feedback": "旧题答案正确"}
        result = assess_student_practice(session, job, SimpleNamespace(client=Client([change])))
        assert result["status"] == "superseded"
        assert session.scalar(select(func.count()).select_from(PracticeResponse)) == 0
    # Another explicit submission can be queued, but leaving the activity invalidates it before any paid call.
    aid2 = flow.client.post(url, json=body).json()["assessment_id"]
    flow.client.post(f"/v1/practice-sessions/{activity['id']}/exit", json={"reason": "not_now"})
    client = Client([])
    assert run_assessment(flow, aid2, client)["status"] == "superseded" and client.calls == []


def test_assessment_privacy_and_failed_attempt_charge_audit(flow):
    from xueban_backend.learning_models import PracticeAssessment
    from xueban_backend.providers.ark import Attempt
    _, task, _ = ready(flow, "calculation")
    activity = start(flow, task).json()
    aid = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": activity["items"][0]["id"], "answer_text": "3+4=7"}).json()["assessment_id"]
    headers = {"Authorization": "Bearer " + flow.signing.token("subject-b")}
    assert flow.client.get(f"/v1/practice-assessments/{aid}", headers=headers).status_code == 404
    error = ProviderError("deadline_exceeded", latency_ms=35000,
                          attempts=(Attempt(1, 200, "deadline_exceeded", 35000, request_id="safe-request"),))
    assert run_assessment(flow, aid, Client([error]))["status"] == "failed"
    with Session(flow.database.engine) as session:
        row = session.get(PracticeAssessment, aid)
        attempt = row.audit["calls"][0]["attempts"][0]
        assert attempt["request_id"] == "safe-request" and attempt["charge_status"] == "unknown"


def test_actual_worker_completes_generation_then_semantic_assessment_without_api_key(flow, monkeypatch):
    from xueban_backend.learning_projection import LearningWorker
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    case = source(flow, "calculation")
    task = queue(flow, case)
    c = candidate("calculation")
    client = Client([c, verification(c), solution_agreement(), {"result": "correct", "feedback": "等价运算正确。"}])
    worker = LearningWorker(flow.database.engine, client=client)
    worker.scan = lambda *a, **kw: None
    assert worker.run_once() and get(flow, task)["status"] == "ready"
    activity = start(flow, task).json()
    body = {"item_id": activity["items"][0]["id"], "answer_text": "3+4=(3+3)+1=7"}
    posted = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json=body)
    assert posted.status_code == 202
    aid = posted.json()["assessment_id"]
    pending = flow.client.get(f"/v1/practice-assessments/{aid}").json()
    assert pending["submission"]["answer_text"] == body["answer_text"] and pending["solution_text"] is None
    assert worker.run_once()
    graded = flow.client.get(f"/v1/practice-assessments/{aid}").json()
    assert graded["status"] == "completed" and graded["result"] == "correct"
    assert "audit" not in graded and "verification_calls" not in graded
    assert len(client.calls) == 4 and worker.run_once() is False


def test_unlinked_knowledge_cannot_be_claimed_and_no_same_type_guess_without_structure(flow):
    case = source(flow)
    rejected = flow.client.post("/v1/students/student_a/variant-generations", json={
        "willing": True, "source_case_id": case["id"], "knowledge_id": "kp-rational-add-sub"})
    assert rejected.status_code == 409 and rejected.json()["code"] == "source_not_eligible"
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "question_type": "unknown", "blank_count": None, "options": []}
        session.commit()
    rejected = flow.client.post("/v1/students/student_a/variant-generations", json={"willing": True, "source_case_id": case["id"]})
    assert rejected.status_code == 409 and rejected.json()["code"] == "unsupported_source_type"


def test_book_selection_changed_after_queue_never_calls_model(flow):
    from test_curriculum_binding import choose, publish_book
    from test_correction_flow import teacher
    case = source(flow)
    task = queue(flow, case)
    publish_book(flow)
    teacher(flow)
    choose(flow, "test-pep-book-v1")
    client = Client([])
    assert run(flow, task, client)["status"] == "superseded" and client.calls == []
