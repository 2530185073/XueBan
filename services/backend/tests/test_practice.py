"""Voluntary variants and preview: the three-part gate, reviewed bank, no debt and no effect on state."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401  (pytest fixture reuse)
from test_learning import FakeClient, evidence_count, knowledge, learning, link, review, reviewed
from xueban_backend import practice
from xueban_backend.asset_store import LocalAssetStore
from xueban_backend.device_auth import utc
from xueban_backend.governance import purge_student
from xueban_backend.learning_models import OptionalWindow, PracticeItem, PracticeResponse, PracticeSession
from xueban_backend.models import Homework
from xueban_backend.practice import grade
from xueban_backend.providers.ark import Metadata


def item(kind, values):
    return SimpleNamespace(answer_kind=kind, answer_values=values)


def test_rule_grading_is_exact_and_conservative():
    assert grade(item("number", ["3/4"]), "0.75") == "correct"
    assert grade(item("number", ["-2"]), "x = −2") == "correct"
    assert grade(item("number", ["2"]), "3") == "incorrect"
    assert grade(item("number", ["2"]), "大约两个") == "unscorable"
    assert grade(item("choice", ["B"]), "ｂ") == "correct"
    assert grade(item("choice", ["AC"]), "C A") == "correct"
    assert grade(item("choice", ["B"]), "我不知道") == "unscorable"
    assert grade(item("text", ["2x+3"]), "2x + 3") == "correct"
    assert grade(item("text", ["2x+3"]), "3+2x") == "incorrect"


@pytest.fixture
def clock(flow, monkeypatch):
    with Session(flow.database.engine) as session:
        occurred = utc(session.get(Homework, flow.homework_id).occurred_at)
    state = {"t": occurred + timedelta(minutes=1)}
    monkeypatch.setattr(practice, "now", lambda: state["t"])
    return state


def new_item(flow, knowledge_id="kp-rational-add-sub", family=None, answer="5", hint="先看符号"):
    response = flow.client.post("/v1/practice-items", json={
        "knowledge_id": knowledge_id, "item_family_id": family, "stem_text": f"计算：2 + 3 = ?（{family or 'x'}）",
        "answer_kind": "number", "answer_values": [answer], "hint_text": hint, "solution_text": "2 + 3 = 5"})
    assert response.status_code == 201, response.text
    return response.json()


def open_window(flow, revision=0, minutes=10, confirm=None):
    response = flow.client.put("/v1/students/student_a/optional-window",
                               json={"expected_revision": revision, "capacity_observed": True, "extra_minutes": minutes,
                                     "school_tasks_confirmed": confirm})
    assert response.status_code == 200, response.text
    return response.json()


def start(flow, **body):
    return flow.client.post("/v1/students/student_a/practice-sessions", json={"willing": True, **body})


def needs_help(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    link(flow, case)
    learning(flow).drain()
    return case


def test_gate_needs_school_tasks_capacity_and_budget(flow, clock):
    reviewed(flow)
    closed = flow.client.get("/v1/students/student_a/optional-activities").json()
    assert closed["gate"]["open"] is False and closed["variants"] == [] and closed["preview"] is None
    assert set(closed["gate"]["reasons"]) == {"school_tasks_not_confirmed", "capacity_not_observed", "no_extra_budget"}
    assert flow.client.put("/v1/students/student_a/optional-window",
                           json={"expected_revision": 0, "capacity_observed": True, "extra_minutes": 10}).status_code == 403
    teacher(flow)
    gate = open_window(flow)
    assert gate["open"] is False and gate["reasons"] == ["school_tasks_not_confirmed"]
    assert gate["school_tasks_complete"]["detail"].startswith("open_cases:1")
    gate = open_window(flow, revision=1, confirm=True)
    assert gate["open"] is True and gate["school_tasks_complete"]["source"] == "staff"
    assert flow.client.put("/v1/students/student_a/optional-window",
                           json={"expected_revision": 1, "capacity_observed": True, "extra_minutes": 10}).status_code == 409
    missing = flow.client.post("/v1/students/student_a/practice-sessions", json={"purpose": "preview"})
    assert missing.status_code == 422
    unknown = start(flow, purpose="preview")
    assert unknown.status_code == 409 and unknown.json()["code"] == "course_unknown"


def test_variant_uses_reviewed_items_and_never_changes_state(flow, clock):
    needs_help(flow)
    before = knowledge(flow)["states"]
    new_item(flow, family="fam-a")
    new_item(flow, family="fam-a")
    new_item(flow, family="fam-b", hint=None)
    blocked = start(flow, purpose="variant", knowledge_id="kp-rational-add-sub")
    assert blocked.status_code == 409 and blocked.json()["code"] == "optional_gate_closed"
    open_window(flow, confirm=True)
    offered = flow.client.get("/v1/students/student_a/optional-activities").json()
    assert [(v["knowledge_id"], v["available_items"]) for v in offered["variants"]] == [("kp-rational-add-sub", 3)]
    assert start(flow, purpose="variant", knowledge_id="kp-rational-compare").json()["code"] == "practice_not_needed"
    session = start(flow, purpose="variant", knowledge_id="kp-rational-add-sub", count=3)
    assert session.status_code == 201, session.text
    body = session.json()
    assert len(body["items"]) == 2 and "answer_values" not in body["items"][0] and "solution_text" not in body["items"][0]
    assert start(flow, purpose="variant", knowledge_id="kp-rational-add-sub").status_code == 409
    first, second = sorted(body["items"], key=lambda i: not i["has_hint"])
    assert flow.client.post(f"/v1/practice-sessions/{body['id']}/items/{first['id']}/hint").json()["hint_text"] == "先看符号"
    key = {"Idempotency-Key": "practice-answer-1"}
    answered = flow.client.post(f"/v1/practice-sessions/{body['id']}/responses", json={"item_id": first["id"], "answer_text": "5"}, headers=key)
    assert answered.status_code == 201 and answered.json()["result"] == "correct" and answered.json()["solution_text"]
    assert flow.client.post(f"/v1/practice-sessions/{body['id']}/responses",
                            json={"item_id": first["id"], "answer_text": "5"}, headers=key).json() == answered.json()
    assert flow.client.post(f"/v1/practice-sessions/{body['id']}/responses",
                            json={"item_id": first["id"], "answer_text": "5"}).status_code == 409
    last = flow.client.post(f"/v1/practice-sessions/{body['id']}/responses", json={"item_id": second["id"], "answer_text": "6"}).json()
    assert last["result"] == "incorrect" and last["session_status"] == "completed"
    detail = {i["id"]: i for i in flow.client.get(f"/v1/practice-sessions/{body['id']}").json()["items"]}
    assert detail[first["id"]]["response"]["hint_used"] is True
    detail = flow.client.get(f"/v1/practice-sessions/{body['id']}").json()
    assert detail["gate"]["child_willing"]["value"] is True
    learning(flow).drain()
    assert knowledge(flow)["states"] == before and evidence_count(flow) == 1
    with Session(flow.database.engine) as db:
        events = set(db.scalars(select(PracticeResponse.event_type)))
    assert events == {"optional_variant"}


def test_preview_quiz_has_no_hints_and_leaving_creates_no_debt(flow, clock):
    reviewed(flow)
    teacher(flow)
    flow.client.put("/v1/students/student_a/learning-profile",
                    json={"expected_revision": 0, "current_knowledge_ids": ["kp-linear-eq-brackets"]})
    new_item(flow, knowledge_id="kp-linear-eq-brackets", family="fam-c")
    open_window(flow, confirm=True)
    offered = flow.client.get("/v1/students/student_a/optional-activities").json()
    assert offered["preview"]["quiz_items_available"] == 1
    body = start(flow, purpose="preview", with_quiz=True).json()
    assert body["targets"][0]["id"] == "kp-linear-eq-brackets" and body["hints_allowed"] is False
    assert {p["id"] for p in body["targets"][0]["prerequisites"]} >= {"kp-remove-brackets"}
    assert all(p["learning_status"] == "no_judgment" for p in body["targets"][0]["prerequisites"])
    hint = flow.client.post(f"/v1/practice-sessions/{body['id']}/items/{body['items'][0]['id']}/hint")
    assert hint.status_code == 409 and hint.json()["code"] == "hints_disabled"
    left = flow.client.post(f"/v1/practice-sessions/{body['id']}/exit", json={"reason": "tired"}).json()
    assert left["status"] == "exited" and left["exit_reason"] == "tired"
    reading = start(flow, purpose="preview")
    assert reading.status_code == 201 and reading.json()["items"] == []


def test_budget_runs_out_and_closes_the_gate(flow, clock):
    needs_help(flow)
    new_item(flow, family="fam-a")
    open_window(flow, minutes=5, confirm=True)
    body = start(flow, purpose="variant", knowledge_id="kp-rational-add-sub").json()
    clock["t"] += timedelta(minutes=6)
    assert flow.client.get(f"/v1/practice-sessions/{body['id']}").json()["status"] == "time_up"
    assert flow.client.get("/v1/students/student_a/practice-sessions").json()["items"][0]["status"] == "time_up"
    late = flow.client.post(f"/v1/practice-sessions/{body['id']}/responses", json={"item_id": body["items"][0]["id"], "answer_text": "5"})
    assert late.status_code == 409
    gate = flow.client.get("/v1/students/student_a/optional-activities").json()["gate"]
    assert gate["open"] is False and gate["reasons"] == ["no_extra_budget"]


class ItemClient(FakeClient):
    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data={"items": [
            {"stem_text": "计算：(-3) + 5 = ?", "answer_kind": "number", "answer_values": ["2"], "hint_text": "异号相加",
             "solution_text": "绝对值相减取大数符号", "difficulty": 1}]}, metadata=Metadata("m", "m", None, None, None, 1, ()))


def test_model_drafts_candidates_that_staff_must_approve(flow, clock):
    case = reviewed(flow)
    assert flow.client.post("/v1/practice-items/generate", json={"knowledge_id": "kp-rational-add-sub"}).status_code == 403
    teacher(flow)
    assert flow.client.post("/v1/practice-items/generate", json={"knowledge_id": "ch-7a-02"}).status_code == 422
    queued = flow.client.post("/v1/practice-items/generate", json={"knowledge_id": "kp-rational-add-sub", "count": 1,
                                                                   "source_case_id": case["id"]})
    assert queued.status_code == 202, queued.text
    client = ItemClient([])
    learning(flow, client).drain()
    assert "1 + 1" in client.calls[0]["text"] and "student_a" not in client.calls[0]["text"]
    [candidate] = flow.client.get("/v1/practice-items?knowledge_id=kp-rational-add-sub").json()["items"]
    assert candidate["status"] == "candidate" and candidate["source"] == "model"
    approved = flow.client.post(f"/v1/practice-items/{candidate['id']}/review",
                                json={"action": "approve", "expected_revision": 1, "edits": {"difficulty": 2}})
    assert approved.status_code == 200 and approved.json()["status"] == "approved" and approved.json()["difficulty"] == 2
    assert flow.client.post(f"/v1/practice-items/{candidate['id']}/review",
                            json={"action": "approve", "expected_revision": 1}).status_code == 409


def test_report_mentions_voluntary_activity_and_purge(flow, clock):
    needs_help(flow)
    new_item(flow, family="fam-a")
    open_window(flow, confirm=True)
    learning(flow).drain()
    from xueban_backend.learning_projection import dirty_students
    with Session(flow.database.engine) as session:
        assert dirty_students(session) == []
    body = start(flow, purpose="variant", knowledge_id="kp-rational-add-sub").json()
    flow.client.post(f"/v1/practice-sessions/{body['id']}/exit", json={})
    with Session(flow.database.engine) as session:
        assert ("tenant_a", "student_a") in dirty_students(session)
    from xueban_backend.learning_reports import build_snapshot, period_of, local_day
    from xueban_backend.models import Student
    with Session(flow.database.engine) as session:
        student = session.get(Student, "student_a")
        snapshot = build_snapshot(session, student, *period_of(local_day(clock["t"])))
        section = snapshot["sections"]["optional_activity"]
        assert (section["sessions"], section["exited"], section["answered"]) == (1, 1, 0)
        assert "不计入" in section["summary"]
        purge_student(session, LocalAssetStore(flow.cfg.asset_storage_dir), "tenant_a", "student_a")
        session.commit()
        for model in (PracticeSession, PracticeResponse, OptionalWindow):
            assert session.scalar(select(func.count()).select_from(model).where(model.student_id == "student_a")) == 0
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 1


def test_resolved_photographed_homework_satisfies_school_tasks_without_staff_override(flow, clock):
    reviewed(flow, verdict="correct")
    teacher(flow)
    gate = open_window(flow)
    assert gate["open"] is True
    assert gate["school_tasks_complete"] == {"value": True, "source": "system", "detail": "photographed_homework_resolved"}
