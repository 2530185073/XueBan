"""Regression of the evening audit: real routes/worker, deterministic model port."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import link, review, reviewed
from test_practice import clock  # noqa: F401
from test_student_variants import ready, start
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import AttemptCondition
from xueban_backend.learning_reports import build_snapshot, render_report
from xueban_backend.models import Student


def snapshot(flow, cadence="daily"):
    with Session(flow.database.engine) as session:
        return build_snapshot(session, session.get(Student, "student_a"),
                              date(2026, 10, 16), date(2026, 10, 18), cadence)


def page(snap, cadence="daily"):
    return render_report({"student_name": "测试学生", "corrections": [], "report": {
        "cadence": cadence, "snapshot": snap, "published_at": None, "version": 1,
        "staff_note": None, "next_step": None}})


def submit_new_answer(flow, case):
    asset = flow.uploaded()
    result = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "unknown"})
    assert result.status_code == 202, result.text
    return flow.client.get(f"/v1/cases/{case['id']}").json()


def check_new_answer(flow, case, verdict="correct"):
    assert flow.worker.run_once()  # recognize the actual submitted image
    flow.pipeline.verdict = verdict
    flow.start_check(flow.client.get(f"/v1/cases/{case['id']}").json())
    assert flow.worker.run_once()
    return flow.client.get(f"/v1/cases/{case['id']}").json()


def notebook(flow):
    response = flow.client.get("/v1/students/student_a/variant-notebook")
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.parametrize("initial", ["correct", "incorrect"])
def test_first_answer_confirmed_or_rejudged_correct_is_not_a_correction(flow, initial):
    case = reviewed(flow, initial)
    teacher(flow)
    review(flow, case, action="confirm" if initial == "correct" else "replace", verdict="correct")
    snap = snapshot(flow)
    assert snap["school_tasks"]["corrected_resolved"] == 0
    assert snap["school_tasks"]["correction_open"] == 0
    assert snap["mistakes"] == [] and snap["completions"] == []


@pytest.mark.parametrize("confirmed", [False, True])
def test_blank_is_not_an_error_but_stays_visible_as_work_to_complete(flow, confirmed):
    case = reviewed(flow, "unanswered")
    teacher(flow)
    if confirmed:
        case = review(flow, case)
    link(flow, case)
    knowledge = flow.client.get("/v1/students/student_a/knowledge").json()
    math = next(item for item in knowledge["subjects"] if item["subject"] == "数学")
    assert math["incorrect_count"] == math["scorable_count"] == 0
    assert math["score_percent"] is None
    snap = snapshot(flow)
    assert snap["mistakes"] == []
    [completion] = snap["completions"]
    assert completion["case_id"] == case["id"]
    assert completion["correction"] == "尚未作答，待完成"
    assert snap["school_tasks"]["correction_open"] == 0
    output = page(snap)
    assert "待完成与补做情况" in output and "尚未作答，待完成" in output
    assert "今天错的题和订正情况" not in output


@pytest.mark.parametrize("initial", ["unanswered", "incorrect"])
def test_new_answer_keeps_completion_separate_from_real_correction(flow, initial):
    case = reviewed(flow, initial)
    teacher(flow)
    case = review(flow, case)
    current = submit_new_answer(flow, case)
    before = snapshot(flow)
    target = "completions" if initial == "unanswered" else "mistakes"
    assert len(before[target]) == 1
    current = check_new_answer(flow, current)
    pending = snapshot(flow)
    assert pending["school_tasks"]["corrected_resolved"] == 0
    assert pending["school_tasks"]["ai_corrected_pending"] == int(initial == "incorrect")
    assert "待老师核实" in pending[target][0]["correction"]
    review(flow, current)
    final = snapshot(flow)
    assert final["school_tasks"]["corrected_resolved"] == int(initial == "incorrect")
    assert final["school_tasks"]["ai_corrected_pending"] == 0
    assert len(final[target]) == 1
    assert final[target][0]["correction"] == ("已补做，老师确认通过" if initial == "unanswered" else "已订正，老师确认通过")


def test_rejudged_error_cannot_return_as_a_correction_after_new_submission(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case, action="replace", verdict="correct")
    current = check_new_answer(flow, submit_new_answer(flow, case))
    review(flow, current)
    snap = snapshot(flow)
    assert snap["school_tasks"]["corrected_resolved"] == 0
    assert snap["mistakes"] == []


def test_wrong_card_survives_original_correction_wait_and_review_without_practice(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    [original] = notebook(flow)
    original_decision_id = case["current_decision"]["id"]
    current = submit_new_answer(flow, case)
    [waiting] = notebook(flow)
    assert waiting["case_id"] == original["case_id"]
    assert waiting["occurred_at"] == original["occurred_at"]
    assert waiting["original_status"] == "rechecking"
    assert waiting["status"] == "uncorrected" and not waiting["practiced"]
    assert waiting["generation_id"] is waiting["session_id"] is None
    current = check_new_answer(flow, current)
    [pending] = notebook(flow)
    assert pending["original_status"] == "awaiting_review"
    assert pending["status"] == "uncorrected" and not pending["practiced"]
    review(flow, current)
    assert notebook(flow) == []
    with Session(flow.database.engine) as session:
        assert session.get(Entity, original_decision_id).document["verdict"] == "incorrect"


def test_withdrawn_wrong_history_does_not_create_a_notebook_card(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case, action="replace", verdict="correct")
    submit_new_answer(flow, case)
    assert notebook(flow) == []


def test_original_correction_preserves_existing_variant_session_and_result(flow, clock):
    case, generated, _ = ready(flow)
    activity = start(flow, generated).json()
    answer = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": activity["items"][0]["id"], "answers": [{"field_id": "blank1", "answer_text": "8"}]})
    assert answer.status_code == 201, answer.text
    [before] = notebook(flow)
    assert before["status"] == "needs_practice" and before["practiced"]
    current = flow.client.get(f"/v1/cases/{case['id']}").json()
    submit_new_answer(flow, current)
    [after] = notebook(flow)
    assert after == {**before, "original_status": "rechecking"}


def test_blank_then_wrong_then_correct_records_the_real_error_correction(flow):
    case = reviewed(flow, "unanswered")
    teacher(flow)
    case = review(flow, case)
    wrong = check_new_answer(flow, submit_new_answer(flow, case), verdict="incorrect")
    wrong = review(flow, wrong)
    right = check_new_answer(flow, submit_new_answer(flow, wrong))
    review(flow, right)
    snap = snapshot(flow)
    assert snap["school_tasks"]["corrected_resolved"] == 1
    assert len(snap["mistakes"]) == 1 and snap["completions"] == []


def condition(flow, case, *, key="seen", level="solution_seen", stamp=None):
    with Session(flow.database.engine) as session, session.begin():
        session.add(AttemptCondition(id="condition_" + key, tenant_id="tenant_a", student_id="student_a",
            attempt_id=case["current_attempt_id"], assistance_level=level, actor_id="actor_a", note=None,
            created_at=stamp or datetime(2026, 10, 16, 10, tzinfo=timezone.utc)))


@pytest.mark.parametrize("cadence", ["daily", "half_month"])
def test_solution_seen_condition_counts_even_without_a_hint_and_renders(flow, cadence):
    case = reviewed(flow)
    condition(flow, case)
    snap = snapshot(flow, cadence)
    assert snap["help"]["answers_shown"] == 1 and snap["help"]["hints_viewed"] == 0
    assert "查看答案或完整解法 1 次" in page(snap, cadence)


def test_hint_and_repeated_solution_conditions_count_the_same_attempt_once(flow):
    case = reviewed(flow)
    condition(flow, case)
    condition(flow, case, key="seen-again", stamp=datetime(2026, 10, 16, 11, tzinfo=timezone.utc))
    with Session(flow.database.engine) as session, session.begin():
        session.add(Entity(id="shown_hint", homework_id=flow.homework_id, kind="hint", revision=1,
            document={"id": "shown_hint", "attempt_id": case["current_attempt_id"], "case_id": case["id"],
                      "first_shown_at": "2026-10-16T10:00:00Z", "exposes_answer": True}))
    assert snapshot(flow)["help"]["answers_shown"] == 1


def test_condition_date_and_later_staff_correction_do_not_leak_into_report(flow):
    case = reviewed(flow)
    condition(flow, case, stamp=datetime(2026, 10, 15, 10, tzinfo=timezone.utc))
    assert snapshot(flow)["help"]["answers_shown"] == 0
    condition(flow, case, key="seen-today")
    assert snapshot(flow)["help"]["answers_shown"] == 1
    condition(flow, case, key="corrected", level="independent", stamp=datetime(2026, 10, 16, 11, tzinfo=timezone.utc))
    assert snapshot(flow)["help"]["answers_shown"] == 0
