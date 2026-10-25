"""An explicit reveal is durable, version-bound help, not retroactive assistance."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import review
from xueban_backend.correction_core import iso
from xueban_backend.correction_models import Entity
from xueban_backend.learning_rules import assistance
from xueban_backend.models import PrincipalRole


def answer_ready(flow, *, answer=True, answer_checks=False):
    original = flow.pipeline.check

    def check(**context):
        result = original(**context)
        if answer:
            result.data.update(correct_answer="2", correction_method="1 + 1 = 2")
        if answer_checks:
            result.data["answer_checks"] = [{"index": 1, "prompt": "1 + 1", "student_answer": "3",
                                              "verdict": "incorrect", "correct_answer": "2",
                                              "explanation": "1加1等于2。"}]
        return result

    flow.pipeline.check = check
    case = flow.ready()
    flow.start_check(case)
    assert flow.worker.run_once()
    return flow.client.get(f"/v1/cases/{case['id']}").json()


def binding(case):
    return {"attempt_id": case["current_attempt_id"],
            "source_decision_id": case["current_decision"]["id"],
            "source_decision_revision": case["current_decision"]["revision"]}


def test_solution_reveal_is_durable_and_deduplicated_across_request_keys(flow):
    case = answer_ready(flow)
    body = binding(case)
    path = f"/v1/cases/{case['id']}/solution-view"
    first = flow.post(path, body, "reveal-request-1")
    assert first.status_code == 200, first.text
    assert first.json() == {"case_id": case["id"], **body, "exposed": True}
    assert flow.post(path, body, "reveal-request-1").json() == first.json()
    assert flow.post(path, body, "reveal-request-2").json() == first.json()
    after = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert len(after["hint_ids"]) == 1
    hint = flow.client.get(f"/v1/hints/{after['hint_ids'][0]}").json()
    assert hint["first_shown_at"] and hint["exposes_answer"]
    assert hint["source_decision_id"] == body["source_decision_id"]
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == "hint")) == 1


def test_reveal_does_not_rewrite_original_independence_but_correction_inherits_help(flow):
    case = answer_ready(flow)
    assert flow.post(f"/v1/cases/{case['id']}/solution-view", binding(case)).status_code == 200
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    hint = flow.client.get(f"/v1/hints/{case['hint_ids'][0]}").json()
    original = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert assistance(original, [hint], "independent") == "independent"
    asset = flow.uploaded()
    result = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": original["id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": iso(), "reported_help": "none_reported",
    })
    assert result.status_code == 202, result.text
    next_case = flow.client.get(f"/v1/cases/{case['id']}").json()
    child = flow.client.get(f"/v1/attempts/{next_case['current_attempt_id']}").json()["record"]
    assert child["help_condition"] == "answer_shown"
    assert assistance(child, [hint], None) == "solution_seen"
    # A cached browser may not reveal the previous answer after a new attempt.
    assert flow.post(f"/v1/cases/{case['id']}/solution-view", binding(case)).status_code == 409


def test_reveal_rejects_stale_binding_missing_solution_and_unowned_student(flow):
    case = answer_ready(flow)
    path = f"/v1/cases/{case['id']}/solution-view"
    body = binding(case)
    assert flow.post(path, {**body, "source_decision_revision": 99}).status_code == 409
    with Session(flow.database.engine) as session:
        session.add(PrincipalRole(principal_id="actor_b", role="student"))
        session.commit()
    flow.client.headers["Authorization"] = "Bearer " + flow.signing.token("subject-b")
    assert flow.post(path, body).status_code == 404


def test_no_solution_does_not_create_a_reveal(flow):
    case = answer_ready(flow, answer=False)
    response = flow.post(f"/v1/cases/{case['id']}/solution-view", binding(case))
    assert response.status_code == 409
    assert flow.client.get(f"/v1/cases/{case['id']}").json()["hint_ids"] == []


def test_subquestion_solution_can_be_revealed_without_top_level_answer(flow):
    case = answer_ready(flow, answer=False, answer_checks=True)
    response = flow.post(f"/v1/cases/{case['id']}/solution-view", binding(case))
    assert response.status_code == 200, response.text


def test_reveal_cannot_mutate_an_attempt_while_a_new_check_is_running(flow):
    case = answer_ready(flow)
    teacher(flow)
    case = review(flow, case)
    job = flow.start_check(case)
    assert job["status"] == "queued"
    pending = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert pending["progress"] == "rechecking"
    response = flow.post(f"/v1/cases/{case['id']}/solution-view", binding(pending))
    assert response.status_code == 409
    assert flow.client.get(f"/v1/cases/{case['id']}").json()["hint_ids"] == []
