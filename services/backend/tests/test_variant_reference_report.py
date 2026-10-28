"""Owned cached sessions and report filtering; disposable SQLite, fake models."""
from copy import deepcopy
from datetime import timedelta
import socket

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import ready
from xueban_backend import practice
from xueban_backend.device_auth import utc
from xueban_backend.learning_models import PracticeItem, PracticeResponse, PracticeSession, VariantGeneration
from xueban_backend.models import Student
from xueban_backend.providers.correction_pipeline import CorrectionPipeline


pytestmark = pytest.mark.parametrize("database", ["sqlite"], indirect=True)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Reference regressions must not use live models or network services")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(CorrectionPipeline, "from_env", forbidden)


def cached_activity(flow, entry):
    _, task, model = ready(flow)
    body = {"purpose": "variant", "willing": True, "generation_job_id": task["job_id"], "count": 1}
    path = "/v1/students/student_a/practice-sessions"
    key = "reference-start-cache"
    response = flow.client.post(path, json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    activity = response.json()
    if entry == "retry":
        submitted = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
            "item_id": activity["items"][0]["id"], "answers": [{"field_id": "blank1", "answer_text": "8"}]})
        assert submitted.status_code == 201 and submitted.json()["result"] == "incorrect"
        path, body, key = f"/v1/practice-sessions/{activity['id']}/retry", {}, "reference-retry-cache"
        response = flow.client.post(path, json=body, headers={"Idempotency-Key": key})
        assert response.status_code == 201, response.text
    replay = flow.client.post(path, json=body, headers={"Idempotency-Key": key})
    assert replay.status_code == 201 and replay.json() == response.json()
    assert len(model.calls) == 2
    return task, activity, path, body, key, response.json()


@pytest.mark.parametrize("entry", ["start", "retry"])
def test_cached_generated_session_rejects_bad_live_reference(flow, entry):
    _, activity, path, body, key, cached = cached_activity(flow, entry)
    with Session(flow.database.engine) as session, session.begin():
        item = session.get(PracticeItem, activity["items"][0]["id"])
        item.stem_text = "3+4=____"
        item.field_answers = [{"field_id": "blank1", "answer_kind": "number", "answer_values": ["8"]}]
        before_sessions = session.scalar(select(func.count()).select_from(PracticeSession))
        before_responses = [(row.id, row.answer_text, row.result) for row in session.scalars(select(PracticeResponse))]
    rejected = flow.client.post(path, json=body, headers={"Idempotency-Key": key})
    assert rejected.status_code == 409 and rejected.json()["code"] == "verification_failed"
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeSession)) == before_sessions
        assert [(row.id, row.answer_text, row.result) for row in session.scalars(select(PracticeResponse))] == before_responses
        assert session.get(PracticeSession, cached["id"]) is not None


@pytest.mark.parametrize("entry", ["start", "retry"])
@pytest.mark.parametrize("foreign", ["item_owner", "generation_owner", "item_tenant"])
def test_cached_reference_checks_owned_ids_before_inspecting_answers(flow, monkeypatch, entry, foreign):
    task, activity, path, body, key, _ = cached_activity(flow, entry)
    with Session(flow.database.engine) as session, session.begin():
        item = session.get(PracticeItem, activity["items"][0]["id"])
        if foreign == "generation_owner":
            session.get(VariantGeneration, task["job_id"]).student_id = "student_ungranted"
        elif foreign == "item_owner":
            item.owner_student_id = "student_ungranted"
        else:
            item.tenant_id, item.owner_student_id = "tenant_b", "student_b"
    inspected = []
    monkeypatch.setattr(practice, "item_arithmetic_issues", lambda item: inspected.append(item.id) or [])
    response = flow.client.post(path, json=body, headers={"Idempotency-Key": key})
    assert response.status_code == (404 if foreign == "generation_owner" else 409)
    assert inspected == []


def test_retry_cache_cannot_restore_removed_generation_binding(flow):
    _, activity, path, body, key, _ = cached_activity(flow, "retry")
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(PracticeSession, activity["id"])
        row.gate = {name: value for name, value in row.gate.items() if name != "generation_job_id"}
    response = flow.client.post(path, json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 409 and response.json()["code"] == "retry_not_available"


@pytest.mark.parametrize("bad_result", ["correct", "incorrect"])
def test_report_excludes_bad_reference_counts_and_keeps_history(flow, bad_result):
    _, activity, _, _, _, _ = cached_activity(flow, "start")
    item_id = activity["items"][0]["id"]
    submitted = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": item_id, "answers": [{"field_id": "blank1", "answer_text": "7" if bad_result == "correct" else "8"}]})
    assert submitted.status_code == 201 and submitted.json()["result"] == bad_result
    with Session(flow.database.engine) as session, session.begin():
        original = session.get(PracticeItem, item_id)
        original.stem_text = "3+4=____"
        original.field_answers = [{"field_id": "blank1", "answer_kind": "number", "answer_values": ["8"]}]
        response = session.scalar(select(PracticeResponse).where(PracticeResponse.item_id == item_id))
        stamp = response.occurred_at
        start, end = utc(stamp) - timedelta(days=1), utc(stamp) + timedelta(days=1)
        for label, owner, tenant, result, when in (
            ("good_shared", None, "tenant_a", "correct", stamp),
            ("good_owned", "student_a", "tenant_a", "incorrect", stamp),
            ("other_student", "student_ungranted", "tenant_a", "correct", stamp),
            ("other_tenant", "student_b", "tenant_b", "correct", stamp),
            ("outside_window", "student_a", "tenant_a", "correct", stamp + timedelta(days=2)),
        ):
            identifier = "item_report_" + label
            session.add(PracticeItem(id=identifier, tenant_id=tenant, owner_student_id=owner,
                graph_version=None, knowledge_id=None, item_family_id=identifier, question_kind="fill_blank",
                question_type="fill_blank", stem_text="3+4=____", options=[],
                answer_fields=[{"id": "blank1", "label": "第1空"}],
                field_answers=[{"field_id": "blank1", "answer_kind": "number", "answer_values": ["7"]}],
                answer_kind="text", answer_values=[], solution_text="3+4=7", difficulty=1, source="model",
                status="ai_verified", revision=1, created_at=stamp, updated_at=stamp))
            # Deliberately malformed foreign pointers must not leak into the
            # current student's totals even if an old imported row holds one.
            session.add(PracticeResponse(id="presp_report_" + label, tenant_id="tenant_a", student_id="student_a",
                session_id=activity["id"], item_id=identifier, graph_version=None, knowledge_id=None,
                event_type="optional_variant", answer_text="7", result=result, hint_used=False, occurred_at=when))
    with Session(flow.database.engine) as session:
        before = [(row.id, row.result, deepcopy(row.details)) for row in session.scalars(select(PracticeResponse))]
        name, report = practice.optional_section(session, session.get(Student, "student_a"), start, end)
        assert name == "optional_activity"
        assert report["sessions"] == 1
        assert report["answered"] == 2 and report["correct"] == 1
        assert "完成 2 道题" in report["summary"]
        assert [(row.id, row.result, deepcopy(row.details)) for row in session.scalars(select(PracticeResponse))] == before


def test_empty_report_remains_empty(flow):
    at = practice.now()
    with Session(flow.database.engine) as session:
        _, report = practice.optional_section(session, session.get(Student, "student_a"),
                                             at - timedelta(days=1), at + timedelta(days=1))
        assert report["sessions"] == report["answered"] == report["correct"] == 0
