"""Restored decision policy with current transactions/contracts; no model calls."""
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401 -- real migrated DB/API fixture
from xueban_backend.correction_models import Entity, Task
from xueban_backend.worker import CorrectionWorker


def original_policy(pipeline, *, evidence_verified=True):
    pipeline.uses_original_decision_policy = True
    saved_check = pipeline.check

    def check(**context):
        result = saved_check(**context)
        return replace(result, data={**result.data, "evidence_verified": evidence_verified,
                                     "correct_answer": "2", "correction_method": "把两个1相加。",
                                     "knowledge_points": [], "uncertainties": []})

    pipeline.check = check


@pytest.mark.parametrize("use_original", [True, False])
def test_original_pipeline_keeps_verified_verdict_despite_initial_read_note(flow, use_original):
    case = flow.ready()
    if use_original:
        original_policy(flow.pipeline)
    with Session(flow.database.engine) as session, session.begin():
        attempt = session.get(Entity, case["current_attempt_id"])
        attempt.document = {**attempt.document, "recognition_uncertainties": ["首轮的格式疑点，交由第二轮核对。"]}
    job = flow.start_check(case)
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    current = flow.client.get(f"/v1/cases/{case['id']}").json()["current_decision"]
    assert current["verdict"] == ("incorrect" if use_original else "unable_to_judge")
    if use_original:
        assert current["correct_answer"] == "2"
        assert current["correction_method"] == "把两个1相加。"
        assert current["origin"] == "model" and current["validity"] == "candidate"


@pytest.mark.parametrize("verified,matched,expected", [
    (True, True, "incorrect"), (False, True, "unable_to_judge"),
    (True, False, "unable_to_judge"),
])
def test_original_policy_still_requires_evidence_and_target_binding(flow, verified, matched, expected):
    case = flow.ready()
    original_policy(flow.pipeline, evidence_verified=verified)
    with Session(flow.database.engine) as session, session.begin():
        attempt = session.get(Entity, case["current_attempt_id"])
        attempt.document = {**attempt.document, "recognition_status": "needs_revision",
                            "recognition_uncertainties": ["首轮识读疑点"]}
        attempt.annotations = {**(attempt.annotations or {}),
                               "target_binding": "matched_stem" if matched else "needs_staff"}
    flow.start_check(case)
    assert flow.worker.run_once()
    current = flow.client.get(f"/v1/cases/{case['id']}").json()["current_decision"]
    assert current["verdict"] == expected
    if expected == "unable_to_judge":
        assert current["correct_answer"] is None


def test_original_recognition_enqueues_and_finishes_every_question_check(flow):
    original_policy(flow.pipeline)
    flow.pipeline.extra_item_label = "2"
    worker = CorrectionWorker(flow.database.engine,
                              replace(flow.cfg, auto_check_after_recognition=True), flow.pipeline)
    _, recognition_job = flow.recognize()
    assert worker.run_once()
    assert flow.client.get(f"/v1/jobs/{recognition_job['id']}").json()["status"] == "succeeded"
    with Session(flow.database.engine) as session:
        jobs = list(session.scalars(select(Task).where(Task.homework_id == flow.homework_id,
                                                       Task.kind == "check")))
        assert len(jobs) == 2 and all(job.status == "queued" for job in jobs)
    assert worker.run_once()
    assert worker.run_once()
    assert not worker.run_once()
    for case_id in flow.homework()["case_ids"]:
        case = flow.client.get(f"/v1/cases/{case_id}").json()
        decision = case["current_decision"]
        assert decision["verdict"] == "incorrect"
        assert decision["correct_answer"] == "2"
        assert decision["knowledge_points"] == []
        worker.contracts.validate("Decision", decision)
    assert flow.pipeline.calls == ["recognize", "check", "check"]
