"""Representation differences must not reject valid work or skip step checking."""
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import Client, candidate, verification, source, queue, run, get, start, solution_agreement
from xueban_backend.learning_models import PracticeItem, VariantGeneration


def signed_division():
    generated = candidate("calculation")
    generated.update({"stem_text": "计算：(-24) ÷ 6", "answer_kind": "text", "answer_values": ["-4"],
        "solution_text": "先确定符号：异号相除得负。再算绝对值：24 ÷ 6 = 4。所以 (-24) ÷ 6 = -4。"})
    solved = verification(generated)
    solved.update({"answer_kind": "number", "solution_text": "(-24) ÷ 6 = -(24 ÷ 6) = -4。"})
    return generated, solved


def test_live_text_generator_number_blind_solution_is_accepted_only_after_full_work_check(flow):
    case = source(flow, "calculation")
    task = queue(flow, case)
    generated, solved = signed_division()
    client = Client([generated, solved, solution_agreement()])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 3
    blind = json.loads(client.calls[1]["text"])
    assert "answer_a" not in blind and "answer_values" not in blind and "solution_text" not in blind
    check = json.loads(client.calls[2]["text"])
    assert check["answer_a"]["answer_kind"] == "text" and check["answer_b"]["answer_kind"] == "number"
    assert check["answer_a"]["solution_text"] == generated["solution_text"]
    assert check["answer_b"]["solution_text"] == solved["solution_text"]
    with Session(flow.database.engine) as session:
        item = session.get(PracticeItem, get(flow, task)["item_id"])
        assert item.question_kind == "solution" and item.answer_kind == "number"
        assert item.solution_text == solved["solution_text"] and item.reviewed_by is None
        row = session.get(VariantGeneration, task["job_id"])
        assert [call["stage"] for call in row.audit["executions"][-1]["calls"]] == ["generate", "blind_solve", "answer_equivalence"]
    activity = start(flow, task).json()
    response = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": activity["items"][0]["id"], "answer_text": "异号相除得负，24除以6是4，因此为-4。"})
    assert response.status_code == 202, "numeric reference must not bypass semantic grading of solution work"


@pytest.mark.parametrize("bad_source", ["answer_a", "answer_b"])
def test_same_terminal_answer_never_bypasses_invalid_intermediate_steps(flow, monkeypatch, bad_source):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    case = source(flow, "calculation")
    task = queue(flow, case)
    generated, solved = signed_division()
    # Same final text and type would have short-circuited _answer_agreement.
    solved["answer_kind"] = "text"
    bad = generated if bad_source == "answer_a" else solved
    bad["solution_text"] = "(-24) ÷ 6 = -(24 ÷ 6) = -5 = -4。"
    agreement = solution_agreement()
    agreement[bad_source + "_steps_valid"] = False
    # Even a contradictory aggregate flag cannot override the failed steps.
    agreement["equivalent"] = True
    agreement["reason"] = "终答相同，但含-5=-4的错误中间步骤。"
    client = Client([generated, solved, agreement])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 3
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


def test_identical_final_and_reference_text_still_get_a_full_solution_check(flow):
    task = queue(flow, source(flow, "calculation"))
    generated, _ = signed_division()
    client = Client([generated, verification(generated), solution_agreement()])
    assert run(flow, task, client)["status"] == "ready" and len(client.calls) == 3


@pytest.mark.parametrize("missing", ["blank_solution", "choice_reference", "invented_fields"])
def test_solution_reference_still_requires_complete_correct_structure(flow, monkeypatch, missing):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    task = queue(flow, source(flow, "calculation"))
    generated, solved = signed_division()
    if missing == "blank_solution":
        solved["solution_text"] = "   "
    elif missing == "choice_reference":
        solved["answer_kind"] = "choice"
    else:
        solved["field_answers"] = [{"field_id": "invented", "answer_kind": "number", "answer_values": ["-4"]}]
    client = Client([generated, solved])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 2
