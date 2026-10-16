"""Model-proposed question-to-knowledge links. Candidates only; staff confirm.

The model chooses from the published concept list (an enum in the output
schema), so it cannot invent a knowledge ID. Question text is user data and is
never placed in the instructions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy import select

from .correction_models import Entity
from .learning_models import KnowledgeNode, LearningOutbox, QuestionKnowledgeLink, StudentLearningProfile
from .learning_projection import enqueue, new_id, published_graph, register_job_handler, register_projection_followup
from .models import Homework
from .providers.prompts import Role

MAX_LINKS = 3
INSTRUCTIONS = (
    "从 candidates 中选择这道题直接考查的知识点，最多 3 个。primary 只给最直接考查、最可能出错的 1 个，其余为 secondary。"
    "只能使用 candidates 中的 id；拿不准时返回空 links。rationale 用一句话说明题目哪一步用到该知识点，不判断学生对错，"
    "不评价学生能力。")


def _candidates(session, graph_version, grade):
    nodes = session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph_version,
                                                        KnowledgeNode.node_type.in_(("concept", "skill")))
                            .order_by(KnowledgeNode.sort_order))
    output = []
    for node in nodes:
        grades = {ref.get("grade") for ref in node.curriculum_refs}
        if grade is None or not grades or min(g for g in grades if g is not None) <= grade:
            output.append(node)
    return output


def schema(ids):
    return {"type": "object", "additionalProperties": False, "required": ["links"], "properties": {"links": {
        "type": "array", "maxItems": MAX_LINKS, "items": {"type": "object", "additionalProperties": False,
            "required": ["knowledge_id", "role", "rationale"], "properties": {
                "knowledge_id": {"enum": ids}, "role": {"enum": ["primary", "secondary"]},
                "rationale": {"type": "string", "minLength": 1, "maxLength": 200}}}}}}


@register_job_handler("suggest_links")
def suggest_links(session, job, worker):
    client = worker.client
    if client is None or not client.settings.enabled:
        return {"status": "provider_disabled"}
    question = session.get(Entity, job.payload["question_id"])
    if question is None or question.revision != job.payload["question_revision"] or not question.document.get("stem_text"):
        return {"status": "stale"}
    if session.scalar(select(QuestionKnowledgeLink.id).where(QuestionKnowledgeLink.question_id == question.id)):
        return {"status": "already_linked"}
    graph = published_graph(session)
    if graph is None:
        return {"status": "no_published_graph"}
    profile = session.get(StudentLearningProfile, job.student_id)
    nodes = _candidates(session, graph.version, (profile.document.get("grade") if profile else None))
    ids = [n.id for n in nodes]
    text = json.dumps({"question": question.document["stem_text"],
                       "candidates": [{"id": n.id, "name": n.name, "objective": n.observable_objective} for n in nodes]},
                      ensure_ascii=False)
    result = client.generate(role=Role.KNOWLEDGE_EXTRACT, text=text, output_schema=schema(ids), thinking=False,
                             max_tokens=1024, instructions=INSTRUCTIONS)
    links, seen, primary = [], set(), False
    for item in result.data["links"]:
        if item["knowledge_id"] in seen:
            continue
        role = "primary" if item["role"] == "primary" and not primary else "secondary"
        primary = primary or role == "primary"
        seen.add(item["knowledge_id"])
        links.append(QuestionKnowledgeLink(
            id=new_id("qkl"), tenant_id=job.tenant_id, student_id=job.student_id, homework_id=question.homework_id,
            question_id=question.id, question_revision=question.revision, knowledge_id=item["knowledge_id"],
            graph_version=graph.version, role=role, source="model", status="candidate", rationale=item["rationale"],
            actor_id=None, supersedes_id=None, created_at=datetime.now(timezone.utc)))
    session.add_all(links)
    return {"status": "proposed", "count": len(links)}


@register_projection_followup
def enqueue_unmapped(session, tenant_id, student_id, worker):
    """Ask for candidates only where a staff decision exists, so cost follows real evidence."""
    if worker.client is None or not worker.client.settings.enabled:
        return
    homeworks = select(Homework.id).where(Homework.student_id == student_id, Homework.processing_blocked.is_(False))
    linked = set(session.scalars(select(QuestionKnowledgeLink.question_id).where(QuestionKnowledgeLink.student_id == student_id)))
    for case in session.scalars(select(Entity).where(Entity.homework_id.in_(homeworks), Entity.kind == "case")):
        decision = case.document.get("current_decision") or {}
        question_id = case.document["question_id"]
        if case.document.get("lifecycle", "active") != "active" or decision.get("origin") != "staff" or question_id in linked:
            continue
        question = session.get(Entity, question_id)
        key = f"links:{question_id}:{question.revision}"
        if session.scalar(select(LearningOutbox.id).where(LearningOutbox.dedupe_key == key)):
            continue
        enqueue(session, tenant_id, student_id, "suggest_links",
                {"question_id": question_id, "question_revision": question.revision}, key)
