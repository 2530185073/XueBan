"""Voluntary practice: one-step variants and next-lesson preview with an optional mini quiz.

Extra work defaults to zero. The server starts an activity only when school
tasks are complete (photographed homework all resolved, or a staff
confirmation), staff observed capacity and opened an extra-minute budget for
today, and the child says yes for this activity. Unknown counts as not met and
everything is re-checked at start. Leaving early or never starting creates no
debt. Items come from a reviewed bank tagged by knowledge point; the model only
drafts candidates. Answers are rule-graded and kept apart from knowledge state,
so taking part can never make a child look weaker.
"""
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
import re
from typing import Annotated, Literal
import unicodedata
from uuid import uuid4

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .auth import Actor
from .correction_models import Entity
from .device_auth import utc
from .errors import Problem
from .governance import register_downstream_purger
from .learning_models import (KnowledgeEdge, KnowledgeNode, OptionalWindow, PracticeItem, PracticeResponse, PracticeSession,
                              StudentKnowledgeState, StudentLearningProfile)
from .correction_core import remember_idempotent, replay_idempotent
from .learning_projection import OptionalKey, enqueue, new_id, register_job_handler, require_published_graph
from .learning_reports import SHANGHAI, local_day, register_report_section
from .models import Homework, Student
from .providers.prompts import Role

MAX_ITEMS = 3
NEEDS_PRACTICE = ("needs_help_now", "consolidating")
BANK_JOB_STUDENT = "tenant-bank"
GENERATE_INSTRUCTIONS = (
    "为托管班初中数学出少量同知识点练习题候选，供老师审核后使用。题目必须只考查 knowledge 中给出的知识点，符合人教版初中难度，"
    "题干自足、答案唯一。answer_kind 为 number 时 answer_values 写最简数值（分数写成 a/b）；choice 时题干内含 A-D 选项，"
    "answer_values 写正确选项字母；text 时写可接受的最简答案。solution_text 写简洁步骤，hint_text 只指方向不给答案。"
    "source_question 仅作题型参考，不要照抄数字，也不得包含任何学生信息。")


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return utc(value).isoformat() if value else None


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).replace("−", "-").replace("＝", "=")
    return re.sub(r"\s+", "", text).lower()


def _number(value: str):
    text = _normalize(value)
    text = re.sub(r"^[a-z]=", "", text)
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def grade(item: PracticeItem, answer: str) -> str:
    if item.answer_kind == "number":
        given = _number(answer)
        if given is None:
            return "unscorable"
        return "correct" if any(_number(v) == given for v in item.answer_values) else "incorrect"
    if item.answer_kind == "choice":
        letters = sorted(set(re.sub(r"[^A-D]", "", unicodedata.normalize("NFKC", answer).upper())))
        if not letters:
            return "unscorable"
        return "correct" if letters == sorted(set("".join(item.answer_values).upper())) else "incorrect"
    given = _normalize(answer)
    if not given:
        return "unscorable"
    return "correct" if given in {_normalize(v) for v in item.answer_values} else "incorrect"


def item_dict(item: PracticeItem, *, staff=False):
    body = {"id": item.id, "knowledge_id": item.knowledge_id, "stem_text": item.stem_text, "answer_kind": item.answer_kind,
            "difficulty": item.difficulty}
    if staff:
        body.update({"item_family_id": item.item_family_id, "answer_values": item.answer_values, "hint_text": item.hint_text,
                     "solution_text": item.solution_text, "source": item.source, "status": item.status, "revision": item.revision,
                     "created_by": item.created_by, "reviewed_by": item.reviewed_by, "reviewed_at": iso(item.reviewed_at),
                     "created_at": iso(item.created_at)})
    return body


def _today(at=None):
    return local_day(at or now())


def school_tasks_status(session, student_id, at):
    """System check over today's photographed homework; no homework means unknown."""
    today = _today(at)
    homeworks = [h for h in session.scalars(select(Homework).where(Homework.student_id == student_id,
                                                                   Homework.processing_blocked.is_(False)))
                 if local_day(utc(h.occurred_at)) == today]
    if not homeworks:
        return {"value": None, "source": "system", "detail": "no_homework_today"}
    open_cases, open_help = 0, 0
    for entity in session.scalars(select(Entity).where(Entity.homework_id.in_([h.id for h in homeworks]),
                                                       Entity.kind.in_(("case", "help")))):
        doc = entity.document
        if entity.kind == "help":
            open_help += doc["status"] in ("waiting", "claimed")
            continue
        if doc.get("lifecycle", "active") != "active":
            continue
        decision = doc.get("current_decision") or {}
        done = doc["progress"] == "verified_resolved" or (decision.get("verdict") == "correct" and doc["progress"] != "paused")
        open_cases += not done
    value = open_cases == 0 and open_help == 0
    return {"value": value, "source": "system",
            "detail": "photographed_homework_resolved" if value else f"open_cases:{open_cases},open_help:{open_help}"}


def gate_status(session, student_id, at=None):
    at = at or now()
    window = session.scalar(select(OptionalWindow).where(OptionalWindow.student_id == student_id,
                                                         OptionalWindow.local_date == _today(at).isoformat()))
    tasks = school_tasks_status(session, student_id, at)
    if window is not None and window.school_tasks_confirmed is not None:
        tasks = {"value": window.school_tasks_confirmed, "source": "staff", "actor": window.updated_by,
                 "at": iso(window.updated_at), "detail": "staff_confirmed"}
    capacity = {"value": window.capacity_observed if window else None, "source": "staff",
                "actor": window.updated_by if window else None, "at": iso(window.updated_at) if window else None}
    budget = window.extra_minutes if window else 0
    start_of_day = datetime.combine(_today(at), datetime.min.time(), SHANGHAI).astimezone(timezone.utc)
    used = 0.0
    for row in session.scalars(select(PracticeSession).where(PracticeSession.student_id == student_id,
                                                             PracticeSession.started_at >= start_of_day)):
        end = min(utc(row.ended_at) if row.ended_at else at, utc(row.budget_ends_at))
        used += max(0.0, (end - utc(row.started_at)).total_seconds() / 60)
    remaining = max(0, int(budget - used))
    reasons = [code for code, ok in (("school_tasks_not_confirmed", tasks["value"] is True),
                                     ("capacity_not_observed", capacity["value"] is True),
                                     ("no_extra_budget", remaining > 0)) if not ok]
    return {"open": not reasons, "reasons": reasons, "school_tasks_complete": tasks, "has_capacity": capacity,
            "extra_minutes": budget, "remaining_minutes": remaining, "window_revision": window.revision if window else 0,
            "checked_at": at.isoformat()}


def _names(session):
    graph = require_published_graph(session)
    return {n.id: n for n in session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version))}


def _states(session, student_id):
    return {r.knowledge_id: r.document for r in session.scalars(select(StudentKnowledgeState)
                                                                .where(StudentKnowledgeState.student_id == student_id))}


def pick_items(session, tenant_id, student_id, knowledge_ids, count):
    """Unseen reviewed items first, one per family, easier first; never repeat within a session."""
    seen = set(session.scalars(select(PracticeResponse.item_id).where(PracticeResponse.student_id == student_id)))
    pool = list(session.scalars(select(PracticeItem).where(PracticeItem.tenant_id == tenant_id, PracticeItem.status == "approved",
                                                           PracticeItem.knowledge_id.in_(knowledge_ids))
                                .order_by(PracticeItem.difficulty, PracticeItem.created_at, PracticeItem.id)))
    chosen, families = [], set()
    for item in sorted(pool, key=lambda i: (i.id in seen, knowledge_ids.index(i.knowledge_id), i.difficulty)):
        if item.item_family_id in families:
            continue
        chosen.append(item)
        families.add(item.item_family_id)
        if len(chosen) == count:
            break
    return chosen


def preview_targets(session, student_id, knowledge_ids):
    names, states = _names(session), _states(session, student_id)
    graph = require_published_graph(session)
    edges = list(session.scalars(select(KnowledgeEdge).where(KnowledgeEdge.graph_version == graph.version,
                                                             KnowledgeEdge.relation_type == "prerequisite_of",
                                                             KnowledgeEdge.to_id.in_(knowledge_ids))))
    output = []
    for knowledge_id in knowledge_ids:
        node = names.get(knowledge_id)
        if node is None:
            continue
        output.append({"id": node.id, "name": node.name, "objective": node.observable_objective,
                       "prerequisites": [{"id": e.from_id, "name": names[e.from_id].name,
                                          "learning_status": states.get(e.from_id, {}).get("learning_status", "no_judgment")}
                                         for e in edges if e.to_id == knowledge_id and e.from_id in names]})
    return output


def session_dict(session, row: PracticeSession, *, staff=False):
    items = {i.id: i for i in session.scalars(select(PracticeItem).where(PracticeItem.id.in_(row.item_ids)))}
    responses = {r.item_id: r for r in session.scalars(select(PracticeResponse).where(PracticeResponse.session_id == row.id))}
    entries = []
    for item_id in row.item_ids:
        item, response = items[item_id], responses.get(item_id)
        entry = {**item_dict(item, staff=staff), "has_hint": bool(item.hint_text) and row.purpose == "variant"}
        if response:
            entry["response"] = {"answer_text": response.answer_text, "result": response.result, "hint_used": response.hint_used}
            entry["solution_text"] = item.solution_text
        entries.append(entry)
    body = {"id": row.id, "student_id": row.student_id, "purpose": row.purpose, "knowledge_ids": row.knowledge_ids,
            "status": row.status, "exit_reason": row.exit_reason, "items": entries, "started_at": iso(row.started_at),
            "budget_ends_at": iso(row.budget_ends_at), "ended_at": iso(row.ended_at),
            "hints_allowed": row.purpose == "variant",
            "notice": "这是自愿活动，可以随时结束；不做或中途结束都不会记欠账，也不影响知识地图。"}
    if row.purpose == "preview":
        body["targets"] = preview_targets(session, row.student_id, row.knowledge_ids)
    if staff:
        body["gate"] = row.gate
    return body


def _expire(row: PracticeSession, at):
    if row.status == "active" and utc(row.budget_ends_at) <= at:
        row.status, row.ended_at = "time_up", row.budget_ends_at


@register_report_section
def optional_section(session, student, window_start, window_end):
    rows = [r for r in session.scalars(select(PracticeSession).where(PracticeSession.student_id == student.id))
            if window_start <= utc(r.started_at) < window_end]
    responses = [r for r in session.scalars(select(PracticeResponse).where(PracticeResponse.student_id == student.id))
                 if window_start <= utc(r.occurred_at) < window_end]
    minutes = sum(max(0, ((utc(r.ended_at) if r.ended_at else utc(r.budget_ends_at)) - utc(r.started_at)).total_seconds()) for r in rows) / 60
    if not rows:
        summary = "本期没有安排额外活动。学校任务之外默认不加题，这不影响任何判断。"
    else:
        summary = (f"孩子自愿参加 {len(rows)} 次额外活动，共约 {round(minutes)} 分钟，完成 {len(responses)} 道题，"
                   f"其中中途选择结束 {sum(1 for r in rows if r.status == 'exited')} 次。额外活动结果不计入知识点判断。")
    return "optional_activity", {"sessions": len(rows), "minutes": round(minutes), "answered": len(responses),
                                 "correct": sum(1 for r in responses if r.result == "correct"),
                                 "exited": sum(1 for r in rows if r.status == "exited"), "summary": summary}


@register_downstream_purger
def purge_practice(session, tenant_id, student_id):
    for model in (PracticeResponse, PracticeSession, OptionalWindow):
        session.execute(delete(model).where(model.student_id == student_id, model.tenant_id == tenant_id))


def generation_schema():
    text = lambda n: {"type": "string", "minLength": 1, "maxLength": n}  # noqa: E731
    return {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
        "type": "array", "maxItems": 5, "items": {"type": "object", "additionalProperties": False,
            "required": ["stem_text", "answer_kind", "answer_values", "hint_text", "solution_text", "difficulty"], "properties": {
                "stem_text": text(600), "answer_kind": {"enum": ["number", "choice", "text"]},
                "answer_values": {"type": "array", "minItems": 1, "maxItems": 4, "items": text(60)},
                "hint_text": text(200), "solution_text": text(800), "difficulty": {"enum": [1, 2, 3]}}}}}}


@register_job_handler("generate_practice")
def generate_practice(session, job, worker):
    client = worker.client
    if client is None or not client.settings.enabled:
        return {"status": "provider_disabled"}
    node = _names(session).get(job.payload["knowledge_id"])
    if node is None:
        return {"status": "unknown_knowledge"}
    text = json.dumps({"knowledge": {"id": node.id, "name": node.name, "objective": node.observable_objective},
                       "count": job.payload["count"], "source_question": job.payload.get("source_question")}, ensure_ascii=False)
    result = client.generate(role=Role.KNOWLEDGE_EXTRACT, text=text, output_schema=generation_schema(), thinking=True,
                             max_tokens=3072, instructions=GENERATE_INSTRUCTIONS)
    stamp = now()
    family = f"family_{uuid4().hex[:16]}"
    for index, item in enumerate(result.data["items"][:job.payload["count"]]):
        session.add(PracticeItem(id=new_id("item"), tenant_id=job.tenant_id, knowledge_id=node.id,
                                 item_family_id=f"{family}_{index}", stem_text=item["stem_text"], answer_kind=item["answer_kind"],
                                 answer_values=item["answer_values"], hint_text=item["hint_text"],
                                 solution_text=item["solution_text"], difficulty=item["difficulty"], source="model",
                                 status="candidate", generation={"model": result.metadata.model, "job_id": job.id},
                                 created_by=job.payload.get("requested_by"), revision=1, created_at=stamp, updated_at=stamp))
    return {"status": "proposed"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemInput(Strict):
    knowledge_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    item_family_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    stem_text: str = Field(min_length=2, max_length=600)
    answer_kind: Literal["number", "choice", "text"]
    answer_values: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(min_length=1, max_length=4)
    hint_text: str | None = Field(default=None, max_length=200)
    solution_text: str = Field(min_length=1, max_length=800)
    difficulty: int = Field(default=1, ge=1, le=3)


class ItemEdits(Strict):
    stem_text: str | None = Field(default=None, min_length=2, max_length=600)
    answer_values: list[Annotated[str, Field(min_length=1, max_length=60)]] | None = Field(default=None, min_length=1, max_length=4)
    hint_text: str | None = Field(default=None, max_length=200)
    solution_text: str | None = Field(default=None, min_length=1, max_length=800)
    difficulty: int | None = Field(default=None, ge=1, le=3)


class ReviewInput(Strict):
    action: Literal["approve", "reject", "retire"]
    expected_revision: int = Field(ge=1)
    edits: ItemEdits | None = None


class GenerateInput(Strict):
    knowledge_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    count: int = Field(default=3, ge=1, le=5)
    source_case_id: str | None = Field(default=None, max_length=64)


class WindowInput(Strict):
    expected_revision: int = Field(ge=0)
    capacity_observed: bool
    extra_minutes: int = Field(ge=0, le=60)
    school_tasks_confirmed: bool | None = None


class StartInput(Strict):
    purpose: Literal["variant", "preview"]
    willing: Literal[True]
    knowledge_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    source_case_id: str | None = Field(default=None, max_length=64)
    count: int = Field(default=2, ge=1, le=MAX_ITEMS)
    with_quiz: bool = False

    @model_validator(mode="after")
    def check(self):
        if self.purpose == "variant" and not self.knowledge_id:
            raise ValueError("variant practice needs knowledge_id")
        return self


class AnswerInput(Strict):
    item_id: str = Field(max_length=64)
    answer_text: str = Field(min_length=1, max_length=200)


class ExitInput(Strict):
    reason: Literal["tired", "not_now", "done", "other"] = "not_now"


def install_practice(app, engine, cfg, database_session, current_actor):
    from .app import require_student
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]
    is_staff = lambda actor: bool(set(actor.roles) & {"sitting_teacher", "campus_admin"})  # noqa: E731

    def require_staff(actor):
        if not is_staff(actor):
            raise Problem(403, "authorization", "forbidden", "需要坐班老师。")

    def load_item(session, actor, item_id, lock=False):
        query = select(PracticeItem).where(PracticeItem.id == item_id, PracticeItem.tenant_id == actor.tenant_id)
        item = session.scalar(query.with_for_update() if lock else query)
        if item is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        return item

    def load_session(session, actor, session_id, lock=False):
        query = select(PracticeSession).where(PracticeSession.id == session_id)
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, row.student_id)
        return row

    @app.get("/v1/practice-items", operation_id="listPracticeItems")
    def list_items(actor: ActorDep, session: SessionDep, knowledge_id: str | None = None, status: str = "candidate"):
        require_staff(actor)
        query = select(PracticeItem).where(PracticeItem.tenant_id == actor.tenant_id, PracticeItem.status == status)
        if knowledge_id:
            query = query.where(PracticeItem.knowledge_id == knowledge_id)
        rows = session.scalars(query.order_by(PracticeItem.knowledge_id, PracticeItem.created_at).limit(200))
        return {"items": [item_dict(r, staff=True) for r in rows]}

    @app.post("/v1/practice-items", status_code=201, operation_id="createPracticeItem")
    def create_item(payload: ItemInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        node = _names(session).get(payload.knowledge_id)
        if node is None or node.node_type not in ("concept", "skill"):
            raise Problem(422, "validation", "invalid_request", "题目只能关联已发布的具体知识点。")
        stamp = now()
        item = PracticeItem(id=new_id("item"), tenant_id=actor.tenant_id, item_family_id=payload.item_family_id or f"family_{uuid4().hex[:16]}",
                            **payload.model_dump(exclude={"item_family_id"}), source="staff", status="approved", created_by=actor.id,
                            reviewed_by=actor.id, reviewed_at=stamp, revision=1, created_at=stamp, updated_at=stamp)
        session.add(item)
        session.commit()
        return item_dict(item, staff=True)

    @app.post("/v1/practice-items/{item_id}/review", operation_id="reviewPracticeItem")
    def review_item(item_id: str, payload: ReviewInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        item = load_item(session, actor, item_id, lock=True)
        if item.revision != payload.expected_revision:
            raise Problem(409, "conflict", "revision_conflict", "题目已被修改，请刷新后再审核。")
        allowed = {"approve": ("candidate",), "reject": ("candidate",), "retire": ("approved",)}[payload.action]
        if item.status not in allowed:
            raise Problem(409, "conflict", "revision_conflict", "题目当前状态不能执行该操作。")
        if payload.edits:
            for field, value in payload.edits.model_dump(exclude_unset=True).items():
                setattr(item, field, value)
        stamp = now()
        item.status = {"approve": "approved", "reject": "rejected", "retire": "retired"}[payload.action]
        item.reviewed_by, item.reviewed_at, item.revision, item.updated_at = actor.id, stamp, item.revision + 1, stamp
        session.commit()
        return item_dict(item, staff=True)

    @app.post("/v1/practice-items/generate", status_code=202, operation_id="generatePracticeItems")
    def generate(payload: GenerateInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        node = _names(session).get(payload.knowledge_id)
        if node is None or node.node_type not in ("concept", "skill"):
            raise Problem(422, "validation", "invalid_request", "只能为已发布的具体知识点生成候选。")
        source_question = None
        if payload.source_case_id:
            case = session.get(Entity, payload.source_case_id)
            if case is None or case.kind != "case":
                raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
            require_student(session, actor, session.get(Homework, case.homework_id).student_id)
            question = session.scalar(select(Entity).where(Entity.kind == "question", Entity.homework_id == case.homework_id,
                                                           Entity.id == case.document["question_id"]))
            source_question = (question.document.get("stem_text") or "")[:400] if question else None
        job_id = enqueue(session, actor.tenant_id, BANK_JOB_STUDENT, "generate_practice",
                         {"knowledge_id": node.id, "count": payload.count, "source_question": source_question, "requested_by": actor.id},
                         f"practice:{actor.tenant_id}:{node.id}")
        session.commit()
        return {"job_id": job_id, "status": "queued", "notice": "模型只生成候选题，老师审核通过后学生才会看到。"}

    @app.put("/v1/students/{student_id}/optional-window", operation_id="setOptionalWindow")
    def set_window(student_id: str, payload: WindowInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        student = require_student(session, actor, student_id)
        today = _today().isoformat()
        window = session.scalar(select(OptionalWindow).where(OptionalWindow.student_id == student_id,
                                                             OptionalWindow.local_date == today).with_for_update())
        if (window.revision if window else 0) != payload.expected_revision:
            raise Problem(409, "conflict", "revision_conflict", "今天的设置已被修改，请刷新。")
        if window is None:
            window = OptionalWindow(id=new_id("window"), tenant_id=student.tenant_id, student_id=student_id, local_date=today, revision=0)
            session.add(window)
        window.capacity_observed, window.extra_minutes = payload.capacity_observed, payload.extra_minutes
        window.school_tasks_confirmed, window.updated_by = payload.school_tasks_confirmed, actor.id
        window.revision, window.updated_at = window.revision + 1, now()
        session.commit()
        return gate_status(session, student_id)

    @app.get("/v1/students/{student_id}/optional-activities", operation_id="getOptionalActivities")
    def activities(student_id: str, actor: ActorDep, session: SessionDep):
        """What a child may choose now; nothing is suggested unless the gate is open."""
        student = require_student(session, actor, student_id)
        gate = gate_status(session, student_id)
        body = {"gate": gate if is_staff(actor) else {"open": gate["open"], "reasons": gate["reasons"],
                                                     "remaining_minutes": gate["remaining_minutes"]},
                "variants": [], "preview": None,
                "notice": "完成学校任务后可以休息或结束。额外活动完全自愿，不做也不会记欠账。"}
        if not gate["open"] and not is_staff(actor):
            return body
        names, states = _names(session), _states(session, student_id)
        counts = dict(session.execute(select(PracticeItem.knowledge_id, func.count(PracticeItem.id))
                                      .where(PracticeItem.tenant_id == student.tenant_id, PracticeItem.status == "approved")
                                      .group_by(PracticeItem.knowledge_id)).all())
        body["variants"] = [{"knowledge_id": k, "name": names[k].name, "learning_status": s["learning_status"],
                             "available_items": counts.get(k, 0)}
                            for k, s in sorted(states.items()) if s["learning_status"] in NEEDS_PRACTICE and k in names and counts.get(k)]
        profile = session.get(StudentLearningProfile, student_id)
        current = [k for k in ((profile.document.get("current_knowledge_ids") or []) if profile else []) if k in names]
        if current:
            body["preview"] = {"knowledge_ids": current, "names": [names[k].name for k in current],
                               "quiz_items_available": len(pick_items(session, student.tenant_id, student_id, current, MAX_ITEMS))}
        return body

    @app.post("/v1/students/{student_id}/practice-sessions", status_code=201, operation_id="startPracticeSession")
    def start(student_id: str, payload: StartInput, actor: ActorDep, session: SessionDep, idempotency_key: OptionalKey = None):
        operation = f"POST /v1/students/{student_id}/practice-sessions"
        student = require_student(session, actor, student_id)
        if "student" not in actor.roles:
            raise Problem(403, "authorization", "forbidden", "额外活动只能由孩子本人选择开始。")
        session.scalar(select(Student).where(Student.id == student_id).with_for_update())
        if (replay := replay_idempotent(session, actor, student_id, operation, idempotency_key, payload.model_dump(mode="json"))) is not None:
            return replay
        if session.scalar(select(PracticeSession.id).where(PracticeSession.student_id == student_id, PracticeSession.status == "active")):
            active = session.scalar(select(PracticeSession).where(PracticeSession.student_id == student_id, PracticeSession.status == "active"))
            _expire(active, now())
            if active.status == "active":
                raise Problem(409, "conflict", "revision_conflict", "已有进行中的活动，请先完成或结束。")
        at = now()
        gate = gate_status(session, student_id, at)
        if not gate["open"]:
            session.commit()
            raise Problem(409, "conflict", "optional_gate_closed", "现在还不能开始额外活动：需要学校任务已完成、老师确认有余力并开放时间。")
        names = _names(session)
        if payload.purpose == "variant":
            state = _states(session, student_id).get(payload.knowledge_id)
            if state is None or state["learning_status"] not in NEEDS_PRACTICE:
                raise Problem(409, "conflict", "practice_not_needed", "这个知识点目前没有需要额外练习的依据。")
            knowledge_ids, count = [payload.knowledge_id], payload.count
        else:
            profile = session.get(StudentLearningProfile, student_id)
            knowledge_ids = [k for k in ((profile.document.get("current_knowledge_ids") or []) if profile else []) if k in names]
            if not knowledge_ids:
                raise Problem(409, "conflict", "course_unknown", "还不知道下一课的内容，暂不提供预习。")
            count = MAX_ITEMS if payload.with_quiz else 0
        items = pick_items(session, student.tenant_id, student_id, knowledge_ids, count) if count else []
        if payload.purpose == "variant" and not items:
            raise Problem(409, "conflict", "no_reviewed_items", "这个知识点还没有老师审核过的练习题。")
        row = PracticeSession(id=new_id("practice"), tenant_id=student.tenant_id, student_id=student_id, purpose=payload.purpose,
                              knowledge_ids=knowledge_ids, source_case_id=payload.source_case_id, item_ids=[i.id for i in items],
                              hints_used=[], gate={**gate, "child_willing": {"value": True, "source": "student", "actor": actor.id,
                                                                            "at": at.isoformat()}},
                              status="active", started_at=at, budget_ends_at=at + timedelta(minutes=gate["remaining_minutes"]))
        session.add(row)
        session.flush()
        result = remember_idempotent(session, actor, student_id, operation, idempotency_key, payload.model_dump(mode="json"),
                                     session_dict(session, row))
        session.commit()
        return result

    @app.get("/v1/students/{student_id}/practice-sessions", operation_id="listPracticeSessions")
    def list_sessions(student_id: str, actor: ActorDep, session: SessionDep):
        require_student(session, actor, student_id)
        rows = list(session.scalars(select(PracticeSession).where(PracticeSession.student_id == student_id)
                                    .order_by(PracticeSession.started_at.desc()).limit(50)))
        at = now()
        if any(r.status == "active" and utc(r.budget_ends_at) <= at for r in rows):
            for row in session.scalars(select(PracticeSession).where(PracticeSession.student_id == student_id,
                                                                    PracticeSession.status == "active").with_for_update()):
                _expire(row, at)
            session.commit()
        return {"items": [session_dict(session, r, staff=is_staff(actor)) for r in rows]}

    @app.get("/v1/practice-sessions/{session_id}", operation_id="getPracticeSession")
    def get_session(session_id: str, actor: ActorDep, session: SessionDep):
        row = load_session(session, actor, session_id)
        if row.status == "active" and utc(row.budget_ends_at) <= now():
            row = load_session(session, actor, session_id, lock=True)
            _expire(row, now())
            session.commit()
        return session_dict(session, row, staff=is_staff(actor))

    @app.post("/v1/practice-sessions/{session_id}/items/{item_id}/hint", operation_id="revealPracticeHint")
    def hint(session_id: str, item_id: str, actor: ActorDep, session: SessionDep):
        row = load_session(session, actor, session_id, lock=True)
        _expire(row, now())
        if row.status != "active" or item_id not in row.item_ids:
            session.commit()
            raise Problem(409, "conflict", "revision_conflict", "活动已结束或题目不在本次活动中。")
        if row.purpose != "variant":
            raise Problem(409, "conflict", "hints_disabled", "小测进行中不提供提示；可以随时结束。")
        item = session.get(PracticeItem, item_id)
        if not item.hint_text:
            raise Problem(404, "not_found", "not_found", "这道题没有提示。")
        if item_id not in row.hints_used:
            row.hints_used = [*row.hints_used, item_id]
        session.commit()
        return {"item_id": item_id, "hint_text": item.hint_text}

    @app.post("/v1/practice-sessions/{session_id}/responses", status_code=201, operation_id="answerPracticeItem")
    def answer(session_id: str, payload: AnswerInput, actor: ActorDep, session: SessionDep, idempotency_key: OptionalKey = None):
        row = load_session(session, actor, session_id, lock=True)
        operation = f"POST /v1/practice-sessions/{session_id}/responses"
        if (replay := replay_idempotent(session, actor, row.student_id, operation, idempotency_key, payload.model_dump(mode="json"))) is not None:
            return replay
        at = now()
        _expire(row, at)
        if row.status != "active" or payload.item_id not in row.item_ids:
            session.commit()
            raise Problem(409, "conflict", "revision_conflict", "活动已结束或题目不在本次活动中。")
        if session.scalar(select(PracticeResponse.id).where(PracticeResponse.session_id == row.id, PracticeResponse.item_id == payload.item_id)):
            raise Problem(409, "conflict", "revision_conflict", "这道题已经作答。")
        item = session.get(PracticeItem, payload.item_id)
        result = grade(item, payload.answer_text)
        session.add(PracticeResponse(id=new_id("presp"), tenant_id=row.tenant_id, student_id=row.student_id, session_id=row.id,
                                     item_id=item.id, knowledge_id=item.knowledge_id,
                                     event_type="optional_variant" if row.purpose == "variant" else "optional_quiz",
                                     answer_text=payload.answer_text.strip(), result=result, hint_used=item.id in row.hints_used,
                                     occurred_at=at))
        session.flush()
        answered = session.scalar(select(func.count()).select_from(PracticeResponse).where(PracticeResponse.session_id == row.id))
        if answered == len(row.item_ids):
            row.status, row.ended_at = "completed", at
        body = remember_idempotent(session, actor, row.student_id, operation, idempotency_key, payload.model_dump(mode="json"),
                                   {"item_id": item.id, "result": result, "solution_text": item.solution_text, "session_status": row.status})
        session.commit()
        return body

    @app.post("/v1/practice-sessions/{session_id}/exit", operation_id="exitPracticeSession")
    def exit_session(session_id: str, payload: ExitInput, actor: ActorDep, session: SessionDep):
        row = load_session(session, actor, session_id, lock=True)
        _expire(row, now())
        if row.status == "active":
            row.status, row.exit_reason, row.ended_at = "exited", payload.reason, now()
        session.commit()
        return session_dict(session, row, staff=is_staff(actor))
