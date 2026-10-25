"""Saved-reference reveal over authenticated HTTP; all data/inference is synthetic."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_practice_photo import Client, assess, image_payload, readback
from xueban_backend.app import create_app
from xueban_backend.learning_models import (
    LearningEvidence, LearningOutbox, LearningProjection, PracticeAssessment,
    PracticeItem, PracticeResponse, PracticeSession, StudentKnowledgeState, VariantGeneration,
)
from xueban_backend.models import Student, StudentGrant
from xueban_backend.practice import now
from xueban_backend.practice_generation import assess_student_practice
from xueban_backend.providers.ark import ArkClient
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.responses import ResponsesClient
from xueban_backend.providers.user_original.ark import ArkClient as UserOriginalArkClient


ITEM_ID = "item_solution"
SESSION_ID = "practice_solution"
SESSION_URL = f"/v1/practice-sessions/{SESSION_ID}"
URL = f"{SESSION_URL}/items/{ITEM_ID}/solution"
STEPS = "先算绝对值：24 ÷ 6 = 4。\n异号相除得负，所以 (-24) ÷ 6 = -4。"


@pytest.fixture(autouse=True)
def no_live_models(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Saved solutions must never call a live model")
    for provider in (ArkClient, BoundedChatClient, ResponsesClient, UserOriginalArkClient):
        monkeypatch.setattr(provider, "generate", forbidden)


@pytest.fixture
def solution(settings, database, signing, tmp_path):
    cfg = replace(settings, asset_storage_dir=tmp_path / "solution-images")
    app = create_app(cfg)
    stamp = now()
    with Session(database.engine) as session, session.begin():
        session.add(PracticeItem(id=ITEM_ID, tenant_id="tenant_a", owner_student_id="student_a",
            item_family_id="family_solution", stem_text="计算 (-24) ÷ 6，写出步骤。",
            question_type="calculation", question_kind="solution", options=[], answer_fields=[], field_answers=[],
            answer_kind="number", answer_values=["-4", "-4.0"], hint_text="先判断符号。", solution_text=STEPS,
            verification={"source": "ai", "status": "verified"}, source="model", status="ai_verified",
            difficulty=1, revision=1, created_at=stamp, updated_at=stamp))
        session.add(PracticeSession(id=SESSION_ID, tenant_id="tenant_a", student_id="student_a", purpose="variant",
            knowledge_ids=[], item_ids=[ITEM_ID], hints_used=[], status="active",
            gate={"generation_job_id": "synthetic_generation", "no_expiry": True,
                  "child_willing": {"value": True, "source": "student", "actor": "actor_a"}},
            started_at=stamp, budget_ends_at=stamp + timedelta(minutes=10)))
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + signing.token()
        yield SimpleNamespace(client=client, database=database, signing=signing, cfg=cfg, stamp=stamp)


def change(solution, *, item=None, activity=None):
    with Session(solution.database.engine) as session, session.begin():
        for model, identifier, edits in ((PracticeItem, ITEM_ID, item), (PracticeSession, SESSION_ID, activity)):
            if edits:
                row = session.get(model, identifier)
                for key, value in edits.items():
                    setattr(row, key, value)


def snapshot(solution):
    """Include existing evidence and async work to catch accidental side effects."""
    with Session(solution.database.engine) as session:
        return {model.__tablename__: [dict(row) for row in session.execute(
            select(model.__table__).order_by(*model.__table__.primary_key.columns)).mappings()]
            for model in (PracticeItem, PracticeSession, PracticeResponse, PracticeAssessment, VariantGeneration,
                          LearningOutbox, LearningEvidence, LearningProjection, StudentKnowledgeState)}


def assert_rejected(solution, *, status, code=None, url=URL):
    before = snapshot(solution)
    response = solution.client.post(url)
    assert response.status_code == status, response.text
    if code:
        assert response.json()["code"] == code
    assert not {"answer_text", "answer_values", "field_answers", "solution_text"} & response.json().keys()
    assert STEPS not in response.text
    assert snapshot(solution) == before
    return response


@pytest.mark.parametrize("edits,expected", [
    ({}, "-4"),
    ({"answer_kind": "text", "answer_values": ["x = -4", "-4 = x"]}, "x = -4"),
    ({"question_kind": "choice", "question_type": "single_choice", "answer_kind": "choice",
      "options": [{"label": "A", "text": "4"}, {"label": "E", "text": "-4"}], "answer_values": ["E"]}, "E"),
    ({"question_kind": "choice", "question_type": "multiple_choice", "answer_kind": "choice",
      "options": [{"label": "A", "text": "4"}, {"label": "B", "text": "-4"}, {"label": "E", "text": "-4.0"}],
      "answer_values": ["E", "B"]}, "B、E"),
    ({"question_kind": "fill_blank", "question_type": "fill_blank", "answer_kind": "text", "answer_values": [],
      "answer_fields": [{"id": "result", "label": "商"}, {"id": "sign", "label": "符号"}],
      "field_answers": [{"field_id": "sign", "answer_kind": "text", "answer_values": ["负", "负数"]},
                        {"field_id": "result", "answer_kind": "number", "answer_values": ["-4", "-4.0"]}]},
     "商：-4\n符号：负"),
    ({"status": "approved", "source": "staff", "owner_student_id": None, "question_kind": None,
      "verification": None, "answer_kind": "choice", "answer_values": ["CA"]}, "A、C"),
    ({"status": "approved", "source": "staff", "owner_student_id": None, "question_kind": None,
      "verification": None}, "-4"),
    ({"status": "approved", "owner_student_id": None, "verification": None, "reviewed_by": "actor_a"}, "-4"),
], ids=["number", "text-equivalent", "single-choice", "multiple-choice", "labeled-blanks",
        "legacy-choice", "staff-bank", "staff-reviewed-model"])
def test_saved_reference_formats_and_repeated_reveal(solution, edits, expected):
    change(solution, item=edits)
    before = solution.client.get(SESSION_URL)
    assert before.status_code == 200, before.text
    assert not {"answer_text", "answer_values", "field_answers", "solution_text"} & before.json()["items"][0].keys()
    state = snapshot(solution)
    for _ in range(2):
        response = solution.client.post(URL)
        assert response.status_code == 200, response.text
        assert response.json() == {"item_id": ITEM_ID, "answer_text": expected, "solution_text": STEPS}
        assert response.headers["Cache-Control"] == "no-store"
    state["practice_sessions"][0]["hints_used"] = [ITEM_ID]
    assert snapshot(solution) == state
    # A reveal is explicit; ordinary reads still do not include an unsubmitted answer.
    assert "solution_text" not in solution.client.get(SESSION_URL).json()["items"][0]


@pytest.mark.parametrize("status", ["active", "completed", "exited", "time_up"])
@pytest.mark.parametrize("bank", [False, True], ids=["generated", "staff-bank"])
def test_historical_variant_reveal_does_not_restart_or_expire_session(solution, status, bank):
    change(solution, activity={"status": status, "budget_ends_at": solution.stamp - timedelta(days=1),
        "ended_at": None if status == "active" else solution.stamp, "exit_reason": "tired" if status == "exited" else None,
        **({"gate": {}} if bank else {})},
        item={"status": "approved", "owner_student_id": None, "source": "staff"} if bank else None)
    state = snapshot(solution)
    assert solution.client.post(URL).status_code == 200
    state["practice_sessions"][0]["hints_used"] = [ITEM_ID]
    assert snapshot(solution) == state


@pytest.mark.parametrize("status", ["active", "completed", "exited", "time_up"])
def test_preview_never_reveals_solution_in_any_lifecycle_state(solution, status):
    change(solution, activity={"purpose": "preview", "status": status})
    assert_rejected(solution, status=409, code="solutions_disabled")


@pytest.mark.parametrize("mode", ["missing", "invalid", "expired", "revoked-grant", "inactive-student"])
def test_authentication_and_current_student_access(solution, mode):
    if mode == "missing":
        solution.client.headers.pop("Authorization")
    elif mode == "invalid":
        solution.client.headers["Authorization"] = "Bearer invalid-token"
    elif mode == "expired":
        solution.client.headers["Authorization"] = "Bearer " + solution.signing.token(
            overrides={"exp": now() - timedelta(seconds=1)})
    else:
        with Session(solution.database.engine) as session, session.begin():
            if mode == "revoked-grant":
                session.scalar(select(StudentGrant).where(StudentGrant.principal_id == "actor_a")).active = False
            else:
                session.get(Student, "student_a").active = False
    assert_rejected(solution, status=401 if mode in ("missing", "invalid", "expired") else 404)


@pytest.mark.parametrize("target", ["student_ungranted", "student_b"])
def test_foreign_session_is_inaccessible(solution, target):
    change(solution, activity={"student_id": target, "tenant_id": "tenant_b" if target == "student_b" else "tenant_a"})
    assert_rejected(solution, status=404)


@pytest.mark.parametrize("mode,status", [
    ("foreign-tenant", 404), ("foreign-owner", 404), ("other-authorized-owner", 409),
    ("unowned-generated", 409), ("graph-mismatch", 409), ("not-in-session", 409), ("missing-item", 404),
])
def test_item_access_and_session_binding(solution, mode, status):
    if mode == "foreign-tenant":
        change(solution, item={"tenant_id": "tenant_b", "owner_student_id": "student_b"})
    elif mode in ("foreign-owner", "other-authorized-owner"):
        change(solution, item={"owner_student_id": "student_ungranted"})
        if mode == "other-authorized-owner":
            with Session(solution.database.engine) as session, session.begin():
                session.add(StudentGrant(principal_id="actor_a", student_id="student_ungranted", tenant_id="tenant_a", active=True))
    elif mode == "unowned-generated":
        change(solution, item={"owner_student_id": None})
    elif mode == "graph-mismatch":
        change(solution, activity={"graph_version": "pep-math-junior-pilot-1"})
    elif mode == "not-in-session":
        change(solution, activity={"item_ids": []})
    else:
        with Session(solution.database.engine) as session, session.begin():
            session.delete(session.get(PracticeItem, ITEM_ID))
    assert_rejected(solution, status=status)


def test_unknown_session_and_other_tenant_actor(solution):
    assert_rejected(solution, status=404, url=URL.replace(SESSION_ID, "missing_session"))
    solution.client.headers["Authorization"] = "Bearer " + solution.signing.token("subject-b")
    assert_rejected(solution, status=404)


@pytest.mark.parametrize("edits", [
    {"status": "candidate"}, {"status": "rejected"}, {"status": "retired"},
    {"verification": None}, {"verification": {"status": "failed"}},
    {"answer_values": []}, {"answer_values": ["   "]}, {"solution_text": " \n "},
    {"question_kind": "choice", "answer_kind": "choice", "answer_values": ["Z"],
     "options": [{"label": "A", "text": "4"}, {"label": "B", "text": "-4"}]},
    {"question_kind": "fill_blank", "answer_fields": [], "field_answers": []},
    {"question_kind": "fill_blank", "answer_fields": [{"id": "one", "label": "第一空"}], "field_answers": []},
    {"question_kind": "fill_blank", "answer_fields": [{"id": "one", "label": "第一空"}],
     "field_answers": [{"field_id": "one", "answer_values": []}]},
    {"question_kind": "fill_blank", "answer_fields": [{"id": "one", "label": "第一空"}],
     "field_answers": [{"field_id": "other", "answer_values": ["-4"]}]},
], ids=["candidate", "rejected", "retired", "unverified", "failed-verification", "missing-answer", "blank-answer",
        "missing-steps", "invalid-choice", "missing-fields", "missing-blank", "empty-blank", "wrong-field"])
def test_missing_or_unverified_reference_is_not_revealed_or_counted_as_help(solution, edits):
    change(solution, item=edits)
    assert_rejected(solution, status=404)


def test_existing_hint_is_not_duplicated_and_next_response_tracks_help(solution):
    change(solution, item={"status": "approved", "source": "staff", "owner_student_id": None, "question_kind": None})
    assert solution.client.post(f"{SESSION_URL}/items/{ITEM_ID}/hint").status_code == 200
    assert solution.client.post(URL).status_code == 200
    result = solution.client.post(f"{SESSION_URL}/responses", json={"item_id": ITEM_ID, "answer_text": "-4"})
    assert result.status_code == 201 and result.json()["result"] == "correct"
    with Session(solution.database.engine) as session:
        assert session.get(PracticeSession, SESSION_ID).hints_used == [ITEM_ID]
        assert session.scalar(select(PracticeResponse)).hint_used is True


def add_assessment(solution, key, status, *, item_id=ITEM_ID, session_id=SESSION_ID, at=None):
    with Session(solution.database.engine) as session, session.begin():
        session.add(PracticeAssessment(id=key, tenant_id="tenant_a", student_id="student_a",
            session_id=session_id, item_id=item_id, item_revision=1, item_digest="synthetic",
            submission={"answer_text": "合成作答"}, status=status, audit={},
            created_at=at or solution.stamp, updated_at=at or solution.stamp))


@pytest.mark.parametrize("status", ["queued", "grading"])
def test_latest_pending_assessment_rejects_reveal_without_marking_help(solution, status):
    add_assessment(solution, "assessment_old", "completed", at=solution.stamp - timedelta(seconds=1))
    add_assessment(solution, "assessment_new", status)
    assert_rejected(solution, status=409, code="solution_pending")


@pytest.mark.parametrize("status", ["queued", "grading"])
@pytest.mark.parametrize("job_status", ["dead", "queued", "running", "succeeded", "missing"])
def test_pending_assessment_uses_terminal_outbox_status(solution, status, job_status):
    add_assessment(solution, "assessment_pending", status)
    with Session(solution.database.engine) as session, session.begin():
        session.get(PracticeAssessment, "assessment_pending").outbox_id = "outbox_pending"
        if job_status != "missing":
            session.add(LearningOutbox(id="outbox_pending", tenant_id="tenant_a", student_id="student_a",
                kind="assess_student_practice", dedupe_key="synthetic_pending",
                payload={"assessment_id": "assessment_pending"}, status=job_status, tries=1,
                available_at=solution.stamp, created_at=solution.stamp))
    if job_status == "dead":
        before = snapshot(solution)
        assert solution.client.post(URL).status_code == 200
        before["practice_sessions"][0]["hints_used"] = [ITEM_ID]
        assert snapshot(solution) == before
    else:
        assert_rejected(solution, status=409, code="solution_pending")


@pytest.mark.parametrize("status", ["completed", "failed", "superseded"])
def test_only_this_items_latest_assessment_blocks_reveal(solution, status):
    add_assessment(solution, "assessment_old", "grading", at=solution.stamp - timedelta(seconds=1))
    add_assessment(solution, "assessment_new", status)
    add_assessment(solution, "assessment_other_item", "queued", item_id="other_item")
    add_assessment(solution, "assessment_other_session", "grading", session_id="other_session")
    before = snapshot(solution)
    assert solution.client.post(URL).status_code == 200
    before["practice_sessions"][0]["hints_used"] = [ITEM_ID]
    assert snapshot(solution) == before


def test_assessment_order_matches_session_view_when_timestamps_tie(solution):
    add_assessment(solution, "assessment_a", "completed")
    add_assessment(solution, "assessment_b", "queued")
    assert_rejected(solution, status=409, code="solution_pending")


@pytest.mark.parametrize("submission", ["text", "photo"])
@pytest.mark.parametrize("reveal_first", [False, True], ids=["independent", "assisted"])
def test_synthetic_async_submission_and_completion_preserve_help_snapshot(solution, submission, reveal_first):
    if reveal_first:
        assert solution.client.post(URL).status_code == 200
    if submission == "text":
        response = solution.client.post(f"{SESSION_URL}/responses", json={"item_id": ITEM_ID, "answer_text": "(-24)÷6=-4"})
    else:
        response = solution.client.post(f"{SESSION_URL}/photo-responses", json={"item_id": ITEM_ID, "image": image_payload()})
    assert response.status_code == 202, response.text
    assessment_id = response.json()["assessment_id"]
    assert_rejected(solution, status=409, code="solution_pending")
    with Session(solution.database.engine) as session, session.begin():
        session.get(PracticeAssessment, assessment_id).status = "grading"
    assert_rejected(solution, status=409, code="solution_pending")
    grading = {"result": "correct", "feedback": "合成核验通过。"}
    if submission == "photo":
        client = Client(readback(item_id=ITEM_ID, answer_text="(-24)÷6=-4"), grading)
        outcome = assess(solution, assessment_id, client)
        assert len(client.calls) == 2
    else:
        client = Client(grading)
        with Session(solution.database.engine) as session, session.begin():
            assessment = session.get(PracticeAssessment, assessment_id)
            job = session.get(LearningOutbox, assessment.outbox_id)
            job.tries, job.status = 1, "running"
            outcome = assess_student_practice(session, job, SimpleNamespace(client=client))
            job.status = "succeeded"
        assert len(client.calls) == 1
    assert outcome["status"] == "completed", outcome
    with Session(solution.database.engine) as session:
        assert session.scalar(select(PracticeResponse)).hint_used is reveal_first
        assert session.get(PracticeSession, SESSION_ID).status == "completed"
    before = snapshot(solution)
    for _ in range(2):
        assert solution.client.post(URL).status_code == 200
    before["practice_sessions"][0]["hints_used"] = [ITEM_ID]
    assert snapshot(solution) == before


def test_reveal_after_submission_preserves_existing_response_and_mastery(solution):
    change(solution, item={"status": "approved", "source": "staff", "owner_student_id": None, "question_kind": None})
    with Session(solution.database.engine) as session, session.begin():
        session.add(StudentKnowledgeState(student_id="student_a", tenant_id="tenant_a", knowledge_id="synthetic_kp",
            graph_version="synthetic_graph", rule_version="synthetic_rule", state_version=3,
            document={"learning_status": "consolidating", "evidence_ids": ["evidence_solution"]}, computed_at=solution.stamp))
        session.add(LearningEvidence(id="evidence_solution", tenant_id="tenant_a", campus_id="campus_a", student_id="student_a",
            knowledge_id="synthetic_kp", source_key="synthetic_source", document={"help": "independent"},
            occurred_at=solution.stamp, recorded_at=solution.stamp, validity="valid"))
        session.add(LearningProjection(student_id="student_a", tenant_id="tenant_a", source_fingerprint="synthetic",
            evidence_revision=2, state_version=3, projected_at=solution.stamp))
    response = solution.client.post(f"{SESSION_URL}/responses", json={"item_id": ITEM_ID, "answer_text": "-4"})
    assert response.status_code == 201 and response.json()["session_status"] == "completed"
    state = snapshot(solution)
    assert state["practice_responses"][0]["hint_used"] is False
    assert solution.client.post(URL).status_code == 200
    state["practice_sessions"][0]["hints_used"] = [ITEM_ID]
    assert snapshot(solution) == state
