"""Rule R1.1 on hand-built evidence: status, sufficiency and delayed verification."""
from datetime import datetime, timedelta, timezone

from xueban_backend.learning_rules import assistance, chapter_rollup, compute_state

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def ev(n, day, result="correct", independent=True, family=None, event_type="school_first_attempt", assistance="independent"):
    at = datetime(2026, 10, day, 10, tzinfo=timezone.utc)
    return {"id": f"evidence_{n}", "attempt_id": f"attempt_{n}", "occurred_at": at.isoformat(), "result": result,
            "independent": independent, "item_family_id": family or f"q{n}", "event_type": event_type,
            "assistance_level": assistance}


def test_no_evidence_is_no_judgment_not_weakness():
    state = compute_state("kp-x", [], [], NOW)
    assert state["learning_status"] == "no_judgment"
    assert state["evidence_sufficiency"] == "insufficient"


def test_single_error_needs_diagnosis_not_weak():
    state = compute_state("kp-x", [ev(1, 1, "incorrect")], [], NOW)
    assert state["learning_status"] == "needs_help_now"
    assert state["reason_codes"] == ["single_error_needs_diagnosis"]
    assert {"code": "cause_not_diagnosed", "count": 1} in state["open_questions"]


def test_assisted_correction_is_consolidating_not_stable():
    fix = ev(2, 1, "correct", independent=False, event_type="correction", assistance="direction")
    fix["occurred_at"] = datetime(2026, 10, 1, 11, tzinfo=timezone.utc).isoformat()
    rows = [ev(1, 1, "incorrect"), fix]
    state = compute_state("kp-x", rows, [], NOW)
    assert state["learning_status"] == "consolidating"
    assert state["reason_codes"] == ["corrected_with_help_awaiting_new_school_item"]
    assert state["independent_evidence_count"] == 1


def test_two_dates_two_items_24h_after_last_error_is_stable_and_delayed_after_7_days():
    rows = [ev(1, 1, "incorrect"), ev(2, 2), ev(3, 3)]
    state = compute_state("kp-x", rows, [], NOW)
    assert state["learning_status"] == "stable_for_now"
    assert state["delayed_verified_at"] is None
    state = compute_state("kp-x", rows + [ev(4, 10)], [], NOW)
    assert state["delayed_verified_at"].startswith("2026-10-10")


def test_same_item_family_or_same_day_is_not_stable():
    assert compute_state("kp-x", [ev(1, 2, family="f"), ev(2, 3, family="f")], [], NOW)["learning_status"] == "consolidating"
    same_day = [ev(1, 2), {**ev(2, 2), "occurred_at": datetime(2026, 10, 2, 14, tzinfo=timezone.utc).isoformat()}]
    assert compute_state("kp-x", same_day, [], NOW)["learning_status"] == "consolidating"


def test_new_error_after_stable_returns_to_needs_help_but_keeps_history():
    rows = [ev(1, 2), ev(2, 3), ev(3, 5, "incorrect")]
    state = compute_state("kp-x", rows, [], NOW)
    assert state["learning_status"] == "needs_help_now"
    assert state["valid_evidence_count"] == 3


def test_unverified_help_condition_is_open_question():
    rows = [ev(1, 2, independent=False, assistance="no_recorded_help")]
    state = compute_state("kp-x", rows, [], NOW)
    assert state["learning_status"] == "no_judgment"
    assert {"code": "independence_unverified", "count": 1} in state["open_questions"]


def test_chapter_rollup_counts_not_averages():
    nodes = [{"id": "ch", "node_type": "chapter", "parent_id": "root", "name": "第1章"},
             {"id": "a", "node_type": "concept", "parent_id": "ch", "name": "A"},
             {"id": "b", "node_type": "concept", "parent_id": "ch", "name": "B"}]
    rollup = chapter_rollup(nodes, {"a": {"learning_status": "consolidating"}})
    assert rollup == [{"chapter_id": "ch", "name": "第1章", "objectives": 2,
                       "status_counts": {"consolidating": 1, "no_judgment": 1}}]
    assert chapter_rollup(nodes, {}) == []


def test_help_counts_only_for_work_done_after_it_was_seen():
    first = {"id": "a1", "occurred_at": "2026-10-10T01:00:00Z", "help_condition": "hint_used"}
    fix = {"id": "a2", "occurred_at": "2026-10-10T01:10:00Z", "help_condition": "hint_used"}
    hints = [{"attempt_id": "a1", "level": "direction", "first_shown_at": "2026-10-10T01:05:00Z", "exposes_answer": False}]
    assert assistance(first, hints, None) == "unknown"
    assert assistance(fix, hints, None) == "direction"
    assert assistance({**first, "help_condition": "none_reported"}, [], None) == "no_recorded_help"
    assert assistance(first, hints, "independent") == "independent"
