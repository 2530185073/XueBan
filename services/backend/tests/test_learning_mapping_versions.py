"""Version-safe mapping and positive evidence; all model outputs are injected."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import evidence_count, knowledge, learning, link, prepare_latest_math_curriculum, reviewed
from test_multimodal_worker import assessment, enable_page_assessments
from xueban_backend.correction_models import Entity
from xueban_backend.learning_mapping import _current_links as scoped_links, _match_node, suggest_links
from xueban_backend.learning_models import CurriculumGraph, KnowledgeNode, LearningEvidence, QuestionKnowledgeLink
from xueban_backend.learning_projection import project_student, published_graph
from xueban_backend.learning_rules import derive_evidence


def _current_links(session, question_id):
    """Tests explicitly select the unique current observation, as the API does."""
    question = session.get(Entity, question_id)
    cases = [row for row in session.scalars(select(Entity).where(Entity.homework_id == question.homework_id,
             Entity.kind == "case")) if row.document["question_id"] == question_id
             and row.document.get("lifecycle", "active") == "active"]
    assert len(cases) == 1
    case = cases[0].document
    attempt = session.get(Entity, case["current_attempt_id"])
    return scoped_links(session, question_id, attempt=attempt, decision=case.get("current_decision"))


def ai_case(flow, labels=None, patch=None):
    prepare_latest_math_curriculum(flow)

    def evaluate(items):
        item = items[0]
        item["subject"] = "数学"
        item["assessment"] = assessment("correct")
        item["assessment"]["knowledge_points"] = labels if labels is not None else ["有理数的加减法"]
        if patch:
            patch(item)

    enable_page_assessments(flow, evaluate)
    return flow.ready()


def test_match_only_unique_id_full_name_or_registered_exact_alias():
    rational = SimpleNamespace(id="kp-rational-add-sub", name="有理数的加减法")
    polynomial = SimpleNamespace(id="kp-polynomial-add-sub", name="整式的加减")
    nodes = [rational, polynomial]
    assert _match_node("kp-rational-add-sub", nodes) is rational
    assert _match_node("有理数的加减法", nodes) is rational
    assert _match_node("有理数加减法", nodes) is rational
    assert _match_node("整式加减法", nodes) is polynomial
    for label in ("加减", "加法", "有理数", "整式", "有理数加减法的应用", "有理数的乘法"):
        assert _match_node(label, nodes) is None
    assert _match_node(rational.name, [rational, SimpleNamespace(id="another-kp", name=rational.name)]) is None
    assert _match_node("a-b", [SimpleNamespace(id="kp-plus", name="a+b")]) is None
    assert _match_node("x²", [SimpleNamespace(id="kp-x2", name="x2")]) is None
    # A node with reused ID but a changed meaning does not inherit old aliases.
    assert _match_node("有理数加减法", [SimpleNamespace(id=rational.id, name="有理数的乘除法")]) is None


@pytest.mark.parametrize("link_patch", [
    {"question_revision": 1}, {"graph_version": "old-graph"},
    {"question_revision": None}, {"graph_version": None},
])
def test_evidence_rejects_stale_or_unversioned_knowledge_links(link_patch):
    kwargs = dict(
        cases=[{"id": "case-1", "question_id": "question-1", "homework_id": "homework-1", "attempt_ids": ["attempt-1"]}],
        attempts=[{"id": "attempt-1", "parent_attempt_id": None, "recognition_revision": 1,
                   "captured_question_revision": 1, "occurred_at": "2026-10-21T00:00:00Z"}],
        decisions=[{"id": "decision-1", "origin": "staff", "validity": "confirmed", "verdict": "incorrect",
                    "created_at": "2026-10-21T00:01:00Z", "input_versions": {
                        "attempt_id": "attempt-1", "question_revision": 2, "attempt_recognition_revision": 1}}],
        questions={"question-1": {"question_id": "question-1", "revision": 2}},
        hints=[], conditions={}, stale_reasons={}, graph_version="current-graph",
    )
    current = {"id": "link-1", "question_id": "question-1", "question_revision": 2,
               "graph_version": "current-graph", "knowledge_id": "kp-old", "status": "confirmed",
               "source": "staff", "role": "primary", "created_at": "2026-10-21T00:02:00Z"}
    assert len(derive_evidence(**kwargs, links=[current])) == 1
    assert derive_evidence(**kwargs, links=[{**current, **link_patch}]) == []


def test_clear_first_correct_answer_maps_automatically_with_model_source_once(flow):
    case = ai_case(flow)
    # Positive mapping is driven by the learning worker, not by a mistake or
    # opening the practice UI.
    learning(flow).drain()
    learning(flow).drain()
    assert evidence_count(flow) == 1
    [state] = knowledge(flow)["states"]
    assert state["model_evidence_count"] == 1
    assert state["staff_verified_evidence_count"] == 0
    assert state["independent_evidence_count"] == 0
    assert state["observed_error_count"] == 0
    assert state["learning_status"] == "no_judgment"
    with Session(flow.database.engine) as session:
        [mapping] = list(_current_links(session, case["question_id"]).values())
        assert (mapping.source, mapping.status, mapping.question_revision) == ("model", "confirmed", 1)
        [row] = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        assert row.document["source_role"] == "model"
        assert row.document["event_type"] == "school_first_attempt"
        assert row.document["graph_version"] == mapping.graph_version


@pytest.mark.parametrize("flag", ["uncertain", "disagreement", "provider_failure"])
def test_uncertain_positive_answer_cannot_create_confirmed_link(flow, flag):
    def patch(item):
        if flag == "uncertain":
            item["uncertainties"] = ["字迹不清"]
        else:
            item["assessment"]["verification"]["status"] = "conflict" if flag == "disagreement" else "degraded"
            item["assessment"]["verification"]["reason_codes"] = [
                "answer_disagreement" if flag == "disagreement" else "provider_unavailable"]
    case = ai_case(flow, patch=patch)
    learning(flow).drain()
    assert evidence_count(flow) == 0
    with Session(flow.database.engine) as session:
        assert _current_links(session, case["question_id"]) == {}


def test_generic_label_stays_unmapped_and_does_not_guess_a_primary_node(flow):
    case = ai_case(flow, labels=["加法"])
    learning(flow).drain()
    assert evidence_count(flow) == 0
    with Session(flow.database.engine) as session:
        assert _current_links(session, case["question_id"]) == {}
        assert session.get(Entity, case["current_decision"]["id"]).document["knowledge_points"] == ["加法"]


def test_teacher_rejection_is_not_replaced_by_positive_auto_mapping(flow):
    case = ai_case(flow)
    teacher(flow)
    rejected = link(flow, case, status="rejected")
    learning(flow).drain()
    assert evidence_count(flow) == 0
    with Session(flow.database.engine) as session:
        assert _current_links(session, case["question_id"])["kp-rational-add-sub"].id == rejected["id"]


def test_stale_judgment_versions_cannot_create_a_positive_mapping(flow):
    case = ai_case(flow)
    with Session(flow.database.engine) as session:
        decision = session.get(Entity, case["current_decision"]["id"])
        decision.document = {**decision.document, "input_versions": {
            **decision.document["input_versions"], "question_revision": 99}}
        stored_case = session.get(Entity, case["id"])
        stored_case.document = {**stored_case.document, "current_decision": decision.document}
        session.commit()
    learning(flow).drain()
    assert evidence_count(flow) == 0
    with Session(flow.database.engine) as session:
        assert _current_links(session, case["question_id"]) == {}


def test_question_revision_requires_a_new_link_before_new_judgment_can_tag(flow):
    case = reviewed(flow)
    teacher(flow)
    old = link(flow, case)
    learning(flow).drain()
    assert evidence_count(flow) == 1
    question = flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]
    revised = flow.post(f"/v1/questions/{case['question_id']}/revisions", {
        "expected_revision": question["revision"], "stem_text": "2 + 1 = ?",
        "regions": question["regions"], "reason": "核对原图修正题干"})
    assert revised.status_code == 201, revised.text
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    flow.start_check(case)
    assert flow.worker.run_once()
    learning(flow).drain()
    assert evidence_count(flow) == 0
    assert evidence_count(flow, "invalidated") == 1
    with Session(flow.database.engine) as session:
        assert _current_links(session, case["question_id"]) == {}
        assert session.get(QuestionKnowledgeLink, old["id"]).question_revision == 1
    current = flow.client.get(f"/v1/questions/{case['question_id']}/knowledge-links").json()
    assert current["items"] == [] and current["question_revision"] == 2
    fresh = link(flow, case)
    assert fresh["supersedes_id"] == old["id"]
    learning(flow).drain()
    assert evidence_count(flow) == 1
    with Session(flow.database.engine) as session:
        row = session.scalar(select(LearningEvidence).where(LearningEvidence.validity == "valid"))
        assert row.document["question_revision"] == 2
        assert row.document["captured_question_revision"] == 1


def test_graph_replacement_with_reused_node_ids_does_not_reuse_old_links(flow):
    case = reviewed(flow)
    teacher(flow)
    old_link = link(flow, case)
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        old_graph = published_graph(session)
        graph = CurriculumGraph(version="test-revised-graph", subject="数学", title="合成目录新版本",
                                status="published", review_status="publisher_latest_reviewed",
                                source_note="仅测试", published_at=datetime.now(timezone.utc) + timedelta(days=1))
        session.add(graph)
        session.flush()
        for node in session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == old_graph.version)).all():
            session.add(KnowledgeNode(
                graph_version=graph.version, id=node.id, node_type=node.node_type, parent_id=node.parent_id,
                name=node.name, definition=node.definition, observable_objective=node.observable_objective,
                curriculum_refs=deepcopy(node.curriculum_refs), coverage_facets=deepcopy(node.coverage_facets),
                misconception_codes=deepcopy(node.misconception_codes), sort_order=node.sort_order))
        session.commit()
    learning(flow).drain()
    assert evidence_count(flow) == 0
    assert knowledge(flow)["states"][0]["learning_status"] == "no_judgment"
    with Session(flow.database.engine) as session:
        assert _current_links(session, case["question_id"]) == {}
    assert flow.client.get(f"/v1/questions/{case['question_id']}/knowledge-links").json()["items"] == []
    fresh_link = link(flow, case)
    assert fresh_link["supersedes_id"] == old_link["id"]
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        [row] = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        assert row.document["link_id"] == fresh_link["id"]
        assert row.document["graph_version"] == "test-revised-graph"


def test_legacy_ambiguous_auto_link_returns_to_system_candidate_then_can_be_remapped(flow):
    case = ai_case(flow, labels=["加法"])
    old_id = "qkl-legacy-ambiguous"
    with Session(flow.database.engine) as session:
        graph = published_graph(session)
        old = QuestionKnowledgeLink(
            id=old_id, tenant_id="tenant_a", student_id="student_a",
            homework_id=flow.homework_id, question_id=case["question_id"], question_revision=1,
            knowledge_id="kp-rational-add-sub", graph_version=graph.version, role="primary",
            source="model", status="confirmed", rationale="AI判定错题时识别到知识点：加法",
            actor_id=None, supersedes_id=None, created_at=datetime.now(timezone.utc))
        session.add(old)
        session.commit()
    learning(flow).drain()
    learning(flow).drain()
    assert evidence_count(flow) == 0
    with Session(flow.database.engine) as session:
        assert len(session.scalars(select(QuestionKnowledgeLink)).all()) == 2
        assert _current_links(session, case["question_id"]) == {}  # unknown legacy scope never becomes current
        pending = session.scalar(select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.supersedes_id == old_id))
        assert (pending.source, pending.status, pending.supersedes_id) == ("model", "candidate", old_id)
        assert old_id in pending.rationale and "[system_migration:r1.4]" in pending.rationale
        assert pending.actor_id is None
        assert session.get(QuestionKnowledgeLink, old_id).status == "confirmed"
        decision = session.get(Entity, case["current_decision"]["id"])
        decision.document = {**decision.document, "knowledge_points": ["有理数的加减法"]}
        stored_case = session.get(Entity, case["id"])
        stored_case.document = {**stored_case.document, "current_decision": decision.document}
        pending_id = pending.id
        project_student(session, "tenant_a", "student_a")
        session.commit()
        current = _current_links(session, case["question_id"])["kp-rational-add-sub"]
        assert (current.source, current.status, current.supersedes_id) == ("model", "confirmed", None)
        assert current.id != pending_id and current.observation_bindings[0]["decision_id"] == decision.id
    assert evidence_count(flow) == 1


@pytest.mark.parametrize("source,label", [
    ("model", "有理数的加减法"), ("staff", "加法"),
])
def test_legacy_exact_model_link_and_teacher_link_are_not_rewritten(flow, source, label):
    case = ai_case(flow, labels=[])
    with Session(flow.database.engine) as session:
        graph = published_graph(session)
        session.add(QuestionKnowledgeLink(
            id="qkl-legacy-safe", tenant_id="tenant_a", student_id="student_a",
            homework_id=flow.homework_id, question_id=case["question_id"], question_revision=1,
            knowledge_id="kp-rational-add-sub", graph_version=graph.version, role="primary",
            source=source, status="confirmed", rationale=f"AI判定错题时识别到知识点：{label}",
            actor_id=None, supersedes_id=None, created_at=datetime.now(timezone.utc)))
        session.commit()
    learning(flow).drain()
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        assert len(session.scalars(select(QuestionKnowledgeLink)).all()) == 1
        stored = session.get(QuestionKnowledgeLink, "qkl-legacy-safe")
        assert stored.status == "confirmed" and stored.observation_bindings is None
        current = _current_links(session, case["question_id"])
        if source == "staff":
            assert current["kp-rational-add-sub"].id == stored.id
        else:
            assert current == {}  # Unknown legacy observation stays historical.
        assert len(session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()) == (1 if source == "staff" else 0)
    assert evidence_count(flow, "invalidated") == 0


def test_old_graph_suggestion_job_cannot_write_into_new_graph(flow):
    case = reviewed(flow)
    job = SimpleNamespace(student_id="student_a", payload={"question_id": case["question_id"], "question_revision": 1,
                                   "graph_version": "superseded-version"})
    client = SimpleNamespace(settings=SimpleNamespace(enabled=True))
    with Session(flow.database.engine) as session:
        result = suggest_links(session, job, SimpleNamespace(client=client))
    assert result == {"status": "stale"}
