"""Student practice: one-step variants and next-lesson preview with an optional mini quiz.

Extra work defaults to zero. The server starts an activity only when school
tasks are complete (photographed homework all resolved, or a staff
confirmation), staff observed capacity and opened an extra-minute budget for
today, and the child says yes for this activity. A student launched directly
from an AI-identified mistake can start its variant immediately. Unknown counts
as not met and everything is re-checked at start. Leaving early or never
starting creates no debt. Items come from a reviewed bank tagged by knowledge
point; answers are rule-graded and kept apart from knowledge state, so taking
part can never make a child look weaker.
"""
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import json
import re
from typing import Annotated, Literal
import unicodedata
from uuid import uuid4

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, object_session

from .auth import Actor
from .correction_models import Entity
from .curriculum_catalog import canonical_subject, curriculum_matches_scope, graph_is_usable, scope_for_profile
from .device_auth import utc
from .errors import Problem
from .governance import register_downstream_purger
from .learning_models import (CurriculumGraph, KnowledgeEdge, KnowledgeNode, LearningOutbox, LearningProjection, OptionalWindow,
                              PracticeItem, PracticeResponse, PracticeSession, QuestionKnowledgeLink, StudentKnowledgeState,
                              StudentLearningProfile, VariantGeneration, PracticeAssessment)
from .correction_core import remember_idempotent, replay_idempotent
from .learning_mapping import _current_links, _match_node, auto_confirm_model_links
from .learning_rules import RULE_VERSION, model_decision_usable
from .learning_projection import (OptionalKey, enqueue, fingerprints, new_id, project_student, register_job_handler,
                                   published_graph, require_published_graph)
from .learning_reports import SHANGHAI, local_day, register_report_section
from .models import Homework, Student
from .providers.prompts import Role
from .providers.variant_arithmetic import item_arithmetic_issues

MAX_ITEMS = 3
NEEDS_PRACTICE = ("needs_help_now", "consolidating")
BANK_JOB_STUDENT = "tenant-bank"
GENERATE_INSTRUCTIONS = (
    "为托管班出少量同知识点练习题候选，供老师审核后使用。题目必须只考查 knowledge 中给出的知识点，符合 curriculum 的教材与年级范围，"
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


def _solution_answer_text(item: PracticeItem) -> str | None:
    """Format the saved reference without guessing missing answers or steps."""
    def first_value(values):
        if isinstance(values, list) and values and isinstance(values[0], str):
            return values[0].strip() or None
        return None

    if item.question_kind == "fill_blank":
        fields, answers = item.answer_fields, item.field_answers
        if not isinstance(fields, list) or not fields or not isinstance(answers, list):
            return None
        by_id = {}
        for answer in answers:
            if not isinstance(answer, dict) or not isinstance(answer.get("field_id"), str):
                return None
            field_id = answer["field_id"]
            value = first_value(answer.get("answer_values"))
            if not field_id or field_id in by_id or value is None:
                return None
            by_id[field_id] = value
        lines, seen = [], set()
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("id"), str):
                return None
            field_id, label = field["id"], field.get("label")
            if field_id in seen or field_id not in by_id or not isinstance(label, str) or not label.strip():
                return None
            seen.add(field_id)
            lines.append(f"{label.strip()}：{by_id[field_id]}")
        return "\n".join(lines) if seen == set(by_id) else None
    if item.answer_kind == "choice":
        values = item.answer_values
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
            return None
        if item.question_kind == "choice":
            options = item.options
            if not isinstance(options, list) or any(not isinstance(o, dict) or not isinstance(o.get("label"), str) for o in options):
                return None
            labels = [o["label"] for o in options]
            if len(labels) != len(set(labels)) or not set(values) <= set(labels):
                return None
            return "、".join(label for label in labels if label in values)
        # The original bank stores A-D labels, including compact values like AC.
        labels = unicodedata.normalize("NFKC", "".join(values)).upper()
        return "、".join(sorted(set(labels))) if re.fullmatch(r"[A-D]+", labels) else None
    if item.answer_kind in ("number", "text"):
        return first_value(item.answer_values)
    return None


def item_dict(item: PracticeItem, *, staff=False):
    body = {"id": item.id, "knowledge_id": item.knowledge_id, "graph_version": item.graph_version,
            "stem_text": item.stem_text, "answer_kind": item.answer_kind,
            "difficulty": item.difficulty}
    if item.question_kind:
        body.update({"question_type": item.question_type, "question_kind": item.question_kind,
                     "options": item.options or [], "answer_fields": item.answer_fields or [],
                     "verification": item.verification})
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
        if _generated_variant(row):
            continue
        end = min(utc(row.ended_at) if row.ended_at else at, utc(row.budget_ends_at))
        used += max(0.0, (end - utc(row.started_at)).total_seconds() / 60)
    remaining = max(0, int(budget - used))
    reasons = [code for code, ok in (("school_tasks_not_confirmed", tasks["value"] is True),
                                     ("capacity_not_observed", capacity["value"] is True),
                                     ("no_extra_budget", remaining > 0)) if not ok]
    return {"open": not reasons, "reasons": reasons, "school_tasks_complete": tasks, "has_capacity": capacity,
            "extra_minutes": budget, "remaining_minutes": remaining, "window_revision": window.revision if window else 0,
            "checked_at": at.isoformat()}


def _graph_by_version(session, graph_version):
    graph = session.get(CurriculumGraph, graph_version) if graph_version else None
    if graph is None or graph.status != "published" or (graph.book_identity and not graph_is_usable(graph)):
        raise Problem(422, "validation", "invalid_request", "请选择已发布且可用的教材图谱版本。")
    return graph


def _names(session, student_id=None, *, graph_version=None):
    graph = _graph_by_version(session, graph_version) if graph_version else require_published_graph(session, student_id)
    return {n.id: n for n in session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version))}


def _states(session, student_id):
    graph = require_published_graph(session, student_id)
    projection = session.get(LearningProjection, student_id)
    expected = fingerprints(session, [student_id]).get(student_id)
    if expected and (projection is None or projection.source_fingerprint != expected):
        return {}
    return {r.knowledge_id: r.document for r in session.scalars(select(StudentKnowledgeState)
                  .where(StudentKnowledgeState.student_id == student_id, StudentKnowledgeState.graph_version == graph.version,
                         StudentKnowledgeState.rule_version == RULE_VERSION))}


def _source_matches_graph(session, student_id, homework, question, graph):
    profile = session.get(StudentLearningProfile, student_id)
    curriculum = homework.curriculum if homework else {}
    subject = canonical_subject((question.document.get("subject") if question else None)
                                or curriculum.get("subject") or graph.subject)
    scope = scope_for_profile(profile.document if profile else {}, subject)
    return bool(scope and curriculum_matches_scope(curriculum, scope)
                and (scope.graph_version is None or scope.graph_version == graph.version))


def pick_items(session, tenant_id, student_id, knowledge_ids, count, source_stem=None):
    """Unseen reviewed items first, one per family, easier first; never repeat within a session."""
    seen = set(session.scalars(select(PracticeResponse.item_id).where(PracticeResponse.student_id == student_id)))
    graph = require_published_graph(session, student_id)
    source_key = _normalize(source_stem) if source_stem else None
    pool = list(session.scalars(select(PracticeItem).where(PracticeItem.tenant_id == tenant_id, PracticeItem.status == "approved",
                                                           PracticeItem.graph_version == graph.version,
                                                           PracticeItem.knowledge_id.in_(knowledge_ids))
                                .order_by(PracticeItem.difficulty, PracticeItem.created_at, PracticeItem.id)))
    chosen, families = [], set()
    for item in sorted(pool, key=lambda i: (i.id in seen, knowledge_ids.index(i.knowledge_id), i.difficulty)):
        if source_key and _normalize(item.stem_text) == source_key:
            continue
        if item.item_family_id in families:
            continue
        chosen.append(item)
        families.add(item.item_family_id)
        if len(chosen) == count:
            break
    return chosen


def _variant_error_overview(session, student, gate):
    """Build student-facing clusters from the current AI verdicts.

    This view is intentionally independent of the teacher review queue. A
    wrong or partially correct AI result is useful for practice immediately;
    topics that do not match the published graph are kept in a visible
    fallback cluster instead of disappearing from the page.
    """
    graph = require_published_graph(session, student.id)
    nodes = {node.id: node for node in session.scalars(select(KnowledgeNode).where(
        KnowledgeNode.graph_version == graph.version, KnowledgeNode.node_type.in_(('concept', 'skill'))))}
    node_list = list(nodes.values())
    states = _states(session, student.id)
    projection = session.get(LearningProjection, student.id)
    expected = fingerprints(session, [student.id]).get(student.id)
    homework_ids = select(Homework.id).where(Homework.student_id == student.id, Homework.processing_blocked.is_(False))
    rows = []
    uncertain_count = 0
    for case in session.scalars(select(Entity).where(Entity.homework_id.in_(homework_ids), Entity.kind == "case")):
        decision = case.document.get("current_decision") or {}
        verdict = decision.get("verdict")
        if case.document.get("lifecycle", "active") != "active":
            continue
        if (decision.get("validity") not in {"candidate", "confirmed"}
                or (decision.get("origin") == "model" and not model_decision_usable(decision))):
            uncertain_count += 1
            continue
        if verdict == "unable_to_judge":
            uncertain_count += 1
            continue
        if verdict not in ("incorrect", "partially_correct") or case.document.get("lifecycle", "active") != "active":
            continue
        question = session.get(Entity, case.document.get("question_id"))
        homework = session.get(Homework, case.homework_id)
        attempt = session.get(Entity, case.document.get("current_attempt_id"))
        occurred_at = (attempt.document.get("occurred_at") if attempt else None) or (iso(homework.occurred_at) if homework else None)
        labels = [value.strip() for value in decision.get("knowledge_points", [])
                  if isinstance(value, str) and value.strip()]
        current_links = _current_links(session, case.document.get("question_id"))
        links = sorted((link for link in current_links.values() if link.status == "confirmed"),
                       key=lambda link: (link.created_at, link.id), reverse=True)
        rejected = {link.knowledge_id for link in current_links.values() if link.status == "rejected"}
        scope_matches = _source_matches_graph(session, student.id, homework, question, graph)
        primary_link = next((link for link in links if scope_matches and link.role == "primary" and link.knowledge_id in nodes), None)
        node = nodes.get(primary_link.knowledge_id) if primary_link else next(
            (candidate for label in labels if scope_matches and graph_is_usable(graph) and (candidate := _match_node(label, node_list))
             and candidate.id not in rejected), None)
        topic = labels[0] if labels else ((question.document.get("question_type") or "未标注题型") if question else "未标注题型")
        if node is None:
            key = ("auto-topic-" if scope_matches else "history-topic-") + hashlib.sha256(topic.encode("utf-8")).hexdigest()[:16]
            name = f"{'待匹配题型' if scope_matches else '历史教材题目'} · {topic[:24]}"
        else:
            key, name = node.id, node.name
        rows.append((key, name, node, case, decision, question, homework, occurred_at, scope_matches))

    # One card represents one original wrong question even if a rebuild left
    # more than one historical evidence row for it.
    groups = {}
    for knowledge_id, name, node, case, decision, question, homework, occurred_at, scope_matches in sorted(
            rows, key=lambda item: (item[7] or "", item[3].id), reverse=True):
        group = groups.setdefault(knowledge_id, {"knowledge_id": knowledge_id, "name": name,
                                                   "learning_status": states.get(knowledge_id, {}).get("learning_status", "needs_help_now"),
                                                   "error_count": 0, "source_case_id": case.id, "questions": [],
                                                   "available_items": 0, "graph_version": graph.version if node else None,
                                                   "curriculum_status": "current" if scope_matches else "historical_unmatched"})
        if any(item["case_id"] == case.id for item in group["questions"]):
            continue
        group["error_count"] += 1
        group["questions"].append({
            "case_id": case.id, "question_id": case.document.get("question_id"),
            "question_text": (question.document.get("stem_text") or "")[:240] if question else None,
            "homework_title": homework.title if homework else None,
            "occurred_at": occurred_at,
            "correct_answer": decision.get("correct_answer"),
            "correction_method": decision.get("correction_method"),
            "source_curriculum": homework.curriculum if homework else None,
            "curriculum_status": "current" if scope_matches else "historical_unmatched",
        })

    approved_items = list(session.scalars(select(PracticeItem).where(
        PracticeItem.tenant_id == student.tenant_id, PracticeItem.status == "approved", PracticeItem.graph_version == graph.version)))
    for group in groups.values():
        group["source_case_id"] = group["questions"][0]["case_id"]
        source_key = _normalize(group["questions"][0]["question_text"] or "")
        group["available_items"] = sum(1 for item in approved_items
                                       if item.knowledge_id == group["knowledge_id"] and _normalize(item.stem_text) != source_key)

    return {"student_id": student.id,
            "projection": {"pending": bool(expected) and (projection is None or projection.source_fingerprint != expected),
                           "projected_at": projection.projected_at.isoformat() if projection else None},
            "gate": {"open": gate["open"], "reasons": gate["reasons"], "remaining_minutes": gate["remaining_minutes"]},
            "groups": sorted(groups.values(), key=lambda item: (-item["error_count"], item["name"])),
            "pending_count": uncertain_count,
            "notice": "错题按 AI 本次判定归纳；当前教材范围内的可靠关联用于练习。旧教材题目保留原始来源，不会自动归入新教材。"}


def preview_targets(session, student_id, knowledge_ids, *, graph_version):
    # Historical sessions render their own graph, never today's selected book.
    graph = session.get(CurriculumGraph, graph_version) if graph_version else None
    if graph is None:
        return []
    names = {n.id: n for n in session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version))}
    current_graph = published_graph(session, student_id)
    states = _states(session, student_id) if current_graph and current_graph.version == graph.version else {}
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
    items = {i.id: i for i in session.scalars(select(PracticeItem).where(
        PracticeItem.id.in_(row.item_ids), PracticeItem.tenant_id == row.tenant_id,
        PracticeItem.graph_version == row.graph_version))}
    responses = {r.item_id: r for r in session.scalars(select(PracticeResponse).where(PracticeResponse.session_id == row.id))}
    assessments = {}
    for assessment in session.scalars(select(PracticeAssessment).where(PracticeAssessment.session_id == row.id)
                                     .order_by(PracticeAssessment.created_at, PracticeAssessment.id)):
        assessments[assessment.item_id] = assessment
    entries = []
    for item_id in row.item_ids:
        if item_id not in items:
            raise Problem(409, "conflict", "revision_conflict", "历史练习的题目与教材版本不一致，请核对原始记录。")
        item, response = items[item_id], responses.get(item_id)
        invalid_reference = bool(item_arithmetic_issues(item))
        entry = {**item_dict(item, staff=staff), "has_hint": bool(item.hint_text) and row.purpose == "variant"}
        if item_id in assessments:
            from .practice_generation import assessment_dict
            entry["assessment"] = assessment_dict(session, assessments[item_id])
        if response:
            entry["response"] = {"answer_text": response.answer_text, "result": response.result, "hint_used": response.hint_used,
                                 **({k: v for k, v in response.details.items() if k in ("selected_options", "answers", "feedback")}
                                    if response.details else {})}
            if invalid_reference:
                # Preserve the historical response in storage, but do not keep
                # presenting a demonstrably wrong reference as a student error.
                entry["response"].update(result="unscorable", feedback="参考答案未通过计算核验，本次不计对错，请重新生成。")
            else:
                entry["solution_text"] = item.solution_text
        entries.append(entry)
    body = {"id": row.id, "student_id": row.student_id, "purpose": row.purpose, "knowledge_ids": row.knowledge_ids,
            "graph_version": row.graph_version,
            "status": row.status, "exit_reason": row.exit_reason, "items": entries, "started_at": iso(row.started_at),
            "budget_ends_at": iso(row.budget_ends_at), "ended_at": iso(row.ended_at),
            "no_expiry": bool(_generated_variant(row) and row.gate.get("no_expiry") is True),
            "hints_allowed": row.purpose == "variant",
            "notice": "这是自愿活动，可以随时结束；不做或中途结束都不会记欠账，也不影响知识地图。"}
    if row.purpose == "preview":
        body["targets"] = preview_targets(session, row.student_id, row.knowledge_ids, graph_version=row.graph_version)
    if staff:
        body["gate"] = row.gate
    return body


def _generated_variant(row):
    return row.purpose == "variant" and bool(row.gate.get("generation_job_id"))


def require_valid_reference(item):
    if item is not None and item_arithmetic_issues(item):
        raise Problem(409, "conflict", "verification_failed", "这道练习的参考答案未通过计算核验，请重新生成；本次不计对错。")


def require_owned_generated_reference(session, tenant_id, student_id, generation_id, *, item_ids=None):
    """Validate owned live records before returning an older idempotent body."""
    generation = session.scalar(select(VariantGeneration).where(
        VariantGeneration.id == generation_id, VariantGeneration.tenant_id == tenant_id,
        VariantGeneration.student_id == student_id))
    if generation is None:
        raise Problem(404, "not_found", "not_found", "任务不存在或不可访问。")
    item = session.scalar(select(PracticeItem).where(
        PracticeItem.id == generation.item_id, PracticeItem.tenant_id == tenant_id,
        PracticeItem.owner_student_id == student_id))
    if item is None or (item_ids is not None and item_ids != [item.id]):
        raise Problem(409, "conflict", "stale_source", "本次练习记录与来源不一致。")
    require_valid_reference(item)


def _expire(row: PracticeSession, at):
    before = (row.status, row.ended_at, row.gate)
    if _generated_variant(row):
        if session := object_session(row):
            from .practice_generation import upgrade_generated_session
            upgrade_generated_session(session, row)
        if row.gate.get("no_expiry") is True:
            return before != (row.status, row.ended_at, row.gate)
    if row.status == "active" and utc(row.budget_ends_at) <= at:
        row.status, row.ended_at = "time_up", row.budget_ends_at
    return before != (row.status, row.ended_at, row.gate)


@register_report_section
def optional_section(session, student, window_start, window_end):
    rows = [r for r in session.scalars(select(PracticeSession).where(
            PracticeSession.student_id == student.id, PracticeSession.tenant_id == student.tenant_id))
            if window_start <= utc(r.started_at) < window_end]
    responses = [r for r in session.scalars(select(PracticeResponse).where(
                 PracticeResponse.student_id == student.id, PracticeResponse.tenant_id == student.tenant_id))
                 if window_start <= utc(r.occurred_at) < window_end]
    valid_items = {item.id for item in session.scalars(select(PracticeItem).where(
        PracticeItem.id.in_({response.item_id for response in responses}),
        PracticeItem.tenant_id == student.tenant_id,
        PracticeItem.owner_student_id.is_(None) | (PracticeItem.owner_student_id == student.id)))
        if not item_arithmetic_issues(item)}
    # Keep historical responses intact; an invalid reference supplies neither
    # a completed-answer count nor a correct-answer count in today's report.
    responses = [response for response in responses if response.item_id in valid_items]
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
    for model in (PracticeAssessment, PracticeResponse, PracticeSession, VariantGeneration, OptionalWindow):
        session.execute(delete(model).where(model.student_id == student_id, model.tenant_id == tenant_id))
    session.execute(delete(PracticeItem).where(PracticeItem.owner_student_id == student_id, PracticeItem.tenant_id == tenant_id))


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
    graph_version = job.payload.get("graph_version")
    if not graph_version:
        return {"status": "graph_version_missing"}
    try:
        graph = _graph_by_version(session, graph_version)
    except Problem:
        return {"status": "graph_unavailable"}
    client = worker.client
    if client is None or not client.settings.enabled:
        return {"status": "provider_disabled"}
    node = _names(session, graph_version=graph.version).get(job.payload["knowledge_id"])
    if node is None:
        return {"status": "unknown_knowledge"}
    text = json.dumps({"curriculum": {"graph_version": graph.version, "title": graph.title, "book_identity": graph.book_identity},
                       "knowledge": {"id": node.id, "name": node.name, "objective": node.observable_objective},
                       "count": job.payload["count"], "source_question": job.payload.get("source_question")}, ensure_ascii=False)
    result = client.generate(role=Role.KNOWLEDGE_EXTRACT, text=text, output_schema=generation_schema(), thinking=True,
                             max_tokens=3072, instructions=GENERATE_INSTRUCTIONS)
    stamp = now()
    family = f"family_{uuid4().hex[:16]}"
    for index, item in enumerate(result.data["items"][:job.payload["count"]]):
        session.add(PracticeItem(id=new_id("item"), tenant_id=job.tenant_id, knowledge_id=node.id, graph_version=graph.version,
                                 item_family_id=f"{family}_{index}", stem_text=item["stem_text"], answer_kind=item["answer_kind"],
                                 answer_values=item["answer_values"], hint_text=item["hint_text"],
                                 solution_text=item["solution_text"], difficulty=item["difficulty"], source="model",
                                 status="candidate", generation={"model": result.metadata.model, "job_id": job.id,
                                                                 "graph_version": graph.version},
                                 created_by=job.payload.get("requested_by"), revision=1, created_at=stamp, updated_at=stamp))
    return {"status": "proposed"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemInput(Strict):
    knowledge_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    graph_version: str | None = Field(default=None, min_length=1, max_length=64)
    student_id: str | None = Field(default=None, min_length=1, max_length=64)
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
    graph_version: str | None = Field(default=None, min_length=1, max_length=64)
    student_id: str | None = Field(default=None, min_length=1, max_length=64)
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
    generation_job_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def check(self):
        if self.generation_job_id and (self.purpose != "variant" or self.with_quiz):
            raise ValueError("generated items are one voluntary variant")
        if self.purpose == "variant" and not (self.knowledge_id or self.generation_job_id):
            raise ValueError("variant practice needs knowledge_id")
        return self


class FieldAnswerInput(Strict):
    field_id: str = Field(min_length=1, max_length=40)
    answer_text: str = Field(min_length=1, max_length=4000)


class AnswerInput(Strict):
    item_id: str = Field(max_length=64)
    answer_text: str | None = Field(default=None, min_length=1, max_length=4000)
    selected_options: list[Annotated[str, Field(min_length=1, max_length=8)]] | None = Field(default=None, max_length=10)
    answers: list[FieldAnswerInput] | None = Field(default=None, max_length=12)

    @model_validator(mode="after")
    def check_answer(self):
        if not (self.answer_text or self.selected_options or self.answers):
            raise ValueError("an answer is required")
        return self


class ExitInput(Strict):
    reason: Literal["tired", "not_now", "done", "other"] = "not_now"


class RetryInput(Strict):
    pass


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
        if item.owner_student_id:
            require_student(session, actor, item.owner_student_id)
        return item

    def load_session(session, actor, session_id, lock=False):
        query = select(PracticeSession).where(PracticeSession.id == session_id, PracticeSession.tenant_id == actor.tenant_id)
        row = session.scalar(query)
        if row is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, row.student_id)
        if lock:
            # Match generation and assessment workers: student before session.
            session.scalar(select(Student).where(Student.id == row.student_id).with_for_update())
            row = session.scalar(query.with_for_update().execution_options(populate_existing=True))
        return row

    def bank_graph(session, actor, graph_version=None, student_id=None):
        if student_id:
            require_student(session, actor, student_id)
            graph = require_published_graph(session, student_id)
            if graph_version and graph_version != graph.version:
                raise Problem(409, "conflict", "revision_conflict", "学生教材已变化，请刷新后选择当前图谱。")
            return graph
        return _graph_by_version(session, graph_version) if graph_version else require_published_graph(session)

    @app.get("/v1/practice-items", operation_id="listPracticeItems")
    def list_items(actor: ActorDep, session: SessionDep, knowledge_id: str | None = None, status: str = "candidate",
                   graph_version: str | None = None, student_id: str | None = None):
        require_staff(actor)
        graph = bank_graph(session, actor, graph_version, student_id)
        query = select(PracticeItem).where(PracticeItem.tenant_id == actor.tenant_id, PracticeItem.status == status,
                                           PracticeItem.graph_version == graph.version)
        query = query.where((PracticeItem.owner_student_id.is_(None)) | (PracticeItem.owner_student_id == student_id))
        if knowledge_id:
            query = query.where(PracticeItem.knowledge_id == knowledge_id)
        rows = session.scalars(query.order_by(PracticeItem.knowledge_id, PracticeItem.created_at).limit(200))
        return {"items": [item_dict(r, staff=True) for r in rows]}

    @app.post("/v1/practice-items", status_code=201, operation_id="createPracticeItem")
    def create_item(payload: ItemInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        graph = bank_graph(session, actor, payload.graph_version, payload.student_id)
        node = _names(session, graph_version=graph.version).get(payload.knowledge_id)
        if node is None or node.node_type not in ("concept", "skill"):
            raise Problem(422, "validation", "invalid_request", "题目只能关联已发布的具体知识点。")
        stamp = now()
        item = PracticeItem(id=new_id("item"), tenant_id=actor.tenant_id, item_family_id=payload.item_family_id or f"family_{uuid4().hex[:16]}",
                            **payload.model_dump(exclude={"item_family_id", "graph_version", "student_id"}),
                            graph_version=graph.version, source="staff", status="approved", created_by=actor.id,
                            reviewed_by=actor.id, reviewed_at=stamp, revision=1, created_at=stamp, updated_at=stamp)
        session.add(item)
        session.commit()
        return item_dict(item, staff=True)

    @app.post("/v1/practice-items/{item_id}/review", operation_id="reviewPracticeItem")
    def review_item(item_id: str, payload: ReviewInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        item = load_item(session, actor, item_id, lock=True)
        if item.owner_student_id:
            raise Problem(409, "conflict", "revision_conflict", "学员专属练习已绑定独立验题版本，不能作为共享题库直接改写。")
        if item.revision != payload.expected_revision:
            raise Problem(409, "conflict", "revision_conflict", "题目已被修改，请刷新后再审核。")
        allowed = {"approve": ("candidate",), "reject": ("candidate",), "retire": ("approved",)}[payload.action]
        if item.status not in allowed:
            raise Problem(409, "conflict", "revision_conflict", "题目当前状态不能执行该操作。")
        if payload.action == "approve":
            _graph_by_version(session, item.graph_version)
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
        source_question = None
        source_homework, question = None, None
        student_id = payload.student_id
        if payload.source_case_id:
            case = session.get(Entity, payload.source_case_id)
            if case is None or case.kind != "case":
                raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
            source_homework = session.get(Homework, case.homework_id)
            require_student(session, actor, source_homework.student_id)
            if student_id and student_id != source_homework.student_id:
                raise Problem(404, "not_found", "not_found", "来源错题不存在或不可访问。")
            student_id = source_homework.student_id
            question = session.scalar(select(Entity).where(Entity.kind == "question", Entity.homework_id == case.homework_id,
                                                           Entity.id == case.document["question_id"]))
            source_question = (question.document.get("stem_text") or "")[:400] if question else None
        graph = bank_graph(session, actor, payload.graph_version, student_id)
        node = _names(session, graph_version=graph.version).get(payload.knowledge_id)
        if node is None or node.node_type not in ("concept", "skill"):
            raise Problem(422, "validation", "invalid_request", "只能为已发布的具体知识点生成候选。")
        if source_homework and not _source_matches_graph(session, student_id, source_homework, question, graph):
            raise Problem(409, "conflict", "curriculum_mismatch", "来源错题属于其他教材范围，请先核实教材。")
        job_id = enqueue(session, actor.tenant_id, BANK_JOB_STUDENT, "generate_practice",
                         {"knowledge_id": node.id, "graph_version": graph.version, "count": payload.count,
                          "source_question": source_question, "requested_by": actor.id},
                         f"practice:{actor.tenant_id}:" + hashlib.sha256(f"{graph.version}:{node.id}".encode()).hexdigest())
        session.commit()
        return {"job_id": job_id, "graph_version": graph.version, "status": "queued",
                "notice": "模型只生成候选题，老师审核通过后学生才会看到。"}

    @app.get("/v1/practice-generations/{job_id}", operation_id="getPracticeGeneration")
    def generation_status(job_id: str, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        job = session.get(LearningOutbox, job_id)
        if job is None or job.kind != "generate_practice" or job.tenant_id != actor.tenant_id:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        outcome = (job.payload.get("outcome") or {}).get("status")
        items = [i.id for i in session.scalars(select(PracticeItem).where(PracticeItem.tenant_id == job.tenant_id, PracticeItem.source == "model",
                                                                          PracticeItem.graph_version == job.payload.get("graph_version"),
                                                                          PracticeItem.knowledge_id == job.payload["knowledge_id"]))
                 if (i.generation or {}).get("job_id") == job.id]
        status = {"queued": "queued", "running": "running", "dead": "failed"}.get(job.status)
        if status is None:
            status = "succeeded" if outcome in (None, "proposed") else "failed"
        return {"job_id": job.id, "status": status, "failure_code": None if status != "failed" else (outcome or job.last_error),
                "knowledge_id": job.payload["knowledge_id"], "graph_version": job.payload.get("graph_version"), "candidate_item_ids": items}

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
        names, states = _names(session, student_id), _states(session, student_id)
        graph = require_published_graph(session, student_id)
        counts = dict(session.execute(select(PracticeItem.knowledge_id, func.count(PracticeItem.id))
                                      .where(PracticeItem.tenant_id == student.tenant_id, PracticeItem.status == "approved",
                                             PracticeItem.graph_version == graph.version)
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

    @app.get("/v1/students/{student_id}/variant-practice", operation_id="getVariantPractice")
    def variant_practice(student_id: str, actor: ActorDep, session: SessionDep):
        """Return current AI mistake clusters for the student's practice page."""
        student = require_student(session, actor, student_id)
        # Existing jobs may have been completed before automatic graph linking
        # was enabled. Backfill those links the first time this page is opened.
        if auto_confirm_model_links(session, student.tenant_id, student.id):
            project_student(session, student.tenant_id, student.id)
            session.commit()
        return _variant_error_overview(session, student, gate_status(session, student_id))

    @app.post("/v1/students/{student_id}/practice-sessions", status_code=201, operation_id="startPracticeSession")
    def start(student_id: str, payload: StartInput, actor: ActorDep, session: SessionDep, idempotency_key: OptionalKey = None):
        operation = f"POST /v1/students/{student_id}/practice-sessions"
        student = require_student(session, actor, student_id)
        if "student" not in actor.roles:
            raise Problem(403, "authorization", "forbidden", "额外活动只能由孩子本人选择开始。")
        session.scalar(select(Student).where(Student.id == student_id).with_for_update())
        if payload.generation_job_id:
            require_owned_generated_reference(session, student.tenant_id, student.id, payload.generation_job_id)
        if (replay := replay_idempotent(session, actor, student_id, operation, idempotency_key, payload.model_dump(mode="json"))) is not None:
            return replay
        if payload.generation_job_id:
            from .practice_generation import start_generated
            result = start_generated(session, actor, student, payload.generation_job_id,
                                     source_case_id=payload.source_case_id, knowledge_id=payload.knowledge_id)
            result = remember_idempotent(session, actor, student_id, operation, idempotency_key,
                                         payload.model_dump(mode="json"), result)
            session.commit()
            return result
        for active in session.scalars(select(PracticeSession).where(
                PracticeSession.student_id == student_id, PracticeSession.status == "active")):
            if _generated_variant(active):
                continue
            _expire(active, now())
            if active.status == "active":
                raise Problem(409, "conflict", "practice_active", "已有进行中的活动，请先完成或结束。")
        at = now()
        gate = gate_status(session, student_id, at)
        direct_variant = payload.purpose == "variant" and payload.source_case_id is not None
        if not gate["open"] and not direct_variant:
            session.commit()
            raise Problem(409, "conflict", "optional_gate_closed", "现在还不能开始额外活动：需要学校任务已完成、老师确认有余力并开放时间。")
        names = _names(session, student_id)
        graph = require_published_graph(session, student_id)
        if payload.purpose == "variant" and payload.knowledge_id not in names:
            raise Problem(422, "validation", "invalid_request", "练习必须关联当前教材图谱的知识点。")
        source_stem = None
        source_case = None
        if payload.source_case_id:
            source_case = session.get(Entity, payload.source_case_id)
            if source_case is None or source_case.kind != "case":
                raise Problem(404, "not_found", "not_found", "来源错题不存在或不可访问。")
            source_homework = session.get(Homework, source_case.homework_id)
            if source_homework is None or source_homework.student_id != student_id:
                raise Problem(404, "not_found", "not_found", "来源错题不存在或不可访问。")
            source_question = session.get(Entity, source_case.document.get("question_id"))
            source_stem = source_question.document.get("stem_text") if source_question else None
            if not _source_matches_graph(session, student_id, source_homework, source_question, graph):
                raise Problem(409, "conflict", "curriculum_mismatch", "这道历史错题不属于当前教材范围，不能自动用于新教材练习。")
        if payload.purpose == "variant":
            if source_case is not None:
                source_decision = source_case.document.get("current_decision") or {}
                source_links = list(_current_links(session, source_case.document["question_id"]).values())
                confirmed_ids = {link.knowledge_id for link in source_links if link.status == "confirmed"}
                rejected_ids = {link.knowledge_id for link in source_links if link.status == "rejected"}
                matched_ids = {node.id for label in source_decision.get("knowledge_points", [])
                               if isinstance(label, str) and (node := _match_node(label, list(names.values())))}
                if (source_case.document.get("lifecycle", "active") != "active"
                        or source_decision.get("validity") not in {"candidate", "confirmed"}
                        or source_decision.get("verdict") not in ("incorrect", "partially_correct")
                        or (source_decision.get("origin") == "model" and not model_decision_usable(source_decision))
                        or payload.knowledge_id in rejected_ids
                        or payload.knowledge_id not in (confirmed_ids | matched_ids)):
                    raise Problem(409, "conflict", "practice_not_needed", "这道题当前没有可用于举一反三的错题判定。")
            else:
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
        items = pick_items(session, student.tenant_id, student_id, knowledge_ids, count, source_stem) if count else []
        if payload.purpose == "variant" and not items:
            raise Problem(409, "conflict", "no_reviewed_items", "这个知识点还没有老师审核过的练习题。")
        row = PracticeSession(id=new_id("practice"), tenant_id=student.tenant_id, student_id=student_id, purpose=payload.purpose,
                              graph_version=graph.version,
                              knowledge_ids=knowledge_ids, source_case_id=payload.source_case_id, item_ids=[i.id for i in items],
                              hints_used=[], gate={**gate, "child_willing": {"value": True, "source": "student", "actor": actor.id,
                                                                            "at": at.isoformat()},
                                     "direct_variant": direct_variant},
                              status="active", started_at=at,
                              budget_ends_at=at + timedelta(minutes=max(gate["remaining_minutes"], 10) if direct_variant else gate["remaining_minutes"]))
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
        changed = False
        for row in rows:
            changed = _expire(row, at) or changed
        if changed:
            session.commit()
        return {"items": [session_dict(session, r, staff=is_staff(actor)) for r in rows]}

    @app.get("/v1/practice-sessions/{session_id}", operation_id="getPracticeSession")
    def get_session(session_id: str, actor: ActorDep, session: SessionDep):
        row = load_session(session, actor, session_id)
        if _expire(row, now()):
            session.commit()
        return session_dict(session, row, staff=is_staff(actor))

    @app.post("/v1/practice-sessions/{session_id}/retry", status_code=201, operation_id="retryPracticeSession")
    def retry(session_id: str, payload: RetryInput, actor: ActorDep, session: SessionDep,
              idempotency_key: OptionalKey = None):
        from .practice_generation import retry_generated
        row = load_session(session, actor, session_id)
        if "student" not in actor.roles:
            raise Problem(403, "authorization", "forbidden", "由孩子本人选择重新作答。")
        session.scalar(select(Student).where(Student.id == row.student_id).with_for_update())
        row = load_session(session, actor, session_id, lock=True)
        if not _generated_variant(row):
            raise Problem(409, "conflict", "retry_not_available", "只有已核验的举一反三错题可以重新作答。")
        require_owned_generated_reference(session, row.tenant_id, row.student_id,
                                          row.gate["generation_job_id"], item_ids=row.item_ids)
        operation = f"POST /v1/practice-sessions/{session_id}/retry"
        body = payload.model_dump(mode="json")
        if (replay := replay_idempotent(session, actor, row.student_id, operation, idempotency_key, body)) is not None:
            return replay
        result = remember_idempotent(session, actor, row.student_id, operation, idempotency_key, body,
                                     retry_generated(session, actor, row))
        session.commit()
        return result

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
        require_valid_reference(item)
        if item is None or item.graph_version != row.graph_version:
            raise Problem(409, "conflict", "revision_conflict", "题目与本次活动的教材版本不一致。")
        if not item.hint_text:
            raise Problem(404, "not_found", "not_found", "这道题没有提示。")
        if item_id not in row.hints_used:
            row.hints_used = [*row.hints_used, item_id]
        session.commit()
        return {"item_id": item_id, "hint_text": item.hint_text}

    @app.post("/v1/practice-sessions/{session_id}/items/{item_id}/solution", operation_id="revealPracticeSolution")
    def solution(session_id: str, item_id: str, actor: ActorDep, session: SessionDep):
        row = load_session(session, actor, session_id, lock=True)
        if row.purpose != "variant":
            raise Problem(409, "conflict", "solutions_disabled", "预习小测不提供参考答案。")
        if item_id not in row.item_ids:
            raise Problem(409, "conflict", "revision_conflict", "题目不在本次活动中。")
        item = load_item(session, actor, item_id, lock=True)
        require_valid_reference(item)
        if (item.graph_version != row.graph_version or item.owner_student_id not in (None, row.student_id)
                or (item.status == "ai_verified" and item.owner_student_id != row.student_id)):
            raise Problem(409, "conflict", "revision_conflict", "题目与本次活动的学员或教材版本不一致。")
        latest_assessment = session.scalar(select(PracticeAssessment).where(
            PracticeAssessment.session_id == row.id, PracticeAssessment.item_id == item_id)
            .order_by(PracticeAssessment.created_at.desc(), PracticeAssessment.id.desc()).limit(1))
        if latest_assessment and latest_assessment.status in ("queued", "grading"):
            # Async graders snapshot hints_used on completion, not submission.
            # A dead outbox is terminal even if its assessment was left queued.
            job = session.get(LearningOutbox, latest_assessment.outbox_id) if latest_assessment.outbox_id else None
            if job is None or job.status != "dead":
                raise Problem(409, "conflict", "solution_pending", "本次作答正在核验，完成后可查看参考答案。")
        verified = item.status == "approved" or (item.status == "ai_verified"
                    and isinstance(item.verification, dict) and item.verification.get("status") == "verified")
        answer_text = _solution_answer_text(item) if verified else None
        if not answer_text or not isinstance(item.solution_text, str) or not item.solution_text.strip():
            raise Problem(404, "not_found", "not_found", "这道题暂无已核实的参考答案与步骤。")
        # Historical variants remain readable. Reveal records help for future
        # submissions only; it never rewrites an existing response or mastery.
        if item_id not in row.hints_used:
            row.hints_used = [*row.hints_used, item_id]
        body = {"item_id": item_id, "answer_text": answer_text, "solution_text": item.solution_text}
        session.commit()
        return body

    @app.post("/v1/practice-sessions/{session_id}/responses", status_code=201, operation_id="answerPracticeItem")
    def answer(session_id: str, payload: AnswerInput, actor: ActorDep, session: SessionDep, idempotency_key: OptionalKey = None):
        row = load_session(session, actor, session_id, lock=True)
        if payload.item_id in row.item_ids:
            require_valid_reference(session.get(PracticeItem, payload.item_id))
        operation = f"POST /v1/practice-sessions/{session_id}/responses"
        if (replay := replay_idempotent(session, actor, row.student_id, operation, idempotency_key, payload.model_dump(mode="json"))) is not None:
            return JSONResponse(replay, status_code=202) if replay.get("result") == "pending" else replay
        at = now()
        _expire(row, at)
        if row.status != "active" or payload.item_id not in row.item_ids:
            session.commit()
            raise Problem(409, "conflict", "revision_conflict", "活动已结束或题目不在本次活动中。")
        if session.scalar(select(PracticeResponse.id).where(PracticeResponse.session_id == row.id, PracticeResponse.item_id == payload.item_id)):
            raise Problem(409, "conflict", "revision_conflict", "这道题已经作答。")
        item = session.get(PracticeItem, payload.item_id)
        if item is None or item.graph_version != row.graph_version:
            raise Problem(409, "conflict", "revision_conflict", "题目与本次活动的教材版本不一致。")
        pending_assessment = session.scalar(select(PracticeAssessment).where(PracticeAssessment.session_id == row.id,
            PracticeAssessment.item_id == item.id, PracticeAssessment.status.in_(("queued", "grading"))))
        if pending_assessment:
            outbox = session.get(LearningOutbox, pending_assessment.outbox_id)
            if outbox and outbox.status in ("queued", "running"):
                from .practice_generation import queue_assessment
                body = queue_assessment(session, row, item, payload)
                session.commit()
                return JSONResponse(body, status_code=202)
        details, feedback = None, None
        if item.status == "ai_verified":
            from .practice_generation import grade_generated
            if item.owner_student_id != row.student_id or item.tenant_id != row.tenant_id:
                raise Problem(409, "conflict", "revision_conflict", "题目与本次学员不一致。")
            grading, details = grade_generated(item, payload, rule_only=True)
            if grading is None:
                from .practice_generation import queue_assessment
                body = queue_assessment(session, row, item, payload)
                body = remember_idempotent(session, actor, row.student_id, operation, idempotency_key,
                                           payload.model_dump(mode="json"), body)
                session.commit()
                return JSONResponse(body, status_code=202)
            result, feedback = grading["result"], grading["feedback"]
            if result == "unscorable":
                return {"item_id": item.id, "result": result, "session_status": row.status, "retryable": True,
                        "feedback": feedback, "solution_text": None}
            details = {**details, "feedback": feedback}
        else:
            if payload.answer_text is None:
                raise Problem(422, "validation", "invalid_request", "这道历史练习需要文字作答。")
            result = grade(item, payload.answer_text)
        submitted_text = (payload.answer_text or ("、".join(payload.selected_options) if payload.selected_options else
                          "\n".join(f"{a.field_id}: {a.answer_text}" for a in payload.answers or [])))
        session.add(PracticeResponse(id=new_id("presp"), tenant_id=row.tenant_id, student_id=row.student_id, session_id=row.id,
                                     item_id=item.id, knowledge_id=item.knowledge_id, graph_version=row.graph_version,
                                     event_type="optional_variant" if row.purpose == "variant" else "optional_quiz",
                                     answer_text=submitted_text.strip(), result=result, hint_used=item.id in row.hints_used,
                                     details=details, occurred_at=at))
        session.flush()
        answered = session.scalar(select(func.count()).select_from(PracticeResponse).where(PracticeResponse.session_id == row.id))
        if answered == len(row.item_ids):
            row.status, row.ended_at = "completed", at
        body = remember_idempotent(session, actor, row.student_id, operation, idempotency_key, payload.model_dump(mode="json"),
                                   {"item_id": item.id, "result": result, "solution_text": item.solution_text, "session_status": row.status,
                                    **({"feedback": feedback} if feedback else {})})
        session.commit()
        return body

    @app.post("/v1/practice-sessions/{session_id}/exit", operation_id="exitPracticeSession")
    def exit_session(session_id: str, payload: ExitInput, actor: ActorDep, session: SessionDep):
        row = load_session(session, actor, session_id, lock=True)
        _expire(row, now())
        if row.status == "active":
            finished_reading = row.purpose == "preview" and not row.item_ids and payload.reason == "done"
            row.status, row.exit_reason, row.ended_at = "completed" if finished_reading else "exited", payload.reason, now()
        session.commit()
        return session_dict(session, row, staff=is_staff(actor))

    from .practice_generation import install_variant_generation
    install_variant_generation(app, database_session, current_actor)


from . import practice_generation as _student_variant_jobs  # noqa: E402,F401
