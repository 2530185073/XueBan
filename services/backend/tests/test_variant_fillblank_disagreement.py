"""Regression for the live Q1 sign-slot/complete-terminal-result disagreement."""

from copy import deepcopy
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import Client, candidate, queue, run, verification
from test_variant_fillblank_repair import execution, source_with_stem
from xueban_backend.learning_models import PracticeItem
from xueban_backend.practice_generation import (
    GENERATE_PROMPT, VERIFY_PROMPT, _field_disagreement_issues,
)


SOURCE = ("计算：\n(1)(+28)÷(+4)=+(28÷____)=____。\n"
          "(2)(-21)÷(-7)=____(21÷____)=____。\n"
          "(3)(-24)÷6=____(24÷6)=____。\n(4)0÷(-2026)=____。")


def signed_candidate(*, wrong_value=None, changed_numbers=False):
    """Same eight slots as the audited failure; blank6 is a sign, blank7 is not."""
    value = candidate()
    dividend, divisor, terminal = (48, 8, "-6") if changed_numbers else (35, 7, "-5")
    answers = ("9", "4", "+", "8", "3", "-", terminal if wrong_value is None else wrong_value, "0")
    slots = ((1, 1), (1, 2), (2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (4, 1))
    value.update(
        stem_text=("计算：\n(1)(+36)÷(+9)=+(36÷____)=____。\n"
                   "(2)(-24)÷(-8)=____(24÷____)=____。\n"
                   f"(3)(-{dividend})÷{divisor}=____({dividend}÷{divisor})=____。\n(4)0÷(-2027)=____。"),
        answer_fields=[{"id": f"blank{i}", "label": f"第({part})小题第{slot}空"}
                       for i, (part, slot) in enumerate(slots, 1)],
        field_answers=[{"field_id": f"blank{i}", "answer_kind": "text" if i in (3, 6) else "number",
                        "answer_values": [answer]} for i, answer in enumerate(answers, 1)],
        solution_text=("(1)同号得正，(+36)÷(+9)=+(36÷9)=4。\n"
                       "(2)同号得正，(-24)÷(-8)=+(24÷8)=3。\n"
                       f"(3)异号得负，(-{dividend})÷{divisor}=-({dividend}÷{divisor})={terminal}。\n"
                       "(4)0除以非零数得0，0÷(-2027)=0。"),
        hint_text="先判断符号，再计算绝对值。",
    )
    return value


def assert_blind(call):
    payload = json.loads(call["text"])
    assert set(payload) == {"stem_text", "options", "answer_fields", "question_type", "question_kind",
                            "source_curriculum", "student_grade", "source_subject", "knowledge_target",
                            "requested_question_type"}
    assert not {"repair_feedback", "observed", "expected", "answer_values", "field_answers",
                "solution_text", "hint_text", "answer_a", "answer_b"} & set(payload)


@pytest.mark.parametrize("wrong_value,changed_numbers", [("5", False), ("+5", True)])
def test_terminal_sign_failure_repairs_exact_field_and_requires_fresh_blind_solve(flow, monkeypatch, wrong_value, changed_numbers):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    first = signed_candidate(wrong_value=wrong_value)
    solved = verification(signed_candidate())
    # Position in field_answers must never be mistaken for the field identity.
    first["field_answers"].reverse()
    repaired = signed_candidate(changed_numbers=changed_numbers)
    solved_again = verification(repaired)
    solved_again["field_answers"].reverse()
    client = Client([first, solved, repaired, solved_again])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 4
    feedback = json.loads(client.calls[2]["text"])["repair_feedback"]
    assert feedback["stage"] == "blind_solve"
    assert feedback["errors"] == [{
        "code": "field_answer_disagreement", "path": "field_answers",
        "field_id": "blank7", "field_label": "第(3)小题第2空",
        "observed": {"answer_kind": "number", "answer_values": [wrong_value]},
        "expected": {"answer_kind": "number", "answer_values": ["-5"]},
        "expected_source": "blind_solve",
    }]
    assert feedback["previous_question"]["stem_text"] == first["stem_text"]
    assert_blind(client.calls[1])
    assert_blind(client.calls[3])
    audit = execution(flow, task)
    assert [call["stage"] for call in audit["calls"]] == ["generate", "blind_solve", "generate", "blind_solve"]
    assert audit["calls"][1]["validation_errors"] == feedback["errors"]
    with Session(flow.database.engine) as session:
        item = session.scalar(select(PracticeItem))
        saved = {field["field_id"]: field["answer_values"] for field in item.field_answers}
        assert saved["blank6"] == ["-"]
        assert saved["blank7"] == (["-6"] if changed_numbers else ["-5"])
        assert item.field_answers == solved_again["field_answers"]
        assert item.solution_text == solved_again["solution_text"]


@pytest.mark.parametrize("regenerations", ["0", "1"])
def test_repeated_terminal_sign_error_still_fails_at_budget_without_equivalence_override(flow, monkeypatch, regenerations):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", regenerations)
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    wrong = signed_candidate(wrong_value="5")
    solved = verification(signed_candidate())
    client = Client([wrong, solved] * (1 + int(regenerations)))
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 2 * (1 + int(regenerations))
    audit = execution(flow, task)
    assert all(call["stage"] != "answer_equivalence" for call in audit["calls"])
    for call in audit["calls"]:
        if call["stage"] == "blind_solve":
            assert call["validation_errors"][0]["field_id"] == "blank7"
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


@pytest.mark.parametrize("problem", ["self_contained", "unique_answer", "same_type", "missing_field"])
def test_invalid_independent_solution_does_not_supply_expected_answers_in_feedback(flow, monkeypatch, problem):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    wrong = signed_candidate(wrong_value="5")
    solved = verification(signed_candidate())
    if problem == "missing_field":
        solved["field_answers"].pop()
    else:
        solved[problem] = False
    client = Client([wrong, solved, wrong, solved])
    assert run(flow, task, client)["status"] == "failed"
    feedback = json.loads(client.calls[2]["text"])["repair_feedback"]
    assert all("expected" not in error and "observed" not in error for error in feedback["errors"])
    assert _field_disagreement_issues(wrong, solved) == []
    assert_blind(client.calls[3])
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


def test_feedback_preserves_numeric_equivalence_and_marks_text_differences_as_unconfirmed():
    value = signed_candidate()
    solved = verification(value)
    solved["field_answers"][6]["answer_values"] = ["-5.0"]
    assert _field_disagreement_issues(value, solved) == []
    solved["field_answers"][5]["answer_values"] = ["负号"]
    before = deepcopy((value, solved))
    issues = _field_disagreement_issues(value, solved)
    assert len(issues) == 1 and issues[0]["field_id"] == "blank6"
    assert issues[0]["code"] == "field_answer_equivalence_unconfirmed"
    assert (value, solved) == before


def test_secret_in_independent_answer_is_rejected_before_becoming_repair_feedback(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    wrong = signed_candidate(wrong_value="5")
    solved = verification(signed_candidate())
    solved["field_answers"][6].update(answer_kind="text", answer_values=["test-private-token"])
    client = Client([wrong, solved])
    assert run(flow, task, client)["status"] == "failed"
    assert len(client.calls) == 2
    assert "test-private-token" not in json.dumps(execution(flow, task))
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


@pytest.mark.parametrize("prompt", [GENERATE_PROMPT, VERIFY_PROMPT])
def test_prompts_distinguish_sign_slots_from_complete_terminal_results(prompt):
    assert "等号后的最终结果空" in prompt
    assert "完整结果" in prompt and "绝对值" in prompt
    assert "field_answers" in prompt and "solution_text" in prompt
