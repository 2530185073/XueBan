"""Original image revisions through HTTP + migrated DB + durable worker; no network."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_multimodal_worker import assessment, current_case, enable_page_assessments, submit_correction
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Entity, EntitySnapshot, Task
from xueban_backend.providers import ProviderError
from xueban_backend.providers.correction_pipeline import PipelineResult


def attempt_for(flow, case):
    return flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]


def request_body(flow, case):
    attempt = attempt_for(flow, case)
    return {"expected_revision": case["revision"], "attempt_id": attempt["id"],
            "question_revision": case["current_question_revision"],
            "attempt_recognition_revision": attempt["recognition_revision"]}


def recheck(flow, case, key=None):
    return flow.post(f"/v1/cases/{case['id']}/recognition-rechecks", request_body(flow, case), key)


def recovery(flow, case):
    response = flow.client.get(f"/v1/cases/{case['id']}/recognition-rechecks/status")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def recovery_flow(flow):
    def pending(items):
        for item in items:
            item.update(answer_text=None, answer_status="unclear", recognition_status="needs_revision",
                        uncertainties=["上次核对服务未完成，原文待核实。"])
            item["assessment"] = assessment("unable_to_judge", "degraded")
            item["assessment"]["verification"]["reason_codes"] = ["provider_unavailable"]
    enable_page_assessments(flow, pending)
    case = flow.ready()
    control = SimpleNamespace(error=None, callback=None, change={}, calls=[], last=None)
    def reread(**context):
        control.calls.append(deepcopy({key: value for key, value in context.items() if key != "images"}))
        if control.callback:
            control.callback()
        if control.error:
            raise control.error
        image = context["images"][0]
        row = {"asset_id": image.asset_id, "label": "1", "stem_text": "1 + 1 = ?",
               "question_type": "calculation", "options": [], "blank_count": 0,
               "answer_text": "2", "answer_status": "present", "recognition_status": "ready",
               "regions": [{"asset_id": image.asset_id, "x": 0, "y": 0, "width": image.width_px,
                            "height": image.height_px, "coordinate_space": "original_pixels"}],
               "answer_regions": [], "uncertainties": [], "assessment": assessment("correct")}
        row.update(deepcopy(control.change))
        control.last = PipelineResult({"items": [row], "multimodel_audit": {"reads": ["synthetic-independent-1", "synthetic-independent-2"]}}, flow.pipeline.metadata)
        return control.last
    flow.pipeline.rerecognize_original = reread
    return flow, case, control


def test_recovery_keeps_attempt_photo_time_and_history_but_replaces_recognition(recovery_flow):
    flow, case, control = recovery_flow
    old = attempt_for(flow, case)
    old_decision = case["current_decision"]
    response = recheck(flow, case, "recovery-once")
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert response.json()["recovery"]["active_job_id"] == job["id"]
    assert flow.worker.run_once()
    current = current_case(flow, case["id"])
    new = attempt_for(flow, current)
    for field in ("id", "asset_ids", "occurred_at", "created_at", "parent_attempt_id", "root_attempt_id", "help_condition", "captured_question_revision"):
        assert new[field] == old[field]
    assert new["recognition_revision"] == old["recognition_revision"] + 1
    assert new["answer_text"] == "2" and new["recognition_status"] == "ready"
    assert new["recognition_uncertainties"] == []
    assert len(current["attempt_ids"]) == 1
    decision = current["current_decision"]
    assert decision["verdict"] == "correct" and decision["origin"] == "model"
    assert decision["validity"] == "candidate" and decision["confirmed_by"] is None
    assert decision["input_versions"]["attempt_recognition_revision"] == new["recognition_revision"]
    assert decision["input_versions"]["question_revision"] == current["current_question_revision"] == 2
    assert recovery(flow, current)["last_outcome"] == "applied"
    with Session(flow.database.engine) as session:
        assert session.get(EntitySnapshot, (old["id"], old["revision"])).document == old
        assert session.get(Entity, old_decision["id"]).document["validity"] == "stale"
        task = session.get(Task, job["id"])
        assert task.audit["recheck_dispatched"] is True
        assert task.audit["executions"][0]["candidate"]["items"][0]["answer_text"] == "2"
        assert task.inputs["input_versions"]["attempt_recognition_revision"] == 1
        assert task.audit["recovery_application"]["after"]["attempt_recognition_revision"] == 2
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == "attempt")) == 1
    assert len(control.calls) == 1


@pytest.mark.parametrize("change,outcome", [
    ({"stem_text": "1 + 2 = ?"}, "target_unconfirmed"),
    ({"options": [{"label": "A", "text": "改变条件"}]}, "target_unconfirmed"),
    ({"answer_status": "unclear", "recognition_status": "needs_revision", "uncertainties": ["确有遮挡"]}, "unchanged_pending"),
    ({"assessment": assessment("correct", "degraded")}, "unchanged_pending"),
])
def test_unconfirmed_and_unclear_recovery_never_clears_old_notes(recovery_flow, change, outcome):
    flow, case, control = recovery_flow
    original = attempt_for(flow, case)
    control.change = change
    response = recheck(flow, case)
    assert response.status_code == 202
    assert flow.worker.run_once()
    assert attempt_for(flow, current_case(flow, case["id"])) == original
    assert recovery(flow, case)["last_outcome"] == outcome
    assert current_case(flow, case["id"])["current_decision"]["verdict"] == "unable_to_judge"


def test_budget_counts_dispatch_not_keys_or_versions_and_never_auto_retries(recovery_flow):
    flow, case, control = recovery_flow
    control.error = ProviderError("upstream_unavailable")
    path = f"/v1/cases/{case['id']}/recognition-rechecks"
    body = request_body(flow, case)
    first = flow.post(path, body, "bounded-recovery")
    assert first.status_code == 202
    assert flow.post(path, body, "bounded-recovery").json() == first.json()
    active_case = current_case(flow, case["id"])
    assert recheck(flow, active_case).json()["job"]["id"] == first.json()["job"]["id"]
    assert flow.worker.run_once()
    assert not flow.worker.run_once()
    first_job = flow.client.get(f"/v1/jobs/{first.json()['job']['id']}").json()
    assert first_job["status"] == "failed" and first_job["failure"]["retryable"] is False
    assert recovery(flow, case)["used"] == 1
    second = recheck(flow, current_case(flow, case["id"]))
    assert second.status_code == 202
    assert flow.worker.run_once()
    blocked = recheck(flow, current_case(flow, case["id"]))
    assert blocked.status_code == 409 and blocked.json()["code"] == "recognition_recheck_budget_exhausted"
    assert len(control.calls) == 2 and recovery(flow, case)["remaining"] == 0
    changed = {**body, "question_revision": 999}
    assert flow.post(path, changed, "bounded-recovery").json()["code"] == "idempotency_conflict"


def test_old_inflight_check_preserves_paid_audit_without_overwriting_recovery(recovery_flow):
    flow, case, control = recovery_flow
    job = flow.start_check(case)
    old_id, old_token = flow.worker.claim()
    _, context = flow.worker._load(old_id, old_token)
    old_result = flow.pipeline.check(**context)
    accepted = recheck(flow, current_case(flow, case["id"]))
    assert accepted.status_code == 202
    assert flow.worker.finish(old_id, old_token, result=old_result)
    with Session(flow.database.engine) as session:
        previous = session.get(Task, job["id"])
        assert previous.status == "superseded"
        assert previous.audit["executions"][0]["metadata"]
        assert previous.audit["executions"][0]["candidate"]["verdict"] == "incorrect"
    assert flow.worker.run_once()
    assert current_case(flow, case["id"])["current_decision"]["verdict"] == "correct"


def test_dispatched_expired_lease_is_not_reclaimed_for_another_paid_call(recovery_flow):
    flow, case, control = recovery_flow
    accepted = recheck(flow, case).json()
    identifier, token = flow.worker.claim()
    _, context = flow.worker._load(identifier, token)
    assert flow.worker._dispatch_original_recheck(identifier, token)
    result = flow.pipeline.rerecognize_original(**context)
    with Session(flow.database.engine) as session:
        session.get(Task, identifier).lease_expires_at = now() - timedelta(seconds=1)
        session.commit()
    assert flow.worker.claim() is None
    assert not flow.worker.finish(identifier, token, result=result)
    assert len(control.calls) == 1
    with Session(flow.database.engine) as session:
        task = session.get(Task, identifier)
        assert task.status == "failed" and task.tries == 1
        assert task.audit["recovery_outcome"] == "execution_unknown"
        late = task.audit["executions"][0]["late_return"]
        assert late["applied"] is False and late["metadata"]
    assert attempt_for(flow, case)["recognition_revision"] == 1


def test_pause_does_not_change_attempt_or_discard_valid_recovery(recovery_flow):
    flow, case, control = recovery_flow
    def pause():
        row = current_case(flow, case["id"])
        response = flow.post(f"/v1/cases/{case['id']}/progress", {"expected_revision": row["revision"], "action": "pause"})
        assert response.status_code == 200
    control.callback = pause
    assert recheck(flow, case).status_code == 202
    assert flow.worker.run_once()
    assert current_case(flow, case["id"])["progress"] == "paused"
    assert recovery(flow, case)["last_outcome"] == "applied"


def test_new_student_attempt_supersedes_recovery_and_keeps_its_audit(recovery_flow):
    flow, case, control = recovery_flow
    old = attempt_for(flow, case)
    control.callback = lambda: submit_correction(flow, current_case(flow, case["id"]))
    job = recheck(flow, case).json()["job"]
    assert flow.worker.run_once()
    current = current_case(flow, case["id"])
    assert current["current_attempt_id"] != old["id"]
    assert flow.client.get(f"/v1/attempts/{old['id']}").json()["record"]["recognition_revision"] == 1
    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        assert task.status == "superseded" and task.audit["executions"][0]["candidate"]


def test_double_finish_never_adds_revision_or_changes_success_to_unknown(recovery_flow):
    flow, case, control = recovery_flow
    job = recheck(flow, case).json()["job"]
    identifier, token = flow.worker.claim()
    _, context = flow.worker._load(identifier, token)
    assert flow.worker._dispatch_original_recheck(identifier, token)
    result = flow.pipeline.rerecognize_original(**context)
    assert flow.worker.finish(identifier, token, result=result)
    assert not flow.worker.finish(identifier, token, result=result)
    assert attempt_for(flow, case)["recognition_revision"] == 2
    assert recovery(flow, case)["last_outcome"] == "applied"


def test_recovery_replaces_learning_evidence_instead_of_counting_a_second_attempt(recovery_flow):
    from datetime import date
    from test_correction_flow import teacher
    from test_learning import prepare_latest_math_curriculum, link
    from xueban_backend.learning_models import LearningEvidence
    from xueban_backend.learning_projection import project_student
    from xueban_backend.learning_reports import build_snapshot
    from xueban_backend.models import Student
    flow, case, control = recovery_flow
    prepare_latest_math_curriculum(flow)
    teacher(flow)
    # Seed a formerly usable AI observation, then retain it in the evidence
    # ledger while this same attempt's transcript is flagged for recovery.
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "subject": "数学", "recognition_uncertainties": []}
        attempt = session.get(Entity, case["current_attempt_id"])
        attempt.document = {**attempt.document, "answer_text": "3", "answer_status": "present",
                            "recognition_status": "ready", "recognition_uncertainties": []}
        decision = session.get(Entity, case["current_decision"]["id"])
        decision.document = {**decision.document, "verdict": "incorrect", "validity": "candidate", "reason_code": None,
                             "uncertainties": [], "knowledge_points": ["有理数的加减法"],
                             "verification": {"mode": "two_model", "status": "matched", "independent_readback": True, "reason_codes": []}}
        stored_case = session.get(Entity, case["id"])
        stored_case.document = {**stored_case.document, "current_decision": decision.document}
        session.commit()
    link(flow, case)
    with Session(flow.database.engine) as session:
        project_student(session, "tenant_a", "student_a")
        session.commit()
        old = session.scalar(select(LearningEvidence).where(LearningEvidence.validity == "valid"))
        assert old is not None and old.document["result"] == "incorrect"
        old_id = old.id
        attempt = session.get(Entity, case["current_attempt_id"])
        attempt.document = {**attempt.document, "recognition_status": "needs_revision",
                            "recognition_uncertainties": ["此处原文需要重新核对"]}
        session.commit()
    control.change = {"assessment": {**assessment("correct"), "knowledge_points": ["有理数的加减法"]}}
    assert recheck(flow, current_case(flow, case["id"])).status_code == 202
    assert flow.worker.run_once()
    with Session(flow.database.engine) as session:
        assert session.get(LearningEvidence, old_id).validity == "invalidated"
        valid = list(session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")))
        assert len(valid) == 1
        evidence = valid[0].document
        assert evidence["attempt_id"] == case["current_attempt_id"]
        assert evidence["event_type"] == "school_first_attempt" and evidence["result"] == "correct"
        assert evidence["independent"] is False
        snapshot = build_snapshot(session, session.get(Student, "student_a"), date(2026, 10, 16), date(2026, 10, 17), "daily")
        assert snapshot["school_tasks"]["ai_corrected_pending"] == 0
        assert snapshot["school_tasks"]["corrected_resolved"] == 0


def test_commit_failure_rolls_back_attempt_revision_and_decision(recovery_flow, monkeypatch):
    flow, case, control = recovery_flow
    original = attempt_for(flow, case)
    job = recheck(flow, case).json()["job"]
    original_apply = flow.worker._apply_page_assessment
    def fail_after_application(*args, **kwargs):
        original_apply(*args, **kwargs)
        raise RuntimeError("synthetic transaction failure")
    monkeypatch.setattr(flow.worker, "_apply_page_assessment", fail_after_application)
    assert flow.worker.run_once()
    assert attempt_for(flow, case) == original
    with Session(flow.database.engine) as session:
        assert session.get(Task, job["id"]).status == "failed"
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == "decision")) == 1
    assert recovery(flow, case)["used"] == 1


def test_staff_revision_and_out_of_bounds_regions_cannot_apply(recovery_flow):
    from test_correction_flow import teacher
    flow, case, control = recovery_flow
    teacher(flow)
    def staff_revise():
        original = attempt_for(flow, case)
        response = flow.post(f"/v1/attempts/{original['id']}/recognition-revisions",
                             {"expected_revision": original["revision"], "answer_text": "老师核对后的原文",
                              "answer_regions": [], "reason": "合成审核"})
        assert response.status_code == 201, response.text
    control.callback = staff_revise
    job = recheck(flow, case).json()["job"]
    assert flow.worker.run_once()
    assert attempt_for(flow, case)["answer_text"] == "老师核对后的原文"
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "superseded"


def test_distinct_concurrent_keys_create_at_most_one_recovery_job(recovery_flow):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from xueban_backend.app import create_app
    flow, case, control = recovery_flow
    body = request_body(flow, case)
    barrier = Barrier(2)
    def submit(index):
        app = create_app(flow.cfg)
        with TestClient(app) as client:
            barrier.wait(timeout=5)
            response = client.post(f"/v1/cases/{case['id']}/recognition-rechecks", json=body,
                headers={"Authorization": "Bearer " + flow.signing.token(), "Idempotency-Key": f"recovery-concurrent-{index}"})
        app.state.engine.dispose()
        return response
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, (1, 2)))
    codes = sorted(row.status_code for row in responses)
    assert codes == [202, 409] or flow.database.dialect == "sqlite" and codes == [202, 503]
    for row in responses:
        if row.status_code == 503:
            assert row.json()["code"] == "service_unavailable"
    # A SQLite write-contention 503 is an unknown submission, not permission
    # to change the key or launch another task. Read back the active server job.
    assert recovery(flow, case)["active_job_id"] is not None
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(Task).where(Task.kind == "recognition_recheck")) == 1
    assert flow.worker.run_once()
    assert len(control.calls) == 1


def test_recovery_endpoint_does_not_accept_transcripts_or_cross_student_access(recovery_flow):
    flow, case, _ = recovery_flow
    path = f"/v1/cases/{case['id']}/recognition-rechecks"
    body = request_body(flow, case)
    assert flow.post(path, {**body, "answer_text": "自己声明正确"}).status_code == 422
    other_headers = {"Authorization": "Bearer " + flow.signing.token("subject-b"), "Idempotency-Key": "not-your-original"}
    assert flow.client.post(path, json=body, headers=other_headers).status_code == 404
    assert flow.client.get(path + "/status", headers=other_headers).status_code == 404
    assert recovery(flow, case)["used"] == 0


def test_out_of_bounds_answer_region_cannot_change_saved_attempt(recovery_flow):
    flow, case, control = recovery_flow
    original = attempt_for(flow, case)
    control.change = {"answer_regions": [{"asset_id": original["asset_ids"][0], "x": 0, "y": 0,
                                          "width": 99999, "height": 99999, "coordinate_space": "original_pixels"}]}
    job = recheck(flow, case).json()["job"]
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "failed"
    assert attempt_for(flow, case) == original


def test_complete_blank_is_not_confused_with_missing_transcription(recovery_flow):
    flow, case, control = recovery_flow
    control.change = {"answer_text": None, "answer_status": "blank", "assessment": assessment("unanswered")}
    assert recheck(flow, case).status_code == 202
    assert flow.worker.run_once()
    attempt = attempt_for(flow, case)
    assert attempt["answer_status"] == "blank" and attempt["recognition_status"] == "ready"
    assert current_case(flow, case["id"])["current_decision"]["verdict"] == "unanswered"


def test_question_unit_blank_count_change_requires_scope_repair(recovery_flow):
    flow, case, control = recovery_flow
    context = {"section": "1", "row": None, "column": None, "anchor": "1 + 1"}
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "blank_count": 2}
        question.annotations = {**question.annotations, "target_unit": "question", "target_context": context, "unit_complete": True}
        session.commit()
    control.change = {"target_unit": "question", "blank_count": 1, "unit_complete": True, "target_context": context}
    assert recheck(flow, case).status_code == 202
    assert flow.worker.run_once()
    assert recovery(flow, case)["last_outcome"] == "scope_change_required"
    assert attempt_for(flow, case)["recognition_revision"] == 1


@pytest.mark.parametrize("unit", ["answer_slot", "question"])
@pytest.mark.parametrize("change", [
    {"label": "2"},
    {"label": "2", "visible_locator": {"printed_label": "2", "page_zone": "top"}},
    {"label": "1", "visible_locator": {"printed_label": "2", "page_zone": "top"}},
])
def test_recovery_rejects_explicit_other_question_number_even_if_initial_read_missed_it(
        recovery_flow, unit, change):
    flow, case, control = recovery_flow
    original = attempt_for(flow, case)
    context = {"section": "计算", "row": None, "column": None, "anchor": "1 + 1"}
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.annotations = {**question.annotations, "label": "1", "target_unit": unit}
        if unit == "question":
            question.annotations.update(target_context=context, unit_complete=True)
        session.commit()
    control.change = {"target_unit": unit, **change}
    if unit == "question":
        control.change.update(target_context=context, unit_complete=True)
    job = recheck(flow, case).json()["job"]
    assert flow.worker.run_once()
    assert recovery(flow, case)["last_outcome"] == "target_unconfirmed"
    assert attempt_for(flow, case) == original
    assert current_case(flow, case["id"])["current_decision"]["verdict"] == "unable_to_judge"
    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        assert task.audit["executions"][0]["candidate"]["items"][0]["label"] == change["label"]


@pytest.mark.parametrize("unit", ["answer_slot", "question"])
@pytest.mark.parametrize("old_label,new_label,old_print,new_print,accepted", [
    ("1", "2", None, None, False),
    ("1", "1", None, "2", False),
    (None, "2", "1", None, False),
    (None, None, "1", "2", False),
    ("3(1)", "3(2)", None, None, False),
    ("3.(1)", "3(1)", None, None, True),
    ("3(1)", "3.(1)", "3. (1)", "3(1)", True),
    ("3(1)", "3(1)", "3(1)", "3.(2)", False),
    ("3(1)", "3", None, "3、计算", True),
    ("一(1)", "一、列竖式计算", None, None, True),
    (None, None, None, None, True),
    ("3(1)", None, None, None, True),
])
def test_recovery_number_veto_preserves_format_missing_and_parent_boundaries(
        unit, old_label, new_label, old_print, new_print, accepted):
    from xueban_backend.recognition_recheck import matching_item
    context = {"section": "计算", "row": None, "column": None, "anchor": "1 + 1"}
    annotations = {"target_unit": unit, "label": old_label}
    candidate = {"stem_text": "1 + 1 = ?", "options": [], "label": new_label,
                 "asset_id": "original-photo", "target_unit": unit, "blank_count": 0,
                 "answer_text": "2", "answer_status": "present"}
    if unit == "question":
        annotations.update(target_context=context, unit_complete=True)
        candidate.update(target_context=context, unit_complete=True)
    if old_print is not None:
        annotations["visible_locator"] = {"printed_label": old_print, "page_zone": "top"}
    if new_print is not None:
        candidate["visible_locator"] = {"printed_label": new_print, "page_zone": "top"}
    question = SimpleNamespace(document={"stem_text": "1 + 1 = ?", "options": [], "blank_count": 0},
                               annotations=annotations)
    attempt = SimpleNamespace(document={"asset_ids": ["original-photo"]})
    before = deepcopy((question.document, question.annotations, candidate))
    # Only this question was archived: rejection must not rely on duplicate
    # detection. For accepted missing/parent labels, exact full print, source
    # image and unit scope still bind; numbering alone proves nothing.
    item, reason = matching_item(question, attempt, [candidate], [question])
    assert (item is candidate) == accepted
    assert reason == (None if accepted else "target_unconfirmed")
    assert (question.document, question.annotations, candidate) == before
