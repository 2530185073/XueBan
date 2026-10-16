"""Each learner's long-term agent: memories, case context and staff summaries.

The agent is per-learner application state, not a per-learner model. Facts are
summarized by deterministic rules from verified evidence; the model may only
propose memories, which staff confirm. Every memory and every run lists the
evidence it rests on, and withdrawing that evidence invalidates them.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .auth import Actor
from .correction_models import Entity
from .device_auth import utc
from .errors import Problem
from .learning_models import (AgentMemoryItem, AgentRun, KnowledgeNode, LearningEvidence, LearningProjection,
                              StudentKnowledgeState)
from .governance import register_downstream_purger
from .learning_projection import (enqueue, new_id, published_graph, register_evidence_listener, register_job_handler, require_published_graph,
                                  register_projection_followup)
from .models import Homework
from .providers.prompts import Role

RULE_CONFIRMER = "rule:memory-r1"
VALIDITY = {"recurring_difficulty": timedelta(days=60), "progress_milestone": timedelta(days=180),
            "effective_support": timedelta(days=120), "support_agreement": timedelta(days=60),
            "learning_goal": timedelta(days=60), "interface_preference": timedelta(days=365)}
STAFF_CATEGORIES = {"effective_support", "support_agreement", "learning_goal", "recurring_difficulty"}
STUDENT_CATEGORIES = {"interface_preference", "learning_goal"}
ACTIVE = ("proposed", "confirmed")
SUMMARY_INSTRUCTIONS = (
    "你为坐班老师整理一名学生的学习情况摘要。只依据 task_input 中的知识状态、证据和已确认记忆，不推测性格、态度、家庭或心理。"
    "每条 claims 必须引用支持它的 evidence_ids；没有证据支持的话不写。状态和计数直接引用给定字段，不自行重算。"
    "资料不足时 status 用 needs_evidence 并如实说明。next_action 只写支持当前学校作业的最小一步，不安排额外练习或补课。"
    "memory_proposals 只在证据反复支持时提出，最多 3 条，由老师确认后才生效。")


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return utc(value).isoformat() if value else None


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _date(value):
    return _parse(value).astimezone(timezone(timedelta(hours=8))).strftime("%-m月%-d日")


def _hash(ids):
    return hashlib.sha256(",".join(sorted(ids)).encode()).hexdigest()[:16]


def _active(memory, at):
    return memory.status == "confirmed" and (memory.valid_until is None or utc(memory.valid_until) > at)


def memory_dict(memory, at=None):
    at = at or now()
    status = "expired" if memory.status == "confirmed" and memory.valid_until and utc(memory.valid_until) <= at else memory.status
    return {"id": memory.id, "category": memory.category, "content": memory.content, "knowledge_id": memory.knowledge_id,
            "evidence_ids": memory.evidence_ids, "source": memory.source, "status": status, "status_reason": memory.status_reason,
            "proposed_by": memory.proposed_by, "confirmed_by": memory.confirmed_by, "confirmed_at": iso(memory.confirmed_at),
            "valid_until": iso(memory.valid_until), "supersedes_id": memory.supersedes_id,
            "revision": memory.revision, "created_at": iso(memory.created_at)}


def rule_proposals(states: dict, evidence: dict, names: dict) -> list[dict]:
    """Factual summaries of verified evidence; no cause, trait or forecast."""
    output = []
    for knowledge_id, state in states.items():
        rows = sorted((evidence[i] for i in state["evidence_ids"] if i in evidence), key=lambda e: _parse(e["occurred_at"]))
        name = names.get(knowledge_id, knowledge_id)
        errors = [e for e in rows if e["result"] in ("incorrect", "partial")]
        if state["learning_status"] == "needs_help_now" and len({e["attempt_id"] for e in errors}) >= 2:
            ids = [e["id"] for e in errors]
            output.append({"category": "recurring_difficulty", "knowledge_id": knowledge_id, "evidence_ids": ids,
                           "rule_key": f"difficulty:{knowledge_id}:{_hash(ids)}",
                           "content": f"{_date(errors[0]['occurred_at'])}至{_date(errors[-1]['occurred_at'])}，"
                                      f"「{name}」有 {len(errors)} 次经老师核实的错误作答。"})
        if state["learning_status"] == "stable_for_now":
            last_error = _parse(state["last_error_at"]) if state["last_error_at"] else None
            wins = [e for e in rows if e["independent"] and e["result"] == "correct"
                    and (last_error is None or _parse(e["occurred_at"]) > last_error)]
            ids = [e["id"] for e in wins]
            dates = sorted({_date(e["occurred_at"]) for e in wins})
            output.append({"category": "progress_milestone", "knowledge_id": knowledge_id, "evidence_ids": ids,
                           "rule_key": f"milestone:{knowledge_id}:{_hash(ids)}",
                           "content": f"「{name}」在 {len(dates)} 个不同日期独立完成了学校新题（{'、'.join(dates)}）。"})
    return output


@register_projection_followup
def refresh_rule_memories(session, tenant_id, student_id, worker):
    graph = published_graph(session)
    if graph is None:
        return
    names = {n.id: n.name for n in session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version))}
    states = {r.knowledge_id: r.document for r in session.scalars(
        select(StudentKnowledgeState).where(StudentKnowledgeState.student_id == student_id))}
    evidence = {r.id: {**r.document, "id": r.id} for r in session.scalars(
        select(LearningEvidence).where(LearningEvidence.student_id == student_id, LearningEvidence.validity == "valid"))}
    existing = list(session.scalars(select(AgentMemoryItem).where(AgentMemoryItem.student_id == student_id,
                                                                  AgentMemoryItem.source == "system_rule")))
    keys = {m.rule_key for m in existing}
    stamp = now()
    for proposal in rule_proposals(states, evidence, names):
        if proposal["rule_key"] in keys:
            continue
        previous = next((m for m in existing if m.status == "confirmed" and m.category == proposal["category"]
                         and m.knowledge_id == proposal["knowledge_id"]), None)
        memory = AgentMemoryItem(id=new_id("memory"), tenant_id=tenant_id, student_id=student_id, source="system_rule",
                                 status="confirmed", confirmed_by=RULE_CONFIRMER, confirmed_at=stamp, proposed_by=RULE_CONFIRMER,
                                 valid_until=stamp + VALIDITY[proposal["category"]], supersedes_id=previous.id if previous else None,
                                 revision=1, created_at=stamp, updated_at=stamp, **proposal)
        session.add(memory)
        if previous:
            previous.status, previous.status_reason, previous.revision, previous.updated_at = "superseded", "newer_evidence", previous.revision + 1, stamp


@register_evidence_listener
def invalidate_dependents(session, tenant_id, student_id, invalidated, changed):
    withdrawn = {row.id for row in invalidated}
    if not withdrawn:
        return
    stamp = now()
    for memory in session.scalars(select(AgentMemoryItem).where(AgentMemoryItem.student_id == student_id,
                                                                AgentMemoryItem.status.in_(ACTIVE))):
        if withdrawn & set(memory.evidence_ids):
            memory.status, memory.status_reason = "invalidated", "evidence_withdrawn"
            memory.revision, memory.updated_at = memory.revision + 1, stamp
    for run in session.scalars(select(AgentRun).where(AgentRun.student_id == student_id, AgentRun.status.in_(("completed", "needs_human")))):
        if withdrawn & set(run.context.get("evidence_ids", [])):
            run.status = "stale"


@register_downstream_purger
def purge_agent(session, tenant_id, student_id):
    for model in (AgentRun, AgentMemoryItem):
        session.execute(delete(model).where(model.student_id == student_id, model.tenant_id == tenant_id))


def summary_schema(evidence_ids):
    ids = {"type": "array", "minItems": 1, "maxItems": 10, "uniqueItems": True, "items": {"enum": evidence_ids}}
    text = lambda n: {"type": "string", "minLength": 1, "maxLength": n}  # noqa: E731
    return {"type": "object", "additionalProperties": False,
            "required": ["status", "summary", "claims", "next_action", "memory_proposals"], "properties": {
                "status": {"enum": ["ready", "needs_evidence", "needs_human"]}, "summary": text(300),
                "claims": {"type": "array", "maxItems": 8, "items": {"type": "object", "additionalProperties": False,
                           "required": ["text", "evidence_ids", "certainty"], "properties": {
                               "text": text(200), "evidence_ids": ids, "certainty": {"enum": ["low", "medium", "high"]}}}},
                "next_action": text(200),
                "memory_proposals": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False,
                                     "required": ["category", "content", "evidence_ids"], "properties": {
                                         "category": {"enum": ["recurring_difficulty", "effective_support", "learning_goal"]},
                                         "content": text(120), "evidence_ids": ids}}}}}


def build_summary_context(session, student_id):
    graph = require_published_graph(session)
    names = {n.id: n.name for n in session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version))}
    projection = session.get(LearningProjection, student_id)
    states = [r.document for r in session.scalars(select(StudentKnowledgeState).where(StudentKnowledgeState.student_id == student_id))]
    rows = list(session.scalars(select(LearningEvidence).where(LearningEvidence.student_id == student_id, LearningEvidence.validity == "valid")
                                .order_by(LearningEvidence.occurred_at.desc()).limit(30)))
    evidence = []
    for row in rows:
        question = session.get(Entity, row.document["question_id"])
        evidence.append({"id": row.id, "knowledge": names.get(row.knowledge_id), "date": row.document["occurred_at"][:10],
                         "result": row.document["result"], "assistance": row.document["assistance_level"],
                         "event_type": row.document["event_type"],
                         "question": (question.document.get("stem_text") or "")[:200] if question else None})
    memories = [{"id": m.id, "category": m.category, "content": m.content} for m in session.scalars(
        select(AgentMemoryItem).where(AgentMemoryItem.student_id == student_id, AgentMemoryItem.status == "confirmed")) if _active(m, now())]
    payload = {"learner": "learner_1", "states": [{"knowledge": names.get(s["knowledge_id"]), **{k: s[k] for k in (
        "learning_status", "reason_codes", "evidence_sufficiency", "independent_evidence_count", "observed_error_count",
        "last_error_at", "last_independent_success_at")}} for s in states], "evidence": evidence, "memories": memories}
    context = {"state_version": projection.state_version if projection else 0,
               "evidence_revision": projection.evidence_revision if projection else 0,
               "evidence_ids": [e["id"] for e in evidence], "memory_ids": [m["id"] for m in memories],
               "graph_version": graph.version}
    return context, payload


@register_job_handler("agent_summary")
def run_summary(session, job, worker):
    run = session.get(AgentRun, job.payload["run_id"])
    if run is None or run.status != "queued":
        return
    context, payload = build_summary_context(session, job.student_id)
    run.context = context
    if not context["evidence_ids"]:
        run.status, run.completed_at = "completed", now()
        run.output = {"status": "needs_evidence", "summary": "尚无经老师核实并关联知识点的作答，暂不能形成学习情况摘要。",
                      "claims": [], "next_action": "继续在学校作业中正常核实作答与关联知识点。", "memory_proposals": []}
        return
    client = worker.client
    if client is None or not client.settings.enabled:
        run.status, run.failure_code, run.completed_at = "failed", "provider_disabled", now()
        return
    result = client.generate(role=Role.KNOWLEDGE_EXTRACT, text=json.dumps(payload, ensure_ascii=False),
                             output_schema=summary_schema(context["evidence_ids"]), thinking=True, max_tokens=2048,
                             instructions=SUMMARY_INSTRUCTIONS)
    output = result.data
    run.model_metadata = {"model": result.metadata.model, "latency_ms": result.metadata.latency_ms}
    run.output, run.completed_at = output, now()
    run.status = "needs_human" if output["status"] == "needs_human" else "completed"
    stamp = now()
    for proposal in output["memory_proposals"]:
        session.add(AgentMemoryItem(id=new_id("memory"), tenant_id=job.tenant_id, student_id=job.student_id,
                                    category=proposal["category"], content=proposal["content"], knowledge_id=None,
                                    evidence_ids=proposal["evidence_ids"], source="model", status="proposed",
                                    proposed_by=f"agent_run:{run.id}", revision=1, created_at=stamp, updated_at=stamp))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryInput(Strict):
    category: Literal["recurring_difficulty", "effective_support", "support_agreement", "learning_goal", "interface_preference"]
    content: str = Field(min_length=2, max_length=120)
    knowledge_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    supersedes_id: str | None = Field(default=None, max_length=64)


class SummaryRequest(Strict):
    purpose: Literal["staff_summary"]


def install_agent(app, engine, cfg, database_session, current_actor):
    from .app import require_student
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]
    staff = lambda actor: bool(set(actor.roles) & {"sitting_teacher", "campus_admin"})  # noqa: E731

    def load_memory(session, actor, memory_id):
        memory = session.scalar(select(AgentMemoryItem).where(AgentMemoryItem.id == memory_id).with_for_update())
        if memory is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, memory.student_id)
        return memory

    @app.get("/v1/students/{student_id}/memories", operation_id="listAgentMemories")
    def list_memories(student_id: str, actor: ActorDep, session: SessionDep, include_inactive: bool = False):
        require_student(session, actor, student_id)
        at = now()
        rows = session.scalars(select(AgentMemoryItem).where(AgentMemoryItem.student_id == student_id)
                               .order_by(AgentMemoryItem.created_at.desc()))
        items = [memory_dict(m, at) for m in rows]
        if not staff(actor):
            # Difficulty records are staff-facing evidence summaries; the child sees them only as gentle tips.
            items = [m for m in items if m["status"] == "confirmed" and m["category"] != "recurring_difficulty"]
        elif not include_inactive:
            items = [m for m in items if m["status"] in ACTIVE]
        return {"student_id": student_id, "items": items,
                "notice": "学伴只记住有出处的事实和老师确认的约定；证据被撤销后相关记忆自动失效。"}

    @app.post("/v1/students/{student_id}/memories", status_code=201, operation_id="createAgentMemory")
    def create_memory(student_id: str, payload: MemoryInput, actor: ActorDep, session: SessionDep):
        student = require_student(session, actor, student_id)
        is_staff = staff(actor)
        allowed = STAFF_CATEGORIES if is_staff else STUDENT_CATEGORIES
        if payload.category not in allowed:
            raise Problem(403, "authorization", "forbidden", "该类记忆需要由坐班老师确认。")
        valid = set(session.scalars(select(LearningEvidence.id).where(LearningEvidence.student_id == student_id,
                                                                     LearningEvidence.validity == "valid")))
        if not set(payload.evidence_ids) <= valid:
            raise Problem(422, "validation", "invalid_request", "记忆只能引用该学生当前有效的证据。")
        if payload.category == "recurring_difficulty" and not payload.evidence_ids:
            raise Problem(422, "validation", "invalid_request", "学科困难必须引用已核实的作答证据。")
        stamp = now()
        previous = None
        if payload.supersedes_id:
            previous = load_memory(session, actor, payload.supersedes_id)
            if previous.student_id != student_id or previous.status not in ACTIVE:
                raise Problem(409, "conflict", "revision_conflict", "被替换的记忆已变化，请刷新后核实。")
            previous.status, previous.status_reason, previous.revision, previous.updated_at = "superseded", "replaced", previous.revision + 1, stamp
        memory = AgentMemoryItem(id=new_id("memory"), tenant_id=student.tenant_id, student_id=student_id, category=payload.category,
                                 content=payload.content.strip(), knowledge_id=payload.knowledge_id, evidence_ids=payload.evidence_ids,
                                 source="staff" if is_staff else "student", status="confirmed", proposed_by=actor.id,
                                 confirmed_by=actor.id, confirmed_at=stamp, valid_until=stamp + VALIDITY[payload.category],
                                 supersedes_id=previous.id if previous else None, revision=1, created_at=stamp, updated_at=stamp)
        session.add(memory)
        session.commit()
        return memory_dict(memory)

    @app.post("/v1/memories/{memory_id}/confirm", operation_id="confirmAgentMemory")
    def confirm_memory(memory_id: str, actor: ActorDep, session: SessionDep):
        if not staff(actor):
            raise Problem(403, "authorization", "forbidden", "需要坐班老师确认。")
        memory = load_memory(session, actor, memory_id)
        if memory.status != "proposed":
            raise Problem(409, "conflict", "revision_conflict", "该记忆不是待确认状态。")
        stamp = now()
        memory.status, memory.confirmed_by, memory.confirmed_at = "confirmed", actor.id, stamp
        memory.valid_until, memory.revision, memory.updated_at = stamp + VALIDITY[memory.category], memory.revision + 1, stamp
        session.commit()
        return memory_dict(memory)

    @app.post("/v1/memories/{memory_id}/retract", operation_id="retractAgentMemory")
    def retract_memory(memory_id: str, actor: ActorDep, session: SessionDep):
        memory = load_memory(session, actor, memory_id)
        if not staff(actor) and not (memory.source == "student" and memory.proposed_by == actor.id):
            raise Problem(403, "authorization", "forbidden", "只能撤回自己设置的偏好。")
        if memory.status not in ACTIVE:
            raise Problem(409, "conflict", "revision_conflict", "该记忆已失效。")
        memory.status, memory.status_reason = "retracted", "retracted_by_" + ("staff" if staff(actor) else "student")
        memory.revision, memory.updated_at = memory.revision + 1, now()
        session.commit()
        return memory_dict(memory)

    @app.get("/v1/cases/{case_id}/agent-context", operation_id="getCaseAgentContext")
    def case_context(case_id: str, actor: ActorDep, session: SessionDep):
        """Personal reminders for this question, citing the memories and evidence used."""
        from .learning_api import current_links
        case = session.get(Entity, case_id)
        if case is None or case.kind != "case":
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        homework = session.get(Homework, case.homework_id)
        student = require_student(session, actor, homework.student_id)
        graph = require_published_graph(session)
        names = {n.id: n for n in session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version))}
        links = [l for l in current_links(session, case.document["question_id"]).values() if l.status == "confirmed"]
        knowledge_ids = [l.knowledge_id for l in links]
        states = {r.knowledge_id: r.document for r in session.scalars(select(StudentKnowledgeState).where(
            StudentKnowledgeState.student_id == student.id, StudentKnowledgeState.knowledge_id.in_(knowledge_ids)))}
        at = now()
        memories = [m for m in session.scalars(select(AgentMemoryItem).where(AgentMemoryItem.student_id == student.id,
                                                                             AgentMemoryItem.status == "confirmed")) if _active(m, at)]
        tips = []
        for memory in memories:
            if memory.knowledge_id in knowledge_ids and memory.category == "recurring_difficulty":
                node = names[memory.knowledge_id]
                tips.append({"kind": "past_difficulty", "memory_id": memory.id, "evidence_ids": memory.evidence_ids,
                             "text": f"学伴记得：「{node.name}」之前练过几次，这里容易卡一下。这次可以先对照：{node.observable_objective or node.name}"})
            elif memory.knowledge_id in knowledge_ids and memory.category == "progress_milestone":
                tips.append({"kind": "past_progress", "memory_id": memory.id, "evidence_ids": memory.evidence_ids,
                             "text": f"学伴记得：「{names[memory.knowledge_id].name}」你已经在不同日期独立做对过，相信自己再检查一遍。"})
            elif memory.category in ("effective_support", "support_agreement") and memory.knowledge_id in (None, *knowledge_ids):
                tips.append({"kind": "agreed_support", "memory_id": memory.id, "evidence_ids": memory.evidence_ids,
                             "text": f"老师和你约定过：{memory.content}"})
        projection = session.get(LearningProjection, student.id)
        context = {"case_id": case_id, "knowledge_ids": knowledge_ids, "state_version": projection.state_version if projection else 0,
                   "memory_ids": sorted(t["memory_id"] for t in tips),
                   "evidence_ids": sorted({e for t in tips for e in t["evidence_ids"]})}
        reusable = session.scalar(select(AgentRun).where(AgentRun.student_id == student.id, AgentRun.purpose == "hint_context",
                                                         AgentRun.subject_ref == case_id, AgentRun.status == "completed")
                                  .order_by(AgentRun.created_at.desc()))
        if reusable is None or reusable.context != context:
            reusable = AgentRun(id=new_id("agentrun"), tenant_id=student.tenant_id, student_id=student.id, purpose="hint_context",
                                status="completed", subject_ref=case_id, context=context, output={"tips": tips},
                                requested_by=actor.id, created_at=at, completed_at=at)
            session.add(reusable)
            session.commit()
        return {"run_id": reusable.id, "case_id": case_id, "tips": tips,
                "knowledge": [{"id": k, "name": names[k].name, "learning_status": states.get(k, {}).get("learning_status", "no_judgment")}
                              for k in knowledge_ids if k in names],
                "notice": "提醒只来自老师核实过的作答和约定，不包含答案。"}

    @app.post("/v1/students/{student_id}/agent-runs", status_code=202, operation_id="requestAgentRun")
    def request_run(student_id: str, payload: SummaryRequest, actor: ActorDep, session: SessionDep):
        if not staff(actor):
            raise Problem(403, "authorization", "forbidden", "需要坐班老师发起。")
        student = require_student(session, actor, student_id)
        pending = session.scalar(select(AgentRun).where(AgentRun.student_id == student_id, AgentRun.purpose == "staff_summary",
                                                        AgentRun.status == "queued"))
        if pending is None:
            pending = AgentRun(id=new_id("agentrun"), tenant_id=student.tenant_id, student_id=student_id, purpose="staff_summary",
                               status="queued", context={}, requested_by=actor.id, created_at=now())
            session.add(pending)
            enqueue(session, student.tenant_id, student_id, "agent_summary", {"run_id": pending.id}, f"summary:{pending.id}")
            session.commit()
        return run_dict(pending)

    @app.get("/v1/students/{student_id}/agent-runs", operation_id="listAgentRuns")
    def list_runs(student_id: str, actor: ActorDep, session: SessionDep):
        if not staff(actor):
            raise Problem(403, "authorization", "forbidden", "需要坐班老师查看。")
        require_student(session, actor, student_id)
        rows = session.scalars(select(AgentRun).where(AgentRun.student_id == student_id, AgentRun.purpose == "staff_summary")
                               .order_by(AgentRun.created_at.desc()).limit(20))
        return {"items": [run_dict(r) for r in rows]}


def run_dict(run):
    return {"id": run.id, "purpose": run.purpose, "status": run.status, "context": run.context, "output": run.output,
            "model_metadata": run.model_metadata, "failure_code": run.failure_code, "created_at": iso(run.created_at),
            "completed_at": iso(run.completed_at)}
