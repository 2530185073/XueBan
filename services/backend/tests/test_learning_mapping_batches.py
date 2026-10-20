"""Batch semantic attribution over synthetic text only; never calls a model API."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_curriculum_binding import choose, publish_book
from test_learning import evidence_count, knowledge, learning, link, prepare_latest_math_curriculum
from test_learning_mapping_versions import ai_case as original_ai_case
from test_multimodal_worker import assessment, enable_page_assessments
from xueban_backend.correction_models import Entity
from xueban_backend.learning_mapping import (
    MAX_BATCH, _current_links, _model_input, enqueue_model_batches, map_model_batch,
)
from xueban_backend.learning_models import CurriculumGraph, LearningOutbox, QuestionKnowledgeLink, StudentLearningProfile
from xueban_backend.learning_projection import new_id, project_student, published_graph
from xueban_backend.models import Homework
from xueban_backend.providers.ark import Attempt, Metadata, ProviderError


def ai_case(flow, labels=None):
    return original_ai_case(flow, labels=labels, patch=lambda item: item.update(answer_text="2"))


class BatchClient:
    settings = SimpleNamespace(enabled=True)

    def __init__(self, transform=None, during_call=None):
        self.calls = []
        self.transform, self.during_call = transform, during_call

    def generate(self, **kwargs):
        payload = json.loads(kwargs["text"])
        self.calls.append(kwargs)
        rows = [{
            "question_id": question["question_id"], "resolution": "matched", "reason": "题干直接要求执行有理数加法。",
            "links": [{"knowledge_id": payload["candidates"][0]["id"], "role": "primary",
                       "evidence_field": "stem_text", "evidence_quote": question["stem_text"],
                       "rationale": "题干的运算符及两个有理数要求直接执行加法运算。"}],
        } for question in payload["questions"]]
        # Pick the intended skill independently of incidental catalogue order.
        preferred = next((node["id"] for node in payload["candidates"]
                          if node["name"] == "有理数的加减法"), payload["candidates"][0]["id"])
        for row in rows:
            row["links"][0]["knowledge_id"] = preferred
        if self.transform:
            self.transform(rows, payload)
        if self.during_call:
            self.during_call()
        return SimpleNamespace(data={"results": rows},
                               metadata=Metadata("synthetic-mapper", "synthetic-mapper", "req-1", None, None, 14, ()))


def jobs(flow):
    with Session(flow.database.engine) as session:
        return [(job.status, deepcopy(job.payload)) for job in session.scalars(
            select(LearningOutbox).where(LearningOutbox.kind == "map_model_batch").order_by(LearningOutbox.created_at))]


def queue(flow, client):
    with Session(flow.database.engine) as session, session.begin():
        enqueue_model_batches(session, "tenant_a", "student_a", SimpleNamespace(client=client))


def run_queued(flow, client):
    with Session(flow.database.engine) as session, session.begin():
        job = session.scalar(select(LearningOutbox).where(LearningOutbox.kind == "map_model_batch"))
        client.active_session = session
        outcome = map_model_batch(session, job, SimpleNamespace(client=client))
    return outcome


@contextmanager
def concurrent_write(flow, client):
    # SQLite's real read transactions block a second writer. Inject changes in
    # its own transaction to exercise the save gate; PostgreSQL verifies a
    # genuinely independent committed write while the mapper holds old objects.
    if flow.database.dialect == "sqlite":
        yield client.active_session
        client.active_session.flush()
    else:
        with Session(flow.database.engine) as session, session.begin():
            yield session


def test_synonym_maps_once_with_quote_provenance_and_model_only_evidence(flow):
    case = ai_case(flow, labels=["整数相加"])
    with Session(flow.database.engine) as session:
        assert _model_input(session, "tenant_a", "student_a", case["id"]) is not None
    client = BatchClient()
    learning(flow, client).drain()
    learning(flow, client).drain()
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["role"] == "knowledge_extract" and request["thinking"] is False
    assert case["question_id"] not in request["instructions"]
    assert len(json.loads(request["text"])["candidates"]) > 1
    with Session(flow.database.engine) as session:
        [mapping] = list(_current_links(session, case["question_id"]).values())
        assert (mapping.source, mapping.status, mapping.actor_id) == ("model", "confirmed", None)
        assert "原题依据「1 + 1 = ?」" in mapping.rationale
    assert evidence_count(flow) == 1
    [state] = knowledge(flow)["states"]
    assert state["model_evidence_count"] == 1 and state["staff_verified_evidence_count"] == 0
    assert state["learning_status"] == "no_judgment"
    [(status, payload)] = jobs(flow)
    assert status == "succeeded"
    assert payload["outcome"]["metadata"]["request_id"] == "req-1"
    assert payload["outcome"]["results"][0]["link_ids"] == [mapping.id]


def test_reliable_error_becomes_model_primary_evidence_without_teacher_action(flow):
    original_ai_case(flow, labels=["整数相加"],
                     patch=lambda item: item["assessment"].update(verdict="incorrect"))
    client = BatchClient()
    learning(flow, client).drain()
    [state] = knowledge(flow)["states"]
    assert state["learning_status"] == "needs_help_now"
    assert state["observed_error_count"] == 1 and state["model_evidence_count"] == 1
    assert state["staff_verified_evidence_count"] == 0


@pytest.mark.parametrize("resolution", ["no_match", "ambiguous"])
def test_empty_results_are_durable_without_teacher_tasks_or_repeat_calls(flow, resolution):
    ai_case(flow, labels=[])

    def output(rows, _):
        rows[0].update(resolution=resolution, links=[], reason="当前节点不足以明确对应本题。")

    client = BatchClient(output)
    learning(flow, client).drain()
    learning(flow, client).drain()
    assert len(client.calls) == 1 and evidence_count(flow) == 0
    [(status, payload)] = jobs(flow)
    assert status == "succeeded" and payload["outcome"]["results"][0]["status"] == resolution
    with Session(flow.database.engine) as session:
        assert list(session.scalars(select(QuestionKnowledgeLink))) == []
        assert not list(session.scalars(select(LearningOutbox).where(LearningOutbox.kind == "suggest_links")))


def test_ambiguous_mapping_stays_candidate_and_does_not_affect_state(flow):
    case = ai_case(flow, labels=[])
    client = BatchClient(lambda rows, _: rows[0].update(resolution="ambiguous"))
    learning(flow, client).drain()
    learning(flow, client).drain()
    assert len(client.calls) == 1
    with Session(flow.database.engine) as session:
        [mapping] = list(_current_links(session, case["question_id"]).values())
        assert (mapping.status, mapping.source) == ("candidate", "model")
    assert evidence_count(flow) == 0


@pytest.mark.parametrize("change,reason", [
    (lambda rows, _: rows[0]["links"][0].update(knowledge_id="invented"), "invalid_row"),
    (lambda rows, _: rows[0]["links"][0].update(evidence_quote="1 - 1"), "quote_not_bound"),
    (lambda rows, _: rows[0]["links"][0].update(rationale="加法"), "invalid_row"),
    (lambda rows, _: rows[0]["links"][0].update(role="secondary"), "missing_unique_primary"),
    (lambda rows, _: rows.append(deepcopy(rows[0])), "duplicate_or_missing_question"),
    (lambda rows, _: rows.clear(), "duplicate_or_missing_question"),
])
def test_unbound_or_invalid_outputs_never_create_confirmed_links(flow, change, reason):
    ai_case(flow, labels=[])
    client = BatchClient(change)
    learning(flow, client).drain()
    assert evidence_count(flow) == 0
    assert jobs(flow)[0][1]["outcome"]["results"][0]["reason"] == reason


@pytest.mark.parametrize("change", ["question", "attempt", "decision", "profile", "uncertainty", "blocked"])
def test_changes_during_model_call_discard_output(flow, change):
    case = ai_case(flow, labels=[])

    def mutate():
        with concurrent_write(flow, client) as session:
            if change == "question":
                row = session.get(Entity, case["question_id"])
                row.document = {**row.document, "stem_text": "1 - 1 = ?"}  # also catch legacy no-version writes
            elif change == "attempt":
                row = session.get(Entity, case["current_attempt_id"])
                row.document = {**row.document, "answer_text": "7"}
            elif change in {"decision", "uncertainty"}:
                row = session.get(Entity, case["current_decision"]["id"])
                row.document = {**row.document, "verdict": "incorrect"} if change == "decision" else {
                    **row.document, "uncertainties": ["负号不能辨认"]}
                stored = session.get(Entity, case["id"])
                stored.document = {**stored.document, "current_decision": row.document}
            elif change == "profile":
                row = session.get(StudentLearningProfile, "student_a")
                row.revision += 1
            else:
                session.get(Homework, flow.homework_id).processing_blocked = True

    client = BatchClient(during_call=mutate)
    queue(flow, client)
    outcome = run_queued(flow, client)
    assert outcome["count"] == 0 and outcome["results"][0]["reason"] == "stale_input"
    assert evidence_count(flow) == 0


def test_teacher_rejection_and_manual_primary_take_precedence_even_during_call(flow):
    case = ai_case(flow, labels=[])
    teacher(flow)

    def reject():
        if flow.database.dialect == "postgresql":
            link(flow, case, status="rejected")
            return
        with concurrent_write(flow, client) as session:
            session.add(QuestionKnowledgeLink(
                id=new_id("qkl"), tenant_id="tenant_a", student_id="student_a", homework_id=flow.homework_id,
                question_id=case["question_id"], question_revision=1, knowledge_id="kp-rational-add-sub",
                graph_version=published_graph(session, "student_a").version, role="primary", source="staff",
                status="rejected", rationale="老师核对后驳回", actor_id="actor_a", supersedes_id=None,
                created_at=datetime.now(timezone.utc)))

    client = BatchClient(during_call=reject)
    queue(flow, client)
    outcome = run_queued(flow, client)
    assert outcome["count"] == 0 and outcome["results"][0]["reason"] == "staff_primary_rejected"
    with Session(flow.database.engine) as session:
        [row] = list(_current_links(session, case["question_id"]).values())
        assert row.source == "staff" and row.status == "rejected"
    # A manually confirmed alternate primary blocks every automatic replacement.
    manual = link(flow, case, knowledge_id="kp-rational-mul-div")
    second = BatchClient()
    outcome = run_queued(flow, second)
    assert outcome["status"] == "stale_or_linked" and second.calls == []
    with Session(flow.database.engine) as session:
        assert _current_links(session, case["question_id"])["kp-rational-mul-div"].id == manual["id"]


def test_new_book_enum_is_used_only_for_homework_in_that_book(flow):
    case = ai_case(flow, labels=["整数相加"])
    publish_book(flow)
    teacher(flow)
    choose(flow, "test-pep-book-v1")
    client = BatchClient()
    queue(flow, client)
    assert jobs(flow) == []  # old BNUP evidence cannot be silently attached to PEP
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(Homework, flow.homework_id)
        row.curriculum = {**row.curriculum, "publisher_code": "PEP", "edition": "isbn:9787107382468"}
    queue(flow, client)
    outcome = run_queued(flow, client)
    assert outcome["count"] == 1 and outcome["graph_version"] == "test-pep-book-v1"
    assert {node["id"] for node in json.loads(client.calls[0]["text"])["candidates"]} == {"test-pep-add", "test-pep-mul"}
    with Session(flow.database.engine) as session:
        [mapping] = list(_current_links(session, case["question_id"]).values())
        assert mapping.knowledge_id == "test-pep-add" and mapping.graph_version == "test-pep-book-v1"


def test_switching_book_during_call_rejects_old_response(flow):
    ai_case(flow, labels=[])
    publish_book(flow)
    teacher(flow)

    def switch():
        if flow.database.dialect == "postgresql":
            choose(flow, "test-pep-book-v1")
            return
        with concurrent_write(flow, client) as session:
            profile = session.get(StudentLearningProfile, "student_a")
            graph = session.get(CurriculumGraph, "test-pep-book-v1")
            profile.document = {**profile.document, "curriculum_binding": {
                **graph.book_identity, "graph_version": graph.version}}
            profile.revision += 1

    client = BatchClient(during_call=switch)
    queue(flow, client)
    outcome = run_queued(flow, client)
    assert outcome["count"] == 0 and outcome["results"][0]["reason"] == "stale_input"


@pytest.mark.parametrize("flag", ["question_not_ready", "answer_not_ready", "uncertain", "unreliable", "wrong_tenant"])
def test_only_ready_reliable_current_evidence_is_enqueued(flow, flag):
    case = ai_case(flow, labels=[])
    with Session(flow.database.engine) as session, session.begin():
        if flag in {"question_not_ready", "answer_not_ready"}:
            row = session.get(Entity, case["question_id"] if flag == "question_not_ready" else case["current_attempt_id"])
            row.document = ({**row.document, "stem_text": None} if flag == "question_not_ready"
                            else {**row.document, "recognition_status": "needs_revision"})
        elif flag in {"uncertain", "unreliable"}:
            row = session.get(Entity, case["current_decision"]["id"])
            patch = {"uncertainties": ["字迹不清"]} if flag == "uncertain" else {
                "verification": {"status": "conflict", "independent_readback": True, "reason_codes": []}}
            row.document = {**row.document, **patch}
            stored = session.get(Entity, case["id"])
            stored.document = {**stored.document, "current_decision": row.document}
        client = BatchClient()
        enqueue_model_batches(session, "tenant_b" if flag == "wrong_tenant" else "tenant_a", "student_a",
                              SimpleNamespace(client=client))
    assert jobs(flow) == []


def test_thirteen_questions_use_two_batches_with_shared_candidates_and_unique_outputs(flow):
    prepare_latest_math_curriculum(flow)

    def many(items):
        original = deepcopy(items[0])
        items[:] = []
        for i in range(MAX_BATCH + 1):
            item = deepcopy(original)
            item.update(label=str(i + 1), subject="数学", answer_text="2")
            item["assessment"] = {**assessment("correct"), "knowledge_points": ["整数相加"]}
            items.append(item)

    enable_page_assessments(flow, many)
    flow.ready()
    # Simulate the correction worker's immediate projection; periodic mapping
    # must still run even though its fingerprint is already clean.
    with Session(flow.database.engine) as session, session.begin():
        project_student(session, "tenant_a", "student_a")
    client = BatchClient()
    learning(flow, client).drain()
    assert sorted(len(json.loads(call["text"])["questions"]) for call in client.calls) == [1, 12]
    ids = [question["question_id"] for call in client.calls for question in json.loads(call["text"])["questions"]]
    assert len(ids) == len(set(ids)) == 13
    assert evidence_count(flow) == 13
    learning(flow, client).drain()
    assert len(client.calls) == 2


def test_one_invalid_quote_does_not_discard_other_questions(flow):
    flow.pipeline.extra_item_label = "2"
    ai_case(flow, labels=[])
    # The fixture transforms only item 1; explicitly make item 2 a current,
    # clear observation too before enqueueing this two-target batch.
    with Session(flow.database.engine) as session, session.begin():
        for case in session.scalars(select(Entity).where(Entity.kind == "case")):
            question = session.get(Entity, case.document["question_id"])
            question.document = {**question.document, "subject": "数学"}
    client = BatchClient(lambda rows, _: rows[0]["links"][0].update(evidence_quote="not on this page"))
    learning(flow, client).drain()
    assert len(client.calls) == 1
    [(_, payload)] = jobs(flow)
    assert {row["status"] for row in payload["outcome"]["results"]} == {"matched", "unmapped"}
    assert evidence_count(flow) == 1


def test_temporarily_disabled_provider_can_resume_unprocessed_input(flow):
    ai_case(flow, labels=[])
    queue(flow, BatchClient())
    worker = learning(flow)
    worker.drain()
    assert jobs(flow)[0][1]["outcome"]["status"] == "provider_disabled"
    client = BatchClient()
    learning(flow, client).drain()
    assert len(client.calls) == 1 and evidence_count(flow) == 1


def test_provider_failure_keeps_bounded_retry_audit_without_any_link(flow):
    ai_case(flow, labels=[])

    class FailedClient(BatchClient):
        def generate(self, **kwargs):
            raise ProviderError("service_unavailable", attempts=(Attempt(1, 503, "http_error", 21),), latency_ms=21)

    worker = learning(flow, FailedClient())
    worker.drain()
    [(status, payload)] = jobs(flow)
    assert status == "queued"
    assert payload["failed_executions"][0]["code"] == "service_unavailable"
    assert payload["failed_executions"][0]["attempts"][0]["status_code"] == 503
    assert evidence_count(flow) == 0
    # Exhausted jobs remain deduped until the input changes, not an endless queue.
    with Session(flow.database.engine) as session, session.begin():
        job = session.scalar(select(LearningOutbox).where(LearningOutbox.kind == "map_model_batch"))
        job.status = "dead"
        job.tries = 5
    queue(flow, FailedClient())
    assert len(jobs(flow)) == 1
