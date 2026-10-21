"""Render saved report facts without assigning unfinished AI reads to staff."""
from copy import deepcopy

import pytest

from xueban_backend.learning_reports import _report_judgment_counts, render_report


def body(cadence, tasks):
    return {"student_name": "合成学生", "corrections": [], "report": {
        "cadence": cadence, "version": 1, "published_at": None, "staff_note": None, "next_step": None,
        "snapshot": {
            "period": {"start": "2026-10-01", "end_exclusive": "2026-10-16"},
            "coverage": {"partial_period": False, "first_seen_date": None, "observed_days": 1, "homeworks": 1},
            "school_tasks": tasks, "help": {"requests": 0, "by_kind": {}, "resolved": 0, "hints_viewed": 0, "answers_shown": 0, "staff_help": []},
            "focus": [], "steady": [], "unknown_current": [], "sufficient": False,
            "sections": {}, "limitations": "仅汇总记录", "mistakes": [],
        }}}


def tasks():
    return {"questions": 9, "staff_verified": 1, "awaiting_verification": 6, "not_judged": 2,
            "verdicts": {"correct": 1, "incorrect": 0, "partially_correct": 0, "unanswered": 0},
            "corrected_resolved": 0, "correction_open": 1, "ai_judged": 4,
            "ai_verdicts": {"correct": 2, "incorrect": 1, "partially_correct": 1, "unanswered": 0}}


@pytest.mark.parametrize("cadence", ["daily", "half_month"])
def test_current_counts_separate_ai_and_unfinished_without_mutation(cadence):
    source = body(cadence, tasks()); before = deepcopy(source)
    assert _report_judgment_counts(source["report"]["snapshot"]["school_tasks"]) == {"ai": 4, "unfinished": 4, "unclassified": 0}
    page = render_report(source)
    assert "<dt>AI 初判，未经老师核实</dt><dd>4 道</dd>" in page
    assert "<dt>尚未形成可用判定</dt><dd>4 道" in page
    assert "待老师核实" not in page and "<dt>待核实</dt>" not in page
    assert source == before


@pytest.mark.parametrize("cadence", ["daily", "half_month"])
@pytest.mark.parametrize("change", ["legacy", "missing_ai", "missing_verdicts", "mismatched_sum", "exceeds_awaiting"])
def test_old_or_inconsistent_counts_never_become_zero_ai(cadence, change):
    value = tasks()
    if change in ("legacy", "missing_ai"): value.pop("ai_judged")
    if change in ("legacy", "missing_verdicts"): value.pop("ai_verdicts")
    if change == "mismatched_sum": value["ai_judged"] = 3
    if change == "exceeds_awaiting": value.update(ai_judged=7, ai_verdicts={"correct":7,"incorrect":0,"partially_correct":0,"unanswered":0})
    source = body(cadence, value); before = deepcopy(source)
    assert _report_judgment_counts(value) == {"ai": None, "unfinished": 2, "unclassified": 6}
    page = render_report(source)
    assert "<dt>未分列的未核实记录</dt><dd>6 道" in page
    assert "这份记录未可靠分列有效 AI 初判与未完成识读" in page
    assert "<dt>尚无判定（已记录）</dt><dd>2 道" in page
    assert "<dt>AI 初判，未经老师核实</dt>" not in page and "待老师核实" not in page
    assert source == before


@pytest.mark.parametrize("cadence", ["daily", "half_month"])
def test_explicit_zero_is_not_absent(cadence):
    value = tasks(); value.update(ai_judged=0, ai_verdicts={"correct":0,"incorrect":0,"partially_correct":0,"unanswered":0})
    assert _report_judgment_counts(value) == {"ai":0,"unfinished":8,"unclassified":0}
    page = render_report(body(cadence, value))
    assert "<dt>AI 初判，未经老师核实</dt><dd>0 道</dd>" in page
    assert "<dt>尚未形成可用判定</dt><dd>8 道" in page
