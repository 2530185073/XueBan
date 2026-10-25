"""Simple math variants retain blind verification and existing source bindings."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import Client, candidate, get, queue, run, solution_agreement, source, start, verification
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import PracticeItem, VariantGeneration
from xueban_backend.models import Homework
from xueban_backend.practice_generation import GENERATE_PROMPT, VERIFY_PROMPT, _near_copy_stem, _valid_candidate, snapshot_current


KNOWN_SOURCE = "已知两数的商是-7/2，被除数是7/2，求除数。"
KNOWN_VARIANT = "已知两数的商是-5/3，被除数是10/3，求除数。"
WORD_SOURCE = "小明买了3本练习册，每本4元，共花多少元？"
WORD_VARIANT = "小明买了8本练习册，每本6元，共花多少元？"


@pytest.mark.parametrize("prompt", [GENERATE_PROMPT, VERIFY_PROMPT])
def test_generation_and_persisted_reference_prompts_use_child_readable_same_grade_steps(prompt):
    for instruction in ("小学或初中", "student_grade", "source_curriculum", "熟悉的词和短句", "同年级教材",
                        "陌生术语", "已知…求…", "简短应用计算题", "不能只给终答"):
        assert instruction in prompt
    for old_requirement in ("不能只替换数字", "必须改变条件组织", "只有原题纯为算式", "应改用缺项等式"):
        assert old_requirement not in prompt
    assert "数学题优先只换数字" in GENERATE_PROMPT
    assert "不强行改故事" in GENERATE_PROMPT


@pytest.mark.parametrize("subject,allowed", [("数学", True), ("math", True), (None, False), ("英语", False)])
def test_numeric_word_problem_edits_require_math_context_and_never_allow_exact_duplicates(subject, allowed):
    value = candidate("application")
    value["stem_text"] = WORD_VARIANT
    snapshot = {"question_type": "application", "question_kind": "solution",
                "source_curriculum": {"grade": 7, "subject": subject},
                "source_question": {"stem_text": WORD_SOURCE, "options": []}}
    assert _valid_candidate(value, snapshot) is allowed
    value["stem_text"] = WORD_SOURCE
    assert not _valid_candidate(value, snapshot)


@pytest.mark.parametrize("original,changed", [
    (KNOWN_SOURCE, KNOWN_VARIANT),
    (KNOWN_SOURCE, "已知两数的商是5/3，被除数是10/3，求除数。"),
    (KNOWN_SOURCE, "已知两数的商是7/2，被除数是7/2，求除数。"),
    (WORD_SOURCE, WORD_VARIANT),
    ("每本练习册2.5元，买4本共花多少元？", "每本练习册3.5元，买6本共花多少元？"),
    (KNOWN_SOURCE + "\n1. " + KNOWN_SOURCE, KNOWN_VARIANT),
])
def test_explicit_numeric_allowance_keeps_default_prose_filter_strict(original, changed):
    assert _near_copy_stem(changed, original)
    assert not _near_copy_stem(changed, original, allow_numeric_changes=True)


@pytest.mark.parametrize("unchanged", [
    WORD_SOURCE, " 小明买了 3 本练习册，每本 4 元，共花多少元？ ",
    "2. " + WORD_SOURCE, WORD_SOURCE.replace("3本", "3.0本"),
    WORD_SOURCE.replace("小明", "小红"),
])
def test_math_allowance_does_not_accept_unchanged_numbers_or_cosmetic_prose_edits(unchanged):
    assert _near_copy_stem(unchanged, WORD_SOURCE, allow_numeric_changes=True)


def test_missing_subject_allows_narrow_arithmetic_cues_but_explicit_other_subject_does_not():
    value = candidate("calculation")
    value["stem_text"] = KNOWN_VARIANT
    snapshot = {"question_type": "calculation", "question_kind": "solution",
                "source_curriculum": {"grade": 7, "subject": None, "publisher_code": None},
                "source_question": {"stem_text": KNOWN_SOURCE, "options": []}}
    assert _valid_candidate(value, snapshot)
    assert not _valid_candidate(value, snapshot, source_subject="英语")
    snapshot["source_curriculum"]["subject"] = "英语"
    assert not _valid_candidate(value, snapshot)


def test_missing_subject_does_not_infer_math_from_the_word_shop_and_numbers():
    value = candidate()
    value["stem_text"] = "已知商店9点开门，要求把这句话译成英文：____。"
    snapshot = {"question_type": "fill_blank", "question_kind": "fill_blank",
                "source_curriculum": {"grade": 7, "subject": None},
                "source_question": {"stem_text": "已知商店8点开门，要求把这句话译成英文：____。", "options": []}}
    assert not _valid_candidate(value, snapshot)


def test_math_numeric_choice_can_keep_valid_options_but_reordering_a_duplicate_is_rejected():
    value = candidate("single_choice")
    snapshot = {"question_type": "single_choice", "question_kind": "choice", "source_curriculum": {"subject": "数学"},
                "source_question": {"stem_text": "6+1等于几？", "options": deepcopy(value["options"])}}
    assert _valid_candidate(value, snapshot)
    value["stem_text"] = snapshot["source_question"]["stem_text"]
    value["options"].reverse()
    assert not _valid_candidate(value, snapshot)


@pytest.mark.parametrize("subject_context", ["curriculum", "question", "arithmetic"])
def test_grade_and_subject_reach_blind_solve_without_changing_snapshot_or_leaking_answers(flow, subject_context):
    case = source(flow, "calculation")
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "stem_text": KNOWN_SOURCE,
                             "subject": "数学" if subject_context == "question" else None}
        homework = session.get(Homework, flow.homework_id)
        homework.curriculum = {**homework.curriculum, "subject": "数学" if subject_context == "curriculum" else None,
                               "publisher_code": "BNUP" if subject_context == "curriculum" else None}
        session.commit()
    task = queue(flow, case)
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        old_snapshot, old_fingerprint = deepcopy(row.snapshot), row.source_fingerprint
        assert "student_grade" not in old_snapshot and "source_subject" not in old_snapshot
        assert "grade" not in old_snapshot and old_snapshot["source_curriculum"]["grade"] == 7
    generated = candidate("calculation")
    generated.update(stem_text=KNOWN_VARIANT, answer_values=["-2"],
                     solution_text="除数=被除数÷商，10/3÷(-5/3)=-2。", hint_text="想一想被除数、除数和商的关系。")
    solved = verification(generated)
    solved.update(answer_kind="number", solution_text="除数=被除数÷商。10/3÷(-5/3)=10/3×(-3/5)=-2。答：除数是-2。")
    client = Client([generated, solved, solution_agreement()])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 3
    generation_input, blind = (json.loads(call["text"]) for call in client.calls[:2])
    for payload in (generation_input, blind):
        assert payload["student_grade"] == 7
        assert payload["source_curriculum"] == old_snapshot["source_curriculum"]
        assert payload["source_subject"] == ("数学" if subject_context == "question" else None)
    assert set(blind) == {"stem_text", "options", "answer_fields", "question_type", "question_kind",
                          "knowledge_target", "requested_question_type", "student_grade", "source_curriculum", "source_subject"}
    assert generated["solution_text"] not in client.calls[1]["text"]
    assert generated["hint_text"] not in client.calls[1]["text"]
    assert client.calls[0]["instructions"] == GENERATE_PROMPT
    assert client.calls[1]["instructions"] == VERIFY_PROMPT
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        assert row.snapshot == old_snapshot and row.source_fingerprint == old_fingerprint
        assert snapshot_current(session, row)
        item = session.get(PracticeItem, row.item_id)
        assert item.answer_values == solved["answer_values"] and item.solution_text == solved["solution_text"]
    activity = start(flow, task).json()
    assert "solution_text" not in activity["items"][0] and "answer_values" not in activity["items"][0]
    assert get(flow, task)["status"] == "ready"
    assert start(flow, task).json()["id"] == activity["id"]


def test_numeric_word_problem_regenerations_pass_but_source_and_history_duplicates_stop_before_blind_solve(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    case = source(flow, "application")
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "stem_text": WORD_SOURCE}
        session.commit()
    first = candidate("application")
    first.update(stem_text=WORD_VARIANT, answer_values=["48"], solution_text="8×6=48（元）。")
    second = candidate("application")
    second.update(stem_text="小明买了5本练习册，每本7元，共花多少元？", answer_values=["35"], solution_text="5×7=35（元）。")
    for index, value in enumerate((first, second)):
        task = queue(flow, case, regenerate_token=f"numeric-{index}")
        client = Client([value, verification(value), solution_agreement()])
        assert run(flow, task, client)["status"] == "ready"
        assert len(client.calls) == 3
        if index:
            assert first["stem_text"] in json.loads(client.calls[0]["text"])["previous_questions_to_avoid"]
            assert "previous_questions_to_avoid" not in json.loads(client.calls[1]["text"])
    for index, stem in enumerate((WORD_SOURCE, first["stem_text"], second["stem_text"])):
        duplicate = {**first, "stem_text": stem}
        task = queue(flow, case, regenerate_token=f"duplicate-{index}")
        client = Client([duplicate])
        assert run(flow, task, client)["failure_code"] == "verification_failed"
        assert len(client.calls) == 1
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 2
