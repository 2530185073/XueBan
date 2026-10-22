"""R1 attribution, late semantic batches and reports; synthetic ports only."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import link, prepare_latest_math_curriculum
from test_learning_mapping_batches import BatchClient, queue
from test_learning_mapping_versions import _current_links
from test_multimodal_worker import assessment, current_case, submit_correction
from test_recognition_recheck import attempt_for, recheck, recovery, recovery_flow  # noqa: F401
from xueban_backend.correction_models import Entity, Task
from xueban_backend.learning_jobs import LearningWorker
from xueban_backend.learning_mapping import inherit_staff_links_for_unchanged_question, map_model_batch
from xueban_backend.learning_link_scope import observation_binding
from xueban_backend.learning_models import LearningEvidence, LearningOutbox, LearningReport, QuestionKnowledgeLink
from xueban_backend.learning_projection import dirty_students, project_student
from xueban_backend.learning_reports import bounds, build_snapshot, local_day, refresh
from xueban_backend.models import Homework, Student
from xueban_backend.worker import verification_summary

STEM = "(-6÷3)+5=?"
OLD_POINT, NEW_POINT = "kp-rational-add-sub", "kp-rational-mul-div"


def observed_case(recovery_flow, labels=None):
    """Seed the former machine reading, keeping the actual R1 transaction real."""
    flow, case, control = recovery_flow
    prepare_latest_math_curriculum(flow)
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "subject": "数学", "stem_text": STEM,
                             "recognition_uncertainties": []}
        attempt = session.get(Entity, case["current_attempt_id"])
        attempt.document = {**attempt.document, "answer_text": "(-2)+5=1", "answer_status": "present",
                            "recognition_status": "ready", "recognition_uncertainties": []}
        decision = session.get(Entity, case["current_decision"]["id"])
        decision.document = {**decision.document, "verdict": "incorrect", "validity": "candidate",
                             "reason_code": None, "uncertainties": [],
                             "knowledge_points": ["有理数的加减法"] if labels is None else labels,
                             "verification": {"mode": "two_model", "status": "matched",
                                              "independent_readback": True, "reason_codes": []}}
        saved = session.get(Entity, case["id"])
        saved.document = {**saved.document, "current_decision": decision.document, "progress": "needs_correction"}
        session.commit()
    with Session(flow.database.engine) as session:
        project_student(session, "tenant_a", "student_a")
        session.commit()
    control.change = {"stem_text": STEM, "answer_text": "2+5=7",
                      "assessment": {**assessment("incorrect"), "knowledge_points": ["有理数的乘除法"],
                                     "correct_answer": "3", "correction_method": "负数除以正数为负数。"}}
    return flow, current_case(flow, case["id"]), control


def mark_for_recovery(flow, case):
    # An injected observation-quality flag lets this test exercise recovery
    # of a formerly usable machine reading, as in the original R1 fixture.
    with Session(flow.database.engine) as session:
        attempt = session.get(Entity, case["current_attempt_id"])
        attempt.document = {**attempt.document, "recognition_status": "needs_revision",
                            "recognition_uncertainties": ["原笔迹需要重新核对"]}
        session.commit()


def recover(flow, case):
    mark_for_recovery(flow, case)
    response = recheck(flow, current_case(flow, case["id"]))
    assert response.status_code == 202, response.text
    assert flow.worker.run_once()
    assert recovery(flow, current_case(flow, case["id"]))["last_outcome"] == "applied"
    return response.json()["job"]["id"]


def test_r1_replaces_machine_primary_from_old_reading_without_rewriting_question_or_attempt(recovery_flow):
    flow, case, _ = observed_case(recovery_flow)
    before_attempt = attempt_for(flow, case)
    with Session(flow.database.engine) as session:
        old_link = _current_links(session, case["question_id"])[OLD_POINT]
        old_id, old_document = old_link.id, (old_link.role, old_link.status, old_link.rationale)
        old_evidence = session.scalar(select(LearningEvidence).where(LearningEvidence.validity == "valid")).id
        old_question = deepcopy(session.get(Entity, case["question_id"]).document)
    job_id = recover(flow, case)
    with Session(flow.database.engine) as session:
        current = _current_links(session, case["question_id"])
        assert OLD_POINT not in current  # old observation is not offered as a current candidate
        withdrawn = session.scalar(select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.supersedes_id == old_id))
        assert withdrawn.status == "candidate"
        assert current[NEW_POINT].status == "confirmed" and current[NEW_POINT].role == "primary"
        original = session.get(QuestionKnowledgeLink, old_id)
        assert (original.role, original.status, original.rationale) == old_document
        assert session.get(Entity, case["question_id"]).document == old_question
        assert session.get(Entity, case["current_decision"]["id"]).document["validity"] == "stale"
        assert session.get(LearningEvidence, old_evidence).validity == "invalidated"
        [valid] = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        assert valid.knowledge_id == NEW_POINT and valid.document["result"] == "incorrect"
        assert valid.document["attempt_id"] == before_attempt["id"]
        assert valid.document["event_type"] == "school_first_attempt" and valid.document["independent"] is False
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == "attempt")) == 1
        audit = session.get(Task, job_id).audit["recovery_application"]
        assert audit["withdrawn_knowledge_links"] == [
            {"previous_link_id": old_id, "withdrawal_link_id": withdrawn.id}]
        assert job_id in withdrawn.rationale and "recognition_revision=1" in withdrawn.rationale
    after = attempt_for(flow, current_case(flow, case["id"]))
    for field in ("id", "asset_ids", "occurred_at", "created_at", "help_condition", "parent_attempt_id", "root_attempt_id"):
        assert after[field] == before_attempt[field]
    assert after["recognition_revision"] == before_attempt["recognition_revision"] + 1


def test_same_machine_knowledge_is_reconfirmed_from_new_reading_with_history(recovery_flow):
    flow, case, control = observed_case(recovery_flow)
    control.change["assessment"]["knowledge_points"] = ["有理数的加减法"]
    with Session(flow.database.engine) as session:
        old_id = _current_links(session, case["question_id"])[OLD_POINT].id
    recover(flow, case)
    with Session(flow.database.engine) as session:
        new = _current_links(session, case["question_id"])[OLD_POINT]
        withdrawal = session.scalar(select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.supersedes_id == old_id))
        assert new.id != old_id and new.status == "confirmed" and new.role == "primary"
        assert withdrawal.status == "candidate" and withdrawal.supersedes_id == old_id
        assert new.supersedes_id is None
        assert new.observation_bindings[0]["decision_id"] != withdrawal.observation_bindings[0]["decision_id"]


@pytest.mark.parametrize("legacy_shared_link", [False, True])
def test_r1_only_reattributes_current_attempt_and_preserves_prior_evidence_id(recovery_flow, legacy_shared_link):
    flow, case, control = observed_case(recovery_flow)
    first_attempt = attempt_for(flow, case)
    with Session(flow.database.engine) as session:
        first_evidence = session.scalar(select(LearningEvidence).where(LearningEvidence.validity == "valid"))
        first_evidence_id, first_document = first_evidence.id, deepcopy(first_evidence.document)
        first_link_id = first_document["link_id"]
    flow.pipeline.stem = STEM
    submit_correction(flow, case)
    assert flow.worker.run_once()
    second_case = current_case(flow, case["id"])
    assert second_case["current_attempt_id"] != first_attempt["id"]
    with Session(flow.database.engine) as session:
        second = session.get(Entity, second_case["current_attempt_id"])
        second.document = {**second.document, "answer_text": "(-2)+5=1", "answer_status": "present",
                           "recognition_status": "ready", "recognition_uncertainties": []}
        decision = session.get(Entity, second_case["current_decision"]["id"])
        decision.document = {**decision.document, "verdict": "incorrect", "validity": "candidate",
                             "reason_code": None, "uncertainties": [], "knowledge_points": ["有理数的加减法"],
                             "verification": verification_summary(assessment()["verification"])}
        saved = session.get(Entity, case["id"])
        saved.document = {**saved.document, "current_decision": decision.document, "progress": "needs_correction"}
        if legacy_shared_link:
            # Simulate migration's verified legacy binding backfill: one old
            # link formerly supplied evidence for both observed attempts.
            original = session.get(QuestionKnowledgeLink, first_link_id)
            original.observation_bindings = [
                {**item, "basis": "legacy_evidence"} for item in original.observation_bindings
            ] + [observation_binding(second, decision, basis="legacy_evidence")]
        project_student(session, "tenant_a", "student_a")
        session.commit()
        valid = list(session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")))
        assert len(valid) == 2 and all(row.knowledge_id == OLD_POINT for row in valid)
        assert session.get(LearningEvidence, first_evidence_id).validity == "valid"
        second_evidence_id = next(row.id for row in valid if row.document["attempt_id"] == second.id)
        # R1 receives the original question photo plus the new answer photo.
        # This synthetic reader must explicitly read the latter for attempt 2.
        source_asset = second.document["asset_ids"][0]
        control.change.update(asset_id=source_asset, regions=[{
            "asset_id": source_asset, "x": 0, "y": 0, "width": 120, "height": 80,
            "coordinate_space": "original_pixels"}])
    recover(flow, second_case)
    with Session(flow.database.engine) as session:
        for _ in range(2):
            project_student(session, "tenant_a", "student_a")
        session.commit()
        first = session.get(LearningEvidence, first_evidence_id)
        assert first.validity == "valid" and first.document == first_document
        assert session.get(LearningEvidence, second_evidence_id).validity == "invalidated"
        valid = list(session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")))
        assert {(row.document["attempt_id"], row.knowledge_id) for row in valid} == {
            (first_attempt["id"], OLD_POINT), (second_case["current_attempt_id"], NEW_POINT)}
        current = next(row for row in valid if row.document["attempt_id"] == second_case["current_attempt_id"])
        assert current.document["event_type"] == "correction" and current.document["independent"] is False
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == "attempt")) == 2


@pytest.mark.parametrize("status", ["confirmed", "rejected"])
def test_r1_preserves_staff_attribution_and_explicit_rejection(recovery_flow, status):
    flow, case, control = observed_case(recovery_flow)
    teacher(flow)
    with Session(flow.database.engine) as session:
        old_id = _current_links(session, case["question_id"])[OLD_POINT].id
    staff = link(flow, case, status=status, expected=old_id)
    control.change["assessment"]["knowledge_points"] = ["有理数的加减法", "有理数的乘除法"]
    job_id = recover(flow, case)
    with Session(flow.database.engine) as session:
        current = _current_links(session, case["question_id"])
        assert current[OLD_POINT].id == staff["id"] and current[OLD_POINT].status == status
        assert current[OLD_POINT].source == "staff"
        audit = session.get(Task, job_id).audit["recovery_application"]["withdrawn_knowledge_links"]
        assert all(item["previous_link_id"] != staff["id"] for item in audit)
        if status == "confirmed":
            assert NEW_POINT not in current
        else:
            assert current[NEW_POINT].status == "confirmed" and current[NEW_POINT].role == "primary"


def test_staff_confirmation_during_r1_model_call_is_preserved(recovery_flow):
    flow, case, control = observed_case(recovery_flow)
    teacher(flow)
    with Session(flow.database.engine) as session:
        old_id = _current_links(session, case["question_id"])[OLD_POINT].id
    confirmed = []
    control.callback = lambda: confirmed.append(link(flow, case, expected=old_id))
    job_id = recover(flow, case)
    with Session(flow.database.engine) as session:
        current = _current_links(session, case["question_id"])
        assert current[OLD_POINT].id == confirmed[0]["id"] and current[OLD_POINT].source == "staff"
        audit = session.get(Task, job_id).audit["recovery_application"]["withdrawn_knowledge_links"]
        assert all(item["previous_link_id"] != confirmed[0]["id"] for item in audit)


@pytest.mark.parametrize("status", ["confirmed", "rejected"])
def test_r1_notes_only_question_revision_inherits_staff_authority_with_explicit_history(recovery_flow, status):
    flow, case, control = observed_case(recovery_flow)
    teacher(flow)
    with Session(flow.database.engine) as session:
        old_id = _current_links(session, case["question_id"])[OLD_POINT].id
    staff = link(flow, case, status=status, expected=old_id)
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "recognition_uncertainties": ["旧题面读图疑点"]}
        session.commit()
    control.change["assessment"]["knowledge_points"] = ["有理数的加减法", "有理数的乘除法"]
    job_id = recover(flow, case)
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        assert question.revision == 2 and question.document["stem_text"] == STEM
        inherited = _current_links(session, case["question_id"])[OLD_POINT]
        assert inherited.id != staff["id"] and inherited.supersedes_id == staff["id"]
        assert inherited.source == "staff" and inherited.status == status and inherited.question_revision == 2
        assert inherited.actor_id is None and inherited.observation_bindings is None
        assert "不代表新增老师操作" in inherited.rationale
        assert session.get(QuestionKnowledgeLink, staff["id"]).question_revision == 1
        assert session.get(Task, job_id).audit["recovery_application"]["inherited_staff_knowledge_links"] == [
            {"previous_staff_link_id": staff["id"], "inherited_link_id": inherited.id}]


def test_changed_printed_question_cannot_inherit_staff_links(recovery_flow):
    flow, case, _ = observed_case(recovery_flow)
    teacher(flow)
    with Session(flow.database.engine) as session:
        old_id = _current_links(session, case["question_id"])[OLD_POINT].id
    link(flow, case, expected=old_id)
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        previous = deepcopy(question.document)
        question.revision += 1
        question.document = {**question.document, "revision": question.revision, "stem_text": "(-6÷3)-5=?"}
        assert inherit_staff_links_for_unchanged_question(session, question, previous, job_id="synthetic-job") == []


def test_failed_application_rolls_back_machine_withdrawal(recovery_flow, monkeypatch):
    flow, case, _ = observed_case(recovery_flow)
    with Session(flow.database.engine) as session:
        old_id = _current_links(session, case["question_id"])[OLD_POINT].id
        count = session.scalar(select(func.count()).select_from(QuestionKnowledgeLink))
    mark_for_recovery(flow, case)
    accepted = recheck(flow, current_case(flow, case["id"]))
    assert accepted.status_code == 202
    original = flow.worker._apply_page_assessment
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic failure after withdrawal")
    monkeypatch.setattr(flow.worker, "_apply_page_assessment", fail)
    assert flow.worker.run_once()
    with Session(flow.database.engine) as session:
        assert _current_links(session, case["question_id"])[OLD_POINT].id == old_id
        assert session.scalar(select(func.count()).select_from(QuestionKnowledgeLink)) == count
        assert session.get(Entity, case["current_attempt_id"]).document["recognition_revision"] == 1


@pytest.mark.parametrize("during_call", [False, True])
def test_old_semantic_batch_cannot_rebuild_mapping_after_r1_and_new_input_can_map(recovery_flow, during_call):
    flow, case, control = observed_case(recovery_flow, labels=[])
    control.change["assessment"]["knowledge_points"] = ["除法符号"]  # intentionally not an exact catalogue label
    def replace_reading():
        if flow.database.dialect == "sqlite" and during_call:
            # Release SQLite's read transaction during the simulated network
            # gap. PostgreSQL keeps its old objects while an independent R1
            # request commits, then exercises the real locked save-time reload.
            old_client.active_session.rollback()
        recover(flow, case)
    old_client = BatchClient(during_call=replace_reading if during_call else None)
    queue(flow, old_client)
    if not during_call:
        replace_reading()
    with Session(flow.database.engine) as session:
        job = session.scalar(select(LearningOutbox).where(LearningOutbox.kind == "map_model_batch"))
        old_client.active_session = session
        outcome = map_model_batch(session, job, SimpleNamespace(client=old_client))
        job.payload = {**job.payload, "outcome": outcome}
        job.status = "succeeded"
        session.commit()
        if during_call:
            assert outcome["count"] == 0 and outcome["results"][0]["reason"] == "stale_input"
        else:
            assert outcome["status"] == "stale_or_linked" and old_client.calls == []
        assert not any(link.status == "confirmed" for link in _current_links(session, case["question_id"]).values())
    def new_skill(rows, _):
        rows[0]["links"][0].update(knowledge_id=NEW_POINT, rationale="题干中的负数除以正数直接考查有理数除法符号。")
    fresh_client = BatchClient(transform=new_skill)
    LearningWorker(flow.database.engine, client=fresh_client).drain()
    assert len(fresh_client.calls) == 1
    assert json.loads(fresh_client.calls[0]["text"])["questions"][0]["answer_text"] == "2+5=7"
    with Session(flow.database.engine) as session:
        [mapping] = [row for row in _current_links(session, case["question_id"]).values() if row.status == "confirmed"]
        assert mapping.knowledge_id == NEW_POINT and mapping.role == "primary"


@pytest.mark.parametrize("published", [False, True])
def test_r1_report_refresh_uses_new_knowledge_and_keeps_published_history(recovery_flow, published):
    flow, case, _ = observed_case(recovery_flow)
    worker = LearningWorker(flow.database.engine)
    worker.drain()
    teacher(flow)
    with Session(flow.database.engine) as session:
        start, end = bounds("daily", local_day(session.get(Homework, flow.homework_id).occurred_at))
        report = refresh(session, session.get(Student, "student_a"), start, end, "requested", "daily")
        old_id, old_revision, old_snapshot = report.id, report.revision, deepcopy(report.snapshot)
        assert {row["knowledge_id"] for row in report.snapshot["focus"]} == {OLD_POINT}
        session.commit()
    if published:
        result = flow.client.post(f"/v1/reports/{old_id}/publish", json={"expected_revision": old_revision})
        assert result.status_code == 200, result.text
    recover(flow, case)
    with Session(flow.database.engine) as session:
        assert ("tenant_a", "student_a") in dirty_students(session)
    worker.drain()
    with Session(flow.database.engine) as session:
        reports = list(session.scalars(select(LearningReport).where(LearningReport.cadence == "daily",
            LearningReport.student_id == "student_a", LearningReport.period_start == start.isoformat())
            .order_by(LearningReport.version)))
        latest = reports[-1]
        assert latest.snapshot == build_snapshot(session, session.get(Student, "student_a"), start, end, "daily")
        assert {row["knowledge_id"] for row in latest.snapshot["focus"]} == {NEW_POINT}
        if published:
            assert len(reports) == 2 and latest.status == "draft" and latest.supersedes_id == old_id
            assert reports[0].status == "published" and reports[0].snapshot == old_snapshot
        else:
            assert len(reports) == 1 and latest.id == old_id and latest.revision > old_revision
