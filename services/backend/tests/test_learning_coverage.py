"""Read-only mapping gaps with real correction records and synthetic mapper output."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import evidence_count, learning, link, review
from test_learning_mapping_batches import BatchClient, jobs, queue
from test_learning_mapping_versions import ai_case
from test_learning_reports import after_period  # noqa: F401
from xueban_backend.correction_models import Entity
from xueban_backend.learning_coverage import mapping_coverage
from xueban_backend.learning_models import LearningOutbox, QuestionKnowledgeLink, StudentLearningProfile
from xueban_backend.learning_reports import build_snapshot, local_day, period_of, render_report
from xueban_backend.models import Homework, Student


def wrong(flow):
    def patch(item):
        item.update(answer_text="12", stem_text="18 - 7 = ____")
        item["assessment"].update(verdict="incorrect")
    return ai_case(flow, labels=[], patch=patch)


def no_match(rows, _):
    for row in rows:
        row.update(resolution="no_match", links=[], reason="暂未找到直接对应的教材节点。")


def coverage(flow):
    response = flow.client.get("/v1/students/student_a/knowledge")
    assert response.status_code == 200, response.text
    return response.json()["mapping_coverage"]


def test_unlinked_error_is_visible_without_new_evidence_jobs_or_teacher_tasks(flow):
    case = wrong(flow)
    assert coverage(flow)["total"] == 1
    assert coverage(flow)["items"][0]["state"] == "not_linked"
    client = BatchClient(no_match)
    queue(flow, client)
    assert coverage(flow)["items"][0]["state"] == "mapping_pending"
    learning(flow, client).drain()
    before = jobs(flow)
    result = coverage(flow)
    assert result["total"] == result["unlinked"] == result["model_count"] == 1
    assert result["linked"] == result["staff_count"] == 0
    assert result["items"][0]["state"] == "no_direct_match"
    assert result["items"][0]["case_id"] == case["id"]
    assert result["items"][0]["stem_preview"] == "18 - 7 = ____"
    assert result["items"][0]["subject"] == "数学"
    assert evidence_count(flow) == 0 and jobs(flow) == before and len(client.calls) == 1
    teacher(flow)
    assert flow.client.get("/v1/knowledge-link-queue").json()["items"] == []


@pytest.mark.parametrize("change", ["input_key", "graph", "profile", "protocol", "result_key", "failed", "outcome_protocol", "outcome_graph"])
def test_old_mapper_outcome_cannot_become_current_no_match(flow, change):
    wrong(flow)
    learning(flow, BatchClient(no_match)).drain()
    assert coverage(flow)["items"][0]["state"] == "no_direct_match"
    with Session(flow.database.engine) as session, session.begin():
        job = session.scalar(select(LearningOutbox).where(LearningOutbox.kind == "map_model_batch"))
        payload = deepcopy(job.payload)
        if change == "input_key": payload["inputs"][0]["input_key"] = "old"
        elif change == "graph": payload["graph_version"] = "old"
        elif change == "protocol": payload["protocol"] = "old"
        elif change == "result_key": payload["outcome"]["results"][0]["input_key"] = "old"
        elif change == "outcome_protocol": payload["outcome"]["protocol"] = "old"
        elif change == "outcome_graph": payload["outcome"]["graph_version"] = "old"
        elif change == "profile": session.get(StudentLearningProfile, "student_a").revision += 1
        else: job.status = "dead"
        job.payload = payload
    assert coverage(flow)["items"][0]["state"] == "not_linked"


def test_confirmed_primary_only_and_staff_rejection_wins(flow):
    case = wrong(flow)
    learning(flow, BatchClient()).drain()
    assert coverage(flow)["linked"] == 1
    with Session(flow.database.engine) as session, session.begin():
        row = session.scalar(select(QuestionKnowledgeLink))
        row.role = "secondary"
        link_id = row.id
    assert coverage(flow)["unlinked"] == 1
    teacher(flow)
    link(flow, case, expected=link_id)
    assert coverage(flow)["linked"] == 1
    current = flow.client.get(f"/v1/questions/{case['question_id']}/knowledge-links").json()
    item = next(row for row in current["items"] if row["status"] == "confirmed")
    link(flow, case, knowledge_id=item["knowledge_id"], status="rejected", expected=item["id"])
    assert coverage(flow)["unlinked"] == 1


@pytest.mark.parametrize("change", ["correct", "withdrawn", "case_archived", "question_stale", "answer_stale", "book", "subject", "blocked"])
def test_removed_or_outside_scope_current_errors_disappear(flow, change):
    case = wrong(flow)
    assert coverage(flow)["total"] == 1
    if change == "correct":
        teacher(flow)
        review(flow, case, action="replace", verdict="correct")
    else:
        with Session(flow.database.engine) as session, session.begin():
            row = session.get(Entity, case["id"])
            q = session.get(Entity, case["question_id"])
            a = session.get(Entity, row.document["current_attempt_id"])
            if change == "withdrawn":
                d = session.get(Entity, row.document["current_decision"]["id"])
                d.document = {**d.document, "validity": "stale"}
                row.document = {**row.document, "current_decision": d.document}
            elif change == "case_archived": row.document = {**row.document, "lifecycle": "archived"}
            elif change == "question_stale": q.revision += 1
            elif change == "answer_stale": a.document = {**a.document, "recognition_revision": 20}
            elif change == "subject": q.annotations = {**(q.annotations or {}), "subject_override": "英语"}
            elif change == "book": session.get(Homework, flow.homework_id).curriculum = {"grade": 7, "edition": "old"}
            else: session.get(Homework, flow.homework_id).processing_blocked = True
    assert coverage(flow)["total"] == 0


def test_window_snapshot_is_as_of_and_daily_is_not_duplicated(flow):
    case = wrong(flow)
    learning(flow, BatchClient(no_match)).drain()
    with Session(flow.database.engine) as session:
        student = session.get(Student, "student_a")
        attempt = session.get(Entity, case["current_attempt_id"])
        moment = datetime.fromisoformat(attempt.document["occurred_at"].replace("Z", "+00:00"))
        assert mapping_coverage(session, student, start=moment, end=moment+timedelta(seconds=1))["total"] == 1
        assert mapping_coverage(session, student, start=moment-timedelta(seconds=1), end=moment)["total"] == 0
        start, end = period_of(local_day(moment))
        snap = build_snapshot(session, student, start, end)
        saved = deepcopy(snap)
        assert snap["mapping_coverage"]["unlinked"] == 1
        assert "state" not in snap["mapping_coverage"]["items"][0]
        daily = build_snapshot(session, student, local_day(moment), local_day(moment)+timedelta(days=1), cadence="daily")
        assert "mapping_coverage" not in daily and len(daily["mistakes"]) == 1
    teacher(flow)
    review(flow, case, action="replace", verdict="correct")
    assert coverage(flow)["total"] == 0 and saved["mapping_coverage"]["unlinked"] == 1
    body = {"student_name":"同学", "corrections":[], "report":{
        "snapshot":saved,"cadence":"half_month","version":1,"published_at":moment.isoformat(),"staff_note":None,"next_step":None}}
    page = render_report(body)
    assert "还有错题尚未关联知识点" in page and "18 - 7" in page and "生成这份报告时" in page
    del saved["mapping_coverage"]
    assert "还有错题尚未关联知识点" not in render_report(body)


def test_authorization_does_not_expose_another_student(flow):
    wrong(flow)
    assert flow.client.get("/v1/students/student_b/knowledge").status_code == 404


def test_shared_binding_is_identical_to_preexisting_mapping_contract(flow):
    from xueban_backend.curriculum_catalog import scope_payload
    from xueban_backend.learning_mapping import MAPPING_VERSION, _digest, _model_input
    case = wrong(flow)
    with Session(flow.database.engine) as session:
        context = _model_input(session, "tenant_a", "student_a", case["id"])
        q, a, d = context["question"], context["attempt"], context["decision"]
        profile = session.get(StudentLearningProfile, "student_a")
        binding = {"case_id": case["id"], "question_id": q.id, "question_revision": q.revision,
            "attempt_id": a.id, "attempt_revision": a.revision, "attempt_recognition_revision": a.document["recognition_revision"],
            "decision_id": d["id"], "decision_revision": d["revision"], "graph_version": context["graph"].version,
            "profile_revision": profile.revision}
        binding["input_key"] = _digest({"protocol": MAPPING_VERSION, "binding": binding,
            "question": q.document, "annotations": q.annotations, "attempt": a.document, "decision": d,
            "curriculum": context["homework"].curriculum, "scope": scope_payload(context["scope"])})
        assert context["binding"] == binding


def test_new_current_answer_never_borrows_old_binding_or_no_match(flow):
    case = wrong(flow)
    learning(flow, BatchClient(no_match)).drain()
    assert coverage(flow)["items"][0]["state"] == "no_direct_match"
    with Session(flow.database.engine) as session, session.begin():
        c = session.get(Entity, case["id"])
        a1 = session.get(Entity, c.document["current_attempt_id"])
        d1 = session.get(Entity, c.document["current_decision"]["id"])
        a2 = {**deepcopy(a1.document), "id": "coverage-a2", "parent_attempt_id": a1.id}
        d2 = {**deepcopy(d1.document), "id": "coverage-d2"}
        d2["input_versions"]["attempt_id"] = a2["id"]
        session.add_all([Entity(id=a2["id"],homework_id=flow.homework_id,kind="attempt",revision=1,document=a2),
                         Entity(id=d2["id"],homework_id=flow.homework_id,kind="decision",revision=1,document=d2)])
        d1.document = {**d1.document,"validity":"stale"}
        c.document = {**c.document,"attempt_ids":[a1.id,a2["id"]],"current_attempt_id":a2["id"],"current_decision":d2}
    result = coverage(flow)
    assert result["total"] == 1 and result["items"][0]["state"] == "not_linked"
    teacher(flow)
    current = flow.client.get(f"/v1/cases/{case['id']}").json()
    review(flow, current, action="replace", verdict="correct")
    assert coverage(flow)["total"] == 0


def test_choose_new_book_excludes_legacy_homework_without_claiming_complete_graph(flow):
    from test_curriculum_binding import publish_book, choose
    wrong(flow)
    assert coverage(flow)["total"] == 1
    publish_book(flow)
    teacher(flow)
    choose(flow, "test-pep-book-v1")
    result = coverage(flow)
    assert result["graph_version"] == "test-pep-book-v1"
    assert result["total"] == 0 and "旧版教材作业不在此范围" in result["notice"]


def test_all_totals_survive_list_limit_and_queries_are_batched(flow):
    from sqlalchemy import event
    case = wrong(flow)
    with Session(flow.database.engine) as session, session.begin():
        q = session.get(Entity, case["question_id"])
        a = session.get(Entity, case["current_attempt_id"])
        d = session.get(Entity, case["current_decision"]["id"])
        c = session.get(Entity, case["id"])
        for n in range(31):
            qid, aid, did, cid = [f"coverage-{kind}-{n}" for kind in ("q", "a", "d", "c")]
            qdoc = {**deepcopy(q.document), "question_id":qid}
            adoc = {**deepcopy(a.document), "id":aid,"question_id":qid,"case_id":cid}
            ddoc = {**deepcopy(d.document), "id":did,"case_id":cid}
            ddoc["input_versions"].update(question_id=qid,attempt_id=aid)
            cdoc = {**deepcopy(c.document), "id":cid,"question_id":qid,"current_attempt_id":aid,
                    "root_attempt_id":aid,"attempt_ids":[aid],"current_decision":ddoc}
            for kind, identity, document in (("question",qid,qdoc),("attempt",aid,adoc),("decision",did,ddoc),("case",cid,cdoc)):
                session.add(Entity(id=identity,homework_id=flow.homework_id,kind=kind,revision=1,document=document))
    queries = []
    def capture(*args): queries.append(args[2])
    event.listen(flow.database.engine, "before_cursor_execute", capture)
    try:
        with Session(flow.database.engine) as session:
            student = session.get(Student, "student_a")
            result = mapping_coverage(session, student)
    finally:
        event.remove(flow.database.engine, "before_cursor_execute", capture)
    assert result["total"] == result["unlinked"] == 32
    assert len(result["items"]) == 30 and result["truncated"]
    assert len(queries) <= 8, len(queries)


def test_published_snapshot_is_not_recomputed_on_get(flow, after_period):
    from test_learning_reports import reports, publish
    wrong(flow)
    learning(flow, BatchClient(no_match)).drain()
    teacher(flow)
    [draft] = reports(flow)
    response = publish(flow, draft)
    assert response.status_code == 200, response.text
    published = response.json()
    saved = deepcopy(published["snapshot"])
    assert saved["mapping_coverage"]["unlinked"] == 1
    case = flow.client.get(f"/v1/cases/{saved['mapping_coverage']['items'][0]['case_id']}").json()
    review(flow, case, action="replace", verdict="correct")
    assert coverage(flow)["unlinked"] == 0
    reread = flow.client.get(f"/v1/reports/{published['id']}").json()
    assert reread["snapshot"] == saved and reread["revision"] == published["revision"]
