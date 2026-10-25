"""Math wording changes preserve numeric dedupe and independent verification."""
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import (
    Client, candidate, get, queue, run, solution_agreement, source, start, verification,
)
from xueban_backend.learning_models import PracticeItem, VariantGeneration
from xueban_backend.practice_generation import (
    _math_numeric_signature, _near_copy_stem, _numeric_substitution, _valid_candidate,
)


SOURCE = "已知两数的商是-7/2，被除数是7/2，求除数。"
REPEATED_SOURCE = SOURCE + "\n1. " + SOURCE
HISTORY = "已知两个数的商是 -5/3，被除数是 10/3，求除数。"
NEXT = "已知两数的商是 -5/4，被除数是 5/2，求除数。"


def snapshot(stem, subject="数学"):
    return {
        "question_type": "calculation", "question_kind": "solution",
        "source_curriculum": {"grade": 7, "subject": subject},
        "source_question": {"stem_text": stem, "options": []},
    }


@pytest.mark.parametrize("previous", [SOURCE, REPEATED_SOURCE, HISTORY],
                         ids=["source", "repeated-ocr", "history"])
@pytest.mark.parametrize("stem", [
    NEXT,
    "已知两个数的商为 -5/4，被除数等于 5/2，求除数？",
], ids=["live-wording", "predicate-and-punctuation"])
def test_changed_numbers_and_benign_wording_are_allowed_with_math_context(previous, stem):
    value = candidate("calculation")
    value["stem_text"] = stem
    assert not _near_copy_stem(stem, previous, allow_numeric_changes=True)
    assert _valid_candidate(value, snapshot(previous))


@pytest.mark.parametrize("previous", [REPEATED_SOURCE, HISTORY])
@pytest.mark.parametrize("subject,override", [("英语", None), ("数学", "英语")])
def test_explicit_nonmath_context_keeps_wording_and_number_edits_strict(previous, subject, override):
    value = candidate("calculation")
    value["stem_text"] = NEXT
    assert _near_copy_stem(NEXT, previous)
    assert not _valid_candidate(value, snapshot(previous, subject), source_subject=override)


@pytest.mark.parametrize("previous,stem", [
    (REPEATED_SOURCE, "已知两个数的商为 -7.0/2，被除数等于 7/2，求除数？"),
    (HISTORY, "已知两数的商等于 -5/3，被除数为 10/3，求除数"),
    ("两数和是3", "两个数和为3"),
    ("两数和是3", "两个数和等于3.0"),
    ("两数和是3", "两个数和为+3"),
    ("两数和是0", "两个数和为-0.0"),
    ("两数和是-3", "两个数和为−3.0"),
])
def test_unchanged_numbers_with_only_wording_changes_remain_duplicates(previous, stem):
    value = candidate("calculation")
    value["stem_text"] = stem
    assert _near_copy_stem(stem, previous, allow_numeric_changes=True)
    assert not _valid_candidate(value, snapshot(previous))


@pytest.mark.parametrize("ending", ["", "。", ".", "?", "？"])
def test_terminal_punctuation_does_not_make_unchanged_short_math_new(ending):
    previous = "两数和是3。"
    assert _near_copy_stem("两个数和为3" + ending, previous, allow_numeric_changes=True)
    assert not _near_copy_stem("两个数和为4" + ending, previous, allow_numeric_changes=True)


@pytest.mark.parametrize("before,after", [
    ("-3", "3"), ("3", "-3"), ("0", "1"), ("1", "0"),
    ("-0.5", "0.5"), ("0.3", "3"), ("2/-5", "2/5"),
])
def test_sign_zero_and_decimal_changes_remain_distinct_numeric_values(before, after):
    previous, stem = "两数和是" + before, "两个数和为" + after
    old_template, old_numbers = _math_numeric_signature(previous)
    new_template, new_numbers = _math_numeric_signature(stem)
    assert new_template == old_template
    assert new_numbers != old_numbers
    assert not _near_copy_stem(stem, previous, allow_numeric_changes=True)


@pytest.mark.parametrize("previous,stem", [
    ("计算3+4", "计算5-6"),
    ("计算3×4", "计算5÷6"),
    ("计算3/4", "计算5*6"),
    ("计算a-3", "计算a+5"),
    ("计算(3+4)-2", "计算(5+6)+1"),
    ("两数和是3米", "两个数和为5厘米"),
    ("已知x>3，求x的范围。", "已知x<5，求x的范围？"),
    ("已知两个正数的和是3，求积。", "已知两个负数的和为5，求积？"),
])
def test_operations_units_and_conditions_are_not_collapsed_into_numeric_substitutions(previous, stem):
    assert _math_numeric_signature(stem)[0] != _math_numeric_signature(previous)[0]
    assert not _numeric_substitution(stem, previous)


def division_candidate(stem, expression):
    value = candidate("calculation")
    value.update(
        stem_text=stem, answer_values=["-2"],
        hint_text="想一想被除数、除数和商的关系。",
        solution_text=f"除数=被除数÷商，{expression}=-2。",
    )
    return value


def ready(flow):
    """Create the historical wording through the same synthetic generation flow."""
    flow.pipeline.stem = REPEATED_SOURCE
    case = source(flow, "calculation")
    task = queue(flow, case)
    value = division_candidate(HISTORY, "10/3÷(-5/3)")
    solved = verification(value)
    solved.update(answer_kind="number", solution_text="除数=被除数÷商。10/3×(-3/5)=-2。")
    client = Client([value, solved, solution_agreement()])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 3
    return case, task, client


def test_changed_numeric_variant_with_historical_wording_is_blind_verified_and_saved(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    case, historical_task, _ = ready(flow)
    task = queue(flow, case, regenerate_token="wording-next")
    assert task["job_id"] != historical_task["job_id"]
    generated = division_candidate(NEXT, "5/2÷(-5/4)")
    generated["answer_values"] = ["-2.0"]
    solved = verification(generated)
    solved.update(
        answer_kind="number", answer_values=["-2"],
        solution_text="除数=被除数÷商。5/2÷(-5/4)=5/2×(-4/5)=-2。答：除数是-2。",
    )
    client = Client([generated, solved, solution_agreement()])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 3 and client.outputs == []
    generation, blind, agreement = [json.loads(call["text"]) for call in client.calls]
    assert generation["source_question"]["stem_text"] == REPEATED_SOURCE
    assert generation["previous_questions_to_avoid"] == [HISTORY]
    assert generation["rejected_questions"] == []
    assert blind["stem_text"] == NEXT
    assert blind["source_curriculum"]["subject"] == "数学"
    for hidden in ("answer_values", "answer_kind", "field_answers", "solution_text", "hint_text",
                   "source_question", "previous_questions_to_avoid", "rejected_questions"):
        assert hidden not in blind
    assert generated["solution_text"] not in client.calls[1]["text"]
    assert generated["hint_text"] not in client.calls[1]["text"]
    assert agreement["answer_a"]["solution_text"] == generated["solution_text"]
    assert agreement["answer_b"]["solution_text"] == solved["solution_text"]

    status = get(flow, task)
    assert status["status"] == "ready"
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        assert row.failure_code is None and row.item_id == status["item_id"]
        assert [call["stage"] for call in row.audit["executions"][-1]["calls"]] == [
            "generate", "blind_solve", "answer_equivalence",
        ]
        item = session.get(PracticeItem, row.item_id)
        assert item.stem_text == NEXT and item.status == "ai_verified"
        assert item.answer_kind == "number" and item.answer_values == ["-2"]
        assert item.solution_text == solved["solution_text"]
        assert item.verification["status"] == "verified"
        assert item.generation["job_id"] == row.id
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 2

    begun = start(flow, task)
    assert begun.status_code == 201, begun.text
    activity = begun.json()
    assert activity["items"][0]["id"] == status["item_id"]
    assert "answer_values" not in activity["items"][0] and "solution_text" not in activity["items"][0]
    revealed = flow.client.post(
        f"/v1/practice-sessions/{activity['id']}/items/{status['item_id']}/solution", json={},
    )
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["answer_text"] == "-2"
    assert revealed.json()["solution_text"] == solved["solution_text"]
    assert len(client.calls) == 3


def test_historical_numbers_with_only_wording_changes_stop_before_blind_verification(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    case, _, _ = ready(flow)
    task = queue(flow, case, regenerate_token="wording-duplicate")
    generated = division_candidate(
        "已知两数的商为 -5/3，被除数等于 10/3，求除数？", "10/3÷(-5/3)",
    )
    # It differs from the source; only the persisted historical item blocks it.
    assert _valid_candidate(generated, snapshot(REPEATED_SOURCE))
    client = Client([generated])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 1
    assert json.loads(client.calls[0]["text"])["previous_questions_to_avoid"] == [HISTORY]
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        assert row.status == "failed" and row.item_id is None
        assert [call["stage"] for call in row.audit["executions"][-1]["calls"]] == ["generate"]
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 1


def test_zero_quotient_numeric_change_cannot_bypass_blind_unique_answer_check(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    flow.pipeline.stem = REPEATED_SOURCE
    task = queue(flow, source(flow, "calculation"))
    generated = candidate("calculation")
    generated.update(
        stem_text="已知两个数的商为0，被除数等于0，求除数？",
        answer_values=["0"], solution_text="除数=被除数÷商，0÷0=0。",
    )
    solved = verification(generated)
    solved.update(unique_answer=False, answer_values=["任意非零数"],
                  solution_text="零除以任意非零数都等于零，所以除数不唯一。", reason="条件无法确定唯一除数。")
    client = Client([generated, solved])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 2
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        assert row.item_id is None
        assert [call["stage"] for call in row.audit["executions"][-1]["calls"]] == ["generate", "blind_solve"]
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0
