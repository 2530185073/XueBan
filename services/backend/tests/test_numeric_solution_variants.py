"""Typed numerical solutions still require blind solving and full-step review."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import Client, candidate, get, queue, run, solution_agreement, source, start, verification
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import PracticeItem, PracticeResponse, StudentKnowledgeState, VariantGeneration
from xueban_backend.practice_generation import _valid_candidate, _valid_reference


def numeric_candidate(dividend=-18, divisor=6, answer="-3"):
    value = candidate("calculation")
    value.update(stem_text=f"计算：({dividend}) ÷ {divisor}。\n1. 解：", answer_kind="number",
                 answer_values=[answer], solution_text=f"({dividend}) ÷ {divisor} = -({-dividend} ÷ {divisor}) = {answer}。")
    return value


def original(flow):
    case = source(flow, "calculation")
    with Session(flow.database.engine) as session, session.begin():
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "stem_text": "计算：(-12) ÷ 3。\n1. 解：", "subject": "数学"}
    return case


@pytest.mark.parametrize("dividend,divisor,answer", [(-18, 6, "-3"), (-20, 4, "-5"), (-24, 4, "-6")])
def test_actual_evening_numeric_candidate_reaches_blind_and_step_verification(flow, dividend, divisor, answer):
    task = queue(flow, original(flow))
    generated = numeric_candidate(dividend, divisor, answer)
    solved = verification(generated)
    client = Client([generated, solved, solution_agreement()])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 3
    blind = json.loads(client.calls[1]["text"])
    assert not {"answer_kind", "answer_values", "solution_text", "field_answers", "hint_text"} & set(blind)
    assert "answer_a_steps_valid" in client.calls[2]["output_schema"]["properties"]
    with Session(flow.database.engine) as session:
        generation = session.get(VariantGeneration, task["job_id"])
        audit = generation.audit["executions"][0]["calls"]
        assert [call["stage"] for call in audit] == ["generate", "blind_solve", "answer_equivalence"]
        assert audit[0]["output"] == generated  # Original typed output is not rewritten.
        item = session.get(PracticeItem, generation.item_id)
        assert item.answer_kind == "number" and item.answer_values == [answer]
        assert item.solution_text == solved["solution_text"]
    activity = start(flow, task).json()
    item = activity["items"][0]
    assert "solution_text" not in item and "answer_values" not in item
    submitted = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": item["id"], "answer_text": answer,
    })
    # A typed numeric reference must not turn a solution into scalar grading.
    assert submitted.status_code == 202 and submitted.json()["result"] == "pending"
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeResponse)) == 0
        assert session.scalar(select(func.count()).select_from(StudentKnowledgeState)) == 0


@pytest.mark.parametrize("rejected_field", ["equivalent", "answer_a_steps_valid", "answer_b_steps_valid"])
def test_numeric_terminal_answer_cannot_bypass_full_work_review(flow, monkeypatch, rejected_field):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    task = queue(flow, original(flow))
    generated = numeric_candidate()
    agreement = {**solution_agreement(), rejected_field: False}
    client = Client([generated, verification(generated), agreement])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 3 and get(flow, task)["status"] == "failed"
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


@pytest.mark.parametrize("answer,accepted", [
    ("-3", True), ("−3", True), (" -3 / 2 ", True), (".5", True),
    ("1 2", False), ("x=3", False), ("NaN", False), ("Infinity", False),
    ("1/0", False), ("3+4", False), ("7元", False), ("", False),
])
def test_number_contract_requires_literal_finite_values_for_both_readers(answer, accepted):
    generated = numeric_candidate()
    generated["answer_values"] = [answer]
    snapshot = {"question_type": "calculation", "question_kind": "solution",
                "source_curriculum": {"subject": "数学"},
                "source_question": {"stem_text": "计算：(-12) ÷ 3。\n1. 解：", "options": []}}
    assert _valid_candidate(generated, snapshot) is accepted
    assert _valid_reference(generated, verification(generated)) is accepted


def test_numeric_answer_still_needs_solution_text_and_no_choice_or_blank_fields():
    value = numeric_candidate()
    snapshot = {"question_type": "calculation", "question_kind": "solution",
                "source_question": {"stem_text": "计算：(-12) ÷ 3。", "options": []}}
    for changes in ({"solution_text": " "}, {"answer_fields": [{"id": "blank1", "label": "空"}]},
                    {"field_answers": [{"field_id": "blank1", "answer_kind": "number", "answer_values": ["-3"]}]},
                    {"options": [{"label": "A", "text": "-3"}]}):
        assert not _valid_candidate({**deepcopy(value), **changes}, snapshot)
