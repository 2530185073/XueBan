"""Answer-blind source reading; synthetic transport, never paid model calls."""
from copy import deepcopy
import json

import httpx
import pytest

from test_user_original_pipeline import (
    checked, context, isolated_factory, picture, recognition, request_task, source_reading,
)
from xueban_backend.providers.user_original import ProviderError


def test_blank_source_cannot_keep_a_solved_first_pass_answer(monkeypatch):
    page = recognition()
    page["items"][0].update(stem_text="写出这些数的倒数：2，-5。", question_type="calculation",
                            answer_text="1/2，-1/5")
    review = source_reading(page)
    review["readings"][0].update(source="empty", answer_text=None)
    flow, requests = isolated_factory(monkeypatch, [page], [review])
    with flow:
        result = flow.recognize(images=(picture(),))
    item = result.data["items"][0]
    assert item["answer_text"] is None and item["answer_status"] == "blank"
    assert item["recognition_status"] == "ready"
    sent = json.dumps(request_task(requests[1]), ensure_ascii=False)
    assert "1/2" not in sent and "-1/5" not in sent
    assert "answer_text" not in sent and "correct_answer" not in sent
    assert result.data["source_reading_audit"][0]["before"]["answer_text"] == "1/2，-1/5"
    assert len(result.metadata.attempts) == 2


def test_source_preserves_missing_minus_and_incorrect_steps(monkeypatch):
    page = recognition()
    page["items"][0].update(stem_text="计算(-12)÷5。", question_type="calculation",
                            answer_text="-12/5")
    review = source_reading(page)
    review["readings"][0]["answer_text"] = "=(-12)×1/5\n=12/5"
    flow, _ = isolated_factory(monkeypatch, [page], [review])
    with flow:
        result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["answer_text"] == "=(-12)×1/5\n=12/5"


def test_source_separates_sign_and_number_blanks_excludes_printed_example(monkeypatch):
    page = recognition()
    page["items"][0].update(
        stem_text="示例：(-8)÷(-2)=4。(-18)÷(-6)=____(18÷____)=____。",
        question_type="fill_blank", blank_count=2,
        answer_slots=[{"index": 1, "prompt": "符号及除数", "answer_text": "+；6", "answer_status": "present"},
                      {"index": 2, "prompt": "得数", "answer_text": "3", "answer_status": "present"}])
    review = source_reading(page)
    review["readings"][0].update(blank_count=3, answer_slots=[
        {"index": 1, "prompt": "结果符号", "answer_text": "+", "source": "handwritten"},
        {"index": 2, "prompt": "18÷____", "answer_text": "6", "source": "handwritten"},
        {"index": 3, "prompt": "得数", "answer_text": None, "source": "empty"},
    ])
    flow, _ = isolated_factory(monkeypatch, [page], [review])
    with flow:
        result = flow.recognize(images=(picture(),))
    item = result.data["items"][0]
    assert item["blank_count"] == 3
    assert [slot["answer_text"] for slot in item["answer_slots"]] == ["+", "6", None]
    assert item["answer_slots"][-1]["answer_status"] == "blank"
    assert "4" not in item["answer_text"]


def test_calculation_subparts_are_not_pretended_fillblank_fields(monkeypatch):
    page = recognition()
    page["items"][0].update(question_type="calculation")
    review = source_reading(page)
    review["readings"][0]["answer_slots"] = [
        {"index": 1, "prompt": "(1) 2+2", "answer_text": "4", "source": "handwritten"},
        {"index": 2, "prompt": "(2) 3+3", "answer_text": "6", "source": "handwritten"},
    ]
    flow, _ = isolated_factory(monkeypatch, [page], [review])
    with flow:
        result = flow.recognize(images=(picture(),))
    assert result.data["items"][0]["blank_count"] == 0
    assert len(result.data["items"][0]["answer_slots"]) == 2


def test_printed_table_example_is_removed_without_changing_student_values(monkeypatch):
    page = recognition()
    page["items"][0].update(question_type="fill_blank", blank_count=3, answer_slots=[
        {"index": index, "prompt": f"第{index}格", "answer_text": "4", "answer_status": "present"}
        for index in range(1, 4)])
    review = source_reading(page)
    review["readings"][0]["answer_slots"] = [
        {"index": 1, "prompt": "示范行", "answer_text": "4", "source": "printed_example"},
        {"index": 2, "prompt": "第一条横线", "answer_text": "-2", "source": "handwritten"},
        {"index": 3, "prompt": "第二条横线", "answer_text": None, "source": "empty"},
    ]
    flow, _ = isolated_factory(monkeypatch, [page], [review])
    with flow:
        result = flow.recognize(images=(picture(),))
    item = result.data["items"][0]
    assert item["blank_count"] == 2
    assert [slot["index"] for slot in item["answer_slots"]] == [1, 2]
    assert [slot["answer_text"] for slot in item["answer_slots"]] == ["-2", None]


def test_choice_source_does_not_inject_slot_number_into_answer(monkeypatch):
    page = recognition()
    page["items"][0].update(question_type="single_choice")
    review = source_reading(page)
    review["readings"][0].update(answer_text="D", answer_slots=[
        {"index": 1, "prompt": "选择", "answer_text": "D", "source": "handwritten"}])
    flow, _ = isolated_factory(monkeypatch, [page], [review])
    with flow:
        item = flow.recognize(images=(picture(),)).data["items"][0]
    assert item["answer_text"] == "D" and item["blank_count"] == 0


def test_judge_readback_can_omit_known_display_slot_number(monkeypatch):
    result = checked(verdict="correct", correct_answer=None, recognition_consistent=True)
    result["readback"]["answer_text"] = "D"
    result["answer_checks"] = [{"index": 1, "prompt": "选择", "student_answer": "D",
        "verdict": "correct", "correct_answer": "D", "explanation": "选择正确。"}]
    flow, _ = isolated_factory(monkeypatch, [result], [result])
    ctx = context()
    ctx["attempt"].update(answer_text="1. D", answer_slots=[
        {"index": 1, "prompt": "选择", "answer_text": "D", "answer_status": "present"}])
    with flow:
        data = flow.check(**ctx).data
    assert data["verdict"] == "correct"


@pytest.mark.parametrize("fault", ["binding", "blank_with_answer", "count", "order", "timeout"])
def test_failed_source_read_never_silently_accepts_first_pass(monkeypatch, fault):
    page = recognition()
    review = source_reading(page)
    if fault == "binding":
        review["readings"][0]["label"] = "9"
    elif fault == "blank_with_answer":
        review["readings"][0]["source"] = "empty"
    elif fault == "count":
        review["readings"][0]["blank_count"] = 2
    elif fault == "order":
        page["items"].append({**deepcopy(page["items"][0]), "label": "2"})
        review = source_reading(page)
        review["readings"].reverse()
    else:
        review = httpx.Response(503)
    flow, requests = isolated_factory(monkeypatch, [page], [review])
    with flow, pytest.raises(ProviderError) as failure:
        flow.recognize(images=(picture(),))
    assert len(requests) == len(failure.value.attempts) == 2


def test_judge_cannot_approve_transcript_when_source_answer_differs(monkeypatch):
    result = checked(verdict="correct", correct_answer=None, recognition_consistent=False)
    result["readback"].update(answer_text=None, answer_status="blank")
    flow, _ = isolated_factory(monkeypatch, [result], [result])
    ctx = context()
    original = deepcopy(ctx["attempt"])
    with flow:
        data = flow.check(**ctx).data
    assert data["verdict"] == "unable_to_judge"
    assert data["correct_answer"] is None and data["evidence_verified"] is False
    assert ctx["attempt"] == original
