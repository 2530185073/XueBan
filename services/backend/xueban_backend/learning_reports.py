"""Half-month learning reports for guardians.

Every period produces a report, quiz or not: windows are Asia/Shanghai natural
half-months, left-closed and right-open. A report is a locked snapshot of
current facts with real denominators, help conditions and what is still
unknown. Drafts follow the facts; a published version is never rewritten, and a
change to facts inside its window yields a correction version that staff must
publish with an explanation. Guardians have no account: staff issue revocable
links that always open the newest published version with its correction trail.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import html
import json
import secrets
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .auth import Actor
from .correction_models import Entity
from .device_auth import utc
from .errors import Problem
from .governance import register_downstream_purger
from .learning_models import (KnowledgeDiagnosis, KnowledgeNode, LearningEvidence, LearningProjection, LearningReport,
                              ReportShareLink, StudentLearningProfile)
from .learning_projection import (fingerprints, new_id, published_graph, register_periodic, register_projection_followup)
from .learning_rules import compute_state
from .models import Homework, Student

SHANGHAI = ZoneInfo("Asia/Shanghai")
REPORT_RULE = "report-r1"
REFRESH_PERIODS = 12
LINK_DAYS_DEFAULT, LINK_DAYS_MAX = 30, 90
REPORT_SECTIONS = []
ASSISTANCE_LABEL = {"independent": "独立完成（老师核实）", "no_recorded_help": "未记录帮助", "direction": "看过提示方向",
                    "staff_help": "老师现场帮助", "solution_seen": "看过答案或解法", "unknown": "帮助情况未知"}
STATUS_LABEL = {"needs_help_now": "当前需要帮助", "consolidating": "正在巩固", "stable_for_now": "阶段性较稳",
                "no_judgment": "暂不判断"}
LIMITATIONS = ("本报告只汇总本期在托管班拍照、经老师核实的学校作业；不代表全部学校作业或整章掌握情况。"
               "没有证据不表示薄弱，也不为填满报告安排测验。学校作业题量和难度不同，不做横向比较；观察变化不代表因果提分。")


def register_report_section(function):
    """function(session, student, window_start, window_end) -> (key, json-safe dict) adds one section."""
    REPORT_SECTIONS.append(function)
    return function


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return utc(value).isoformat() if value else None


def _parse(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_day(moment: datetime) -> date:
    return utc(moment).astimezone(SHANGHAI).date()


def period_of(day: date) -> tuple[date, date]:
    if day.day < 16:
        return day.replace(day=1), day.replace(day=16)
    following = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return day.replace(day=16), following


def window(start: date, end: date) -> tuple[datetime, datetime]:
    return (datetime.combine(start, time(), SHANGHAI).astimezone(timezone.utc),
            datetime.combine(end, time(), SHANGHAI).astimezone(timezone.utc))


def last_completed_period(at: datetime) -> tuple[date, date]:
    current_start, _ = period_of(local_day(at))
    return period_of(current_start - timedelta(days=1))


def parse_period(value: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(value)
    except ValueError:
        raise Problem(422, "validation", "invalid_request", "周期起点须为 YYYY-MM-01 或 YYYY-MM-16。") from None
    if start.day not in (1, 16):
        raise Problem(422, "validation", "invalid_request", "周期起点须为每月 1 日或 16 日。")
    return period_of(start)


def _digest(snapshot) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_snapshot(session, student: Student, start: date, end: date) -> dict:
    """Facts of one window, recomputed from current valid records only."""
    window_start, window_end = window(start, end)
    inside = lambda moment: moment is not None and window_start <= moment < window_end  # noqa: E731
    homeworks = [h for h in session.scalars(select(Homework).where(Homework.student_id == student.id,
                                                                   Homework.processing_blocked.is_(False)))]
    first_seen = min((utc(h.occurred_at) for h in homeworks), default=None)
    period_homeworks = {h.id: h for h in homeworks if inside(utc(h.occurred_at))}
    entities = defaultdict(dict)
    if homeworks:
        for entity in session.scalars(select(Entity).where(Entity.homework_id.in_([h.id for h in homeworks]),
                                                           Entity.kind.in_(("case", "question", "decision", "help", "hint")))):
            key = entity.document.get("question_id") if entity.kind == "question" else entity.id
            entities[entity.kind][key] = (entity.homework_id, entity.document)
    tasks = Counter()
    for homework_id, case in entities["case"].values():
        if homework_id not in period_homeworks or case.get("lifecycle", "active") != "active":
            continue
        tasks["questions"] += 1
        decision = case.get("current_decision") or {}
        if decision.get("origin") == "staff" and decision.get("validity") == "confirmed":
            tasks["staff_verified"] += 1
            tasks[f"verdict_{decision['verdict']}"] += 1
        elif decision.get("verdict") == "unable_to_judge" or not decision:
            tasks["not_judged"] += 1
        else:
            tasks["awaiting_verification"] += 1
        if case.get("progress") == "verified_resolved":
            tasks["corrected_resolved"] += 1
        elif case.get("progress") in ("needs_correction", "editing", "rechecking", "paused"):
            tasks["correction_open"] += 1
    help_rows = [(hw, doc) for hw, doc in entities["help"].values() if inside(_parse(doc["created_at"]))]
    staff_help = []
    for _, doc in sorted(help_rows, key=lambda r: r[1]["created_at"]):
        if doc["status"] in ("resolved", "unresolved") and doc.get("resolution_note"):
            case = entities["case"].get(doc["case_id"], (None, {}))[1]
            question = entities["question"].get(case.get("question_id"), (None, {}))[1]
            staff_help.append({"date": local_day(_parse(doc["created_at"])).isoformat(), "kind": doc["kind"],
                               "question": (question.get("stem_text") or "")[:80], "outcome": doc["status"],
                               "note": doc["resolution_note"][:300]})
    hints = [doc for _, doc in entities["hint"].values() if doc.get("first_shown_at") and inside(_parse(doc["first_shown_at"]))]
    graph = published_graph(session)
    names = {n.id: n for n in session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version))} if graph else {}
    valid = defaultdict(list)
    for row in session.scalars(select(LearningEvidence).where(LearningEvidence.student_id == student.id,
                                                              LearningEvidence.validity == "valid")):
        valid[row.knowledge_id].append({**row.document, "id": row.id})
    diagnoses = defaultdict(list)
    for row in session.scalars(select(KnowledgeDiagnosis).where(KnowledgeDiagnosis.student_id == student.id,
                                                                KnowledgeDiagnosis.status == "active")):
        if utc(row.created_at) < window_end:
            diagnoses[row.knowledge_id].append({"id": row.id, "kind": row.kind, "scope_text": row.scope_text,
                                                "evidence_ids": row.evidence_ids})
    focus, steady, evidence_ids = [], [], []
    for knowledge_id in sorted(valid):
        rows = sorted((e for e in valid[knowledge_id] if inside(_parse(e["occurred_at"]))), key=lambda e: e["occurred_at"])
        if not rows:
            continue
        evidence_ids += [e["id"] for e in rows]
        history = [e for e in valid[knowledge_id] if _parse(e["occurred_at"]) < window_end]
        state = compute_state(knowledge_id, history, diagnoses.get(knowledge_id, []), window_end)
        scorable = [e for e in rows if e["result"] != "unscorable"]
        errors = [e for e in scorable if e["result"] in ("incorrect", "partial")]
        name = names[knowledge_id].name if knowledge_id in names else knowledge_id
        if not errors:
            steady.append({"knowledge_id": knowledge_id, "name": name, "correct": len(scorable),
                           "independent_correct": sum(1 for e in scorable if e["independent"])})
            continue
        cards = []
        for item in errors[-5:]:
            homework_id, question = entities["question"].get(item["question_id"], (None, {}))
            decision = entities["decision"].get(item["decision_id"], (None, {}))[1]
            homework = period_homeworks.get(homework_id) or next((h for h in homeworks if h.id == homework_id), None)
            pages = [r.get("page_index") for r in question.get("regions", []) if isinstance(r, dict) and r.get("page_index") is not None]
            cards.append({"evidence_id": item["id"], "date": local_day(_parse(item["occurred_at"])).isoformat(),
                          "homework_title": homework.title if homework else None, "page": (pages[0] + 1) if pages else None,
                          "question": (question.get("stem_text") or "")[:120], "result": item["result"],
                          "assistance": ASSISTANCE_LABEL.get(item["assistance_level"], item["assistance_level"]),
                          "staff_explanation": "；".join(b["summary"] for b in decision.get("basis", [])
                                                        if b.get("kind") in ("staff_observation", "calculation"))[:300] or None,
                          "certainty": "老师已核实"})
        focus.append({
            "knowledge_id": knowledge_id, "name": name, "errors": len(errors), "denominator": len(scorable),
            "independent_errors": sum(1 for e in errors if e["independent"]),
            "counter_evidence": sum(1 for e in scorable if e["result"] == "correct"),
            "assistance": dict(sorted(Counter(ASSISTANCE_LABEL.get(e["assistance_level"], e["assistance_level"]) for e in rows).items())),
            "status_at_period_end": state["learning_status"], "status_label": STATUS_LABEL[state["learning_status"]],
            "verified_scope": [d["scope_text"] for d in diagnoses.get(knowledge_id, []) if d["kind"] == "verified_weak_scope"],
            "possible_causes": [d["scope_text"] for d in diagnoses.get(knowledge_id, []) if d["kind"] == "suspected_cause"],
            "unknown": [q["code"] for q in state["open_questions"]], "cards": cards,
            "next_step": f"后续学校作业遇到「{name}」相关步骤时，由坐班老师现场观察和答疑；家长无需额外布题。"})
    profile = session.get(StudentLearningProfile, student.id)
    current = (profile.document.get("current_knowledge_ids") or []) if profile else []
    unknown = [{"knowledge_id": k, "name": names[k].name if k in names else k} for k in current if not any(
        inside(_parse(e["occurred_at"])) for e in valid.get(k, []))]
    sections = {}
    for contributor in REPORT_SECTIONS:
        key, value = contributor(session, student, window_start, window_end)
        sections[key] = value
    observed = sorted({local_day(utc(h.occurred_at)).isoformat() for h in period_homeworks.values()})
    return {
        "rule_version": REPORT_RULE, "period": {"start": start.isoformat(), "end_exclusive": end.isoformat(),
                                                "timezone": "Asia/Shanghai"},
        "coverage": {"observed_days": len(observed), "observed_dates": observed, "homeworks": len(period_homeworks),
                     "partial_period": first_seen is not None and first_seen > window_start and local_day(first_seen) > start,
                     "first_seen_date": local_day(first_seen).isoformat() if first_seen else None},
        "school_tasks": {"questions": tasks["questions"], "staff_verified": tasks["staff_verified"],
                         "awaiting_verification": tasks["awaiting_verification"], "not_judged": tasks["not_judged"],
                         "verdicts": {v: tasks[f"verdict_{v}"] for v in ("correct", "incorrect", "partially_correct", "unanswered")},
                         "corrected_resolved": tasks["corrected_resolved"], "correction_open": tasks["correction_open"]},
        "help": {"requests": len(help_rows), "by_kind": dict(sorted(Counter(d["kind"] for _, d in help_rows).items())),
                 "resolved": sum(1 for _, d in help_rows if d["status"] == "resolved"),
                 "hints_viewed": len(hints), "answers_shown": sum(1 for d in hints if d.get("exposes_answer")),
                 "staff_help": staff_help[:10]},
        "focus": focus, "steady": steady, "unknown_current": unknown,
        "sufficient": bool(focus or steady), "evidence_ids": sorted(evidence_ids), "sections": sections,
        "limitations": LIMITATIONS}


def report_dict(report: LearningReport, *, public=False) -> dict:
    body = {"id": report.id, "student_id": report.student_id, "period_start": report.period_start, "period_end": report.period_end,
            "version": report.version, "status": report.status, "snapshot": report.snapshot, "staff_note": report.staff_note,
            "next_step": report.next_step, "correction_note": report.correction_note, "supersedes_id": report.supersedes_id,
            "published_at": iso(report.published_at)}
    if not public:
        body.update({"revision": report.revision, "regenerated_reason": report.regenerated_reason, "published_by": report.published_by,
                     "source_digest": report.source_digest, "created_at": iso(report.created_at),
                     "updated_at": iso(report.updated_at)})
    return body


def versions(session, student_id, period_start, lock=False):
    query = (select(LearningReport).where(LearningReport.student_id == student_id, LearningReport.period_start == period_start)
             .order_by(LearningReport.version))
    return list(session.scalars(query.with_for_update() if lock else query))


def refresh(session, student: Student, start: date, end: date, reason: str) -> LearningReport | None:
    """Keep the draft equal to current facts; open a correction when published facts changed."""
    rows = versions(session, student.id, start.isoformat(), lock=True)
    snapshot = build_snapshot(session, student, start, end)
    digest, stamp = _digest(snapshot), now()
    latest = rows[-1] if rows else None
    published = next((r for r in reversed(rows) if r.status == "published"), None)
    if latest is not None and latest.status == "draft":
        if published is not None and published.source_digest == digest:
            session.delete(latest)
            return published
        if latest.source_digest != digest:
            latest.snapshot, latest.source_digest, latest.regenerated_reason = snapshot, digest, reason
            latest.revision, latest.updated_at = latest.revision + 1, stamp
        return latest
    if published is not None and published.source_digest == digest:
        return published
    report = LearningReport(id=new_id("report"), tenant_id=student.tenant_id, campus_id=student.campus_id, student_id=student.id,
                            period_start=start.isoformat(), period_end=end.isoformat(), version=(latest.version + 1) if latest else 1,
                            status="draft", source_digest=digest, snapshot=snapshot,
                            next_step=published.next_step if published else None, staff_note=published.staff_note if published else None,
                            supersedes_id=published.id if published else None,
                            regenerated_reason="correction_required" if published else reason, revision=1,
                            created_at=stamp, updated_at=stamp)
    session.add(report)
    session.flush()
    return report


@register_periodic(timedelta(minutes=10))
def generate_due_reports(session, worker):
    """Every active learner gets a report for the period that just ended."""
    start, end = last_completed_period(now())
    existing = set(session.scalars(select(LearningReport.student_id).where(LearningReport.period_start == start.isoformat())))
    for student in session.scalars(select(Student).where(Student.active.is_(True))):
        if student.id not in existing:
            refresh(session, student, start, end, "scheduled")


@register_projection_followup
def refresh_after_projection(session, tenant_id, student_id, worker):
    student = session.get(Student, student_id)
    if student is None:
        return
    starts = sorted(set(session.scalars(select(LearningReport.period_start).where(LearningReport.student_id == student_id))),
                    reverse=True)[:REFRESH_PERIODS]
    for value in starts:
        refresh(session, student, *period_of(date.fromisoformat(value)), "facts_changed")


@register_downstream_purger
def purge_reports(session, tenant_id, student_id):
    for model in (ReportShareLink, LearningReport):
        session.execute(delete(model).where(model.student_id == student_id, model.tenant_id == tenant_id))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateInput(Strict):
    period_start: str = Field(pattern=r"^\d{4}-\d{2}-(01|16)$")


class EditInput(Strict):
    expected_revision: int = Field(ge=1)
    staff_note: str | None = Field(default=None, max_length=500)
    next_step: str | None = Field(default=None, max_length=300)


class PublishInput(Strict):
    expected_revision: int = Field(ge=1)
    correction_note: str | None = Field(default=None, min_length=4, max_length=500)


class LinkInput(Strict):
    expires_in_days: int = Field(default=LINK_DAYS_DEFAULT, ge=1, le=LINK_DAYS_MAX)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_link(session, token: str):
    link = session.scalar(select(ReportShareLink).where(ReportShareLink.token_digest == token_digest(token)))
    if link is None or link.revoked_at is not None or utc(link.expires_at) <= now():
        raise Problem(404, "not_found", "not_found", "链接不存在、已过期或已被撤销。")
    origin = session.get(LearningReport, link.report_id)
    if origin is None:
        raise Problem(404, "not_found", "not_found", "链接不存在、已过期或已被撤销。")
    rows = versions(session, origin.student_id, origin.period_start)
    shown = [r for r in rows if r.status in ("published", "superseded")]
    current = next(r for r in reversed(shown) if r.status == "published")
    return link, current, shown


def public_body(session, current, shown):
    student = session.get(Student, current.student_id)
    return {"student_name": student.display_name, "report": report_dict(current, public=True),
            "corrections": [{"version": r.version, "published_at": iso(r.published_at),
                             "correction_note": r.correction_note} for r in shown if r.version > 1 and r.status in ("published", "superseded")]}


def install_reports(app, engine, cfg, database_session, current_actor):
    from .app import accessible_students, require_student
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]

    def require_staff(actor):
        if not set(actor.roles) & {"sitting_teacher", "campus_admin"}:
            raise Problem(403, "authorization", "forbidden", "需要坐班老师或校区负责人。")

    def load(session, actor, report_id, lock=False):
        query = select(LearningReport).where(LearningReport.id == report_id)
        report = session.scalar(query.with_for_update() if lock else query)
        if report is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, report.student_id)
        return report

    @app.get("/v1/students/{student_id}/reports", operation_id="listLearningReports")
    def list_reports(student_id: str, actor: ActorDep, session: SessionDep):
        require_student(session, actor, student_id)
        rows = session.scalars(select(LearningReport).where(LearningReport.student_id == student_id)
                               .order_by(LearningReport.period_start.desc(), LearningReport.version.desc()))
        is_staff = bool(set(actor.roles) & {"sitting_teacher", "campus_admin"})
        return {"items": [report_dict(r, public=not is_staff) for r in rows if is_staff or r.status == "published"]}

    @app.post("/v1/students/{student_id}/reports", status_code=201, operation_id="generateLearningReport")
    def generate(student_id: str, payload: GenerateInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        student = require_student(session, actor, student_id)
        start, end = parse_period(payload.period_start)
        if window(start, end)[1] > now():
            raise Problem(422, "validation", "invalid_request", "周期尚未结束，结束后才能生成报告。")
        session.scalar(select(Student).where(Student.id == student_id).with_for_update())
        report = refresh(session, student, start, end, "requested")
        session.commit()
        return report_dict(report)

    @app.get("/v1/reports", operation_id="listReportQueue")
    def queue(actor: ActorDep, session: SessionDep, status: Literal["draft", "published", "superseded"] = "draft",
              limit: Annotated[int, Query(ge=1, le=200)] = 100, offset: Annotated[int, Query(ge=0, le=10000)] = 0):
        require_staff(actor)
        students = accessible_students(actor).with_only_columns(Student.id)
        rows = list(session.scalars(select(LearningReport).where(LearningReport.student_id.in_(students), LearningReport.status == status)
                                    .order_by(LearningReport.period_start.desc(), LearningReport.id).offset(offset).limit(limit + 1)))
        return {"items": [{**report_dict(r), "snapshot": None, "focus_count": len(r.snapshot["focus"]),
                           "sufficient": r.snapshot["sufficient"]} for r in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None}

    @app.get("/v1/reports/{report_id}", operation_id="getLearningReport")
    def get_report(report_id: str, actor: ActorDep, session: SessionDep):
        report = load(session, actor, report_id)
        is_staff = bool(set(actor.roles) & {"sitting_teacher", "campus_admin"})
        if not is_staff and report.status != "published":
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        return report_dict(report, public=not is_staff)

    @app.patch("/v1/reports/{report_id}", operation_id="editLearningReport")
    def edit(report_id: str, payload: EditInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        report = load(session, actor, report_id, lock=True)
        if report.status != "draft" or report.revision != payload.expected_revision:
            raise Problem(409, "conflict", "revision_conflict", "报告已变化，请刷新后核对。")
        for field in payload.model_fields_set - {"expected_revision"}:
            setattr(report, field, (getattr(payload, field) or "").strip() or None)
        report.revision, report.updated_at = report.revision + 1, now()
        session.commit()
        return report_dict(report)

    @app.post("/v1/reports/{report_id}/publish", operation_id="publishLearningReport")
    def publish(report_id: str, payload: PublishInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        report = load(session, actor, report_id)
        student = session.scalar(select(Student).where(Student.id == report.student_id).with_for_update())
        report = load(session, actor, report_id, lock=True)
        if report.status != "draft" or report.revision != payload.expected_revision:
            raise Problem(409, "conflict", "revision_conflict", "报告已变化，请刷新后核对。")
        projection = session.get(LearningProjection, student.id)
        current = fingerprints(session, [student.id]).get(student.id)
        if current is not None and (projection is None or projection.source_fingerprint != current):
            raise Problem(409, "conflict", "projection_pending", "最新的改判或作答还在汇总，请稍后再发布。")
        latest = refresh(session, student, *period_of(date.fromisoformat(report.period_start)), "facts_changed")
        if latest is not report or report.revision != payload.expected_revision:
            session.commit()
            raise Problem(409, "conflict", "report_changed", "报告事实已更新，请重新核对后发布。")
        previous = session.get(LearningReport, report.supersedes_id) if report.supersedes_id else None
        if previous is not None and not payload.correction_note:
            raise Problem(422, "validation", "invalid_request", "更正版本必须写明更正说明。")
        stamp = now()
        if previous is not None:
            previous.status, previous.updated_at = "superseded", stamp
        report.status, report.published_by, report.published_at = "published", actor.id, stamp
        report.correction_note = payload.correction_note if previous is not None else None
        report.revision, report.updated_at = report.revision + 1, stamp
        session.commit()
        return report_dict(report)

    @app.post("/v1/reports/{report_id}/share-links", status_code=201, operation_id="createReportShareLink")
    def create_link(report_id: str, payload: LinkInput, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        report = load(session, actor, report_id)
        if report.status != "published":
            raise Problem(409, "conflict", "revision_conflict", "只能分享已发布的报告。")
        token = secrets.token_urlsafe(32)
        stamp = now()
        link = ReportShareLink(id=new_id("share"), tenant_id=report.tenant_id, student_id=report.student_id, report_id=report.id,
                               token_digest=token_digest(token), created_by=actor.id, created_at=stamp,
                               expires_at=stamp + timedelta(days=payload.expires_in_days), view_count=0)
        session.add(link)
        session.commit()
        return {**link_dict(link), "token": token, "path": f"/r/{token}"}

    @app.get("/v1/reports/{report_id}/share-links", operation_id="listReportShareLinks")
    def list_links(report_id: str, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        report = load(session, actor, report_id)
        rows = session.scalars(select(ReportShareLink).where(ReportShareLink.student_id == report.student_id,
                                                             ReportShareLink.report_id.in_(
                                                                 [r.id for r in versions(session, report.student_id, report.period_start)])))
        return {"items": [link_dict(r) for r in rows]}

    @app.post("/v1/report-share-links/{link_id}/revoke", operation_id="revokeReportShareLink")
    def revoke(link_id: str, actor: ActorDep, session: SessionDep):
        require_staff(actor)
        link = session.get(ReportShareLink, link_id)
        if link is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, link.student_id)
        link.revoked_at = link.revoked_at or now()
        session.commit()
        return link_dict(link)

    no_store = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}

    @app.get("/v1/report-links/{token}", include_in_schema=False)
    def public_json(token: str, session: SessionDep):
        link, current, shown = resolve_link(session, token)
        link.view_count, link.last_viewed_at = link.view_count + 1, now()
        body = public_body(session, current, shown)
        session.commit()
        return JSONResponse(body, headers=no_store)

    @app.get("/r/{token}", include_in_schema=False, response_class=HTMLResponse)
    def public_page(token: str, session: SessionDep):
        try:
            link, current, shown = resolve_link(session, token)
        except Problem:
            return HTMLResponse(render_missing(), status_code=404, headers=no_store)
        link.view_count, link.last_viewed_at = link.view_count + 1, now()
        page = render_report(public_body(session, current, shown))
        session.commit()
        return HTMLResponse(page, headers={**no_store, "Content-Security-Policy":
                                           "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"})


def link_dict(link):
    return {"id": link.id, "report_id": link.report_id, "created_by": link.created_by, "created_at": iso(link.created_at),
            "expires_at": iso(link.expires_at), "revoked_at": iso(link.revoked_at),
            "view_count": link.view_count, "last_viewed_at": iso(link.last_viewed_at)}


STYLE = """body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#1f2933;line-height:1.6}
main{max-width:720px;margin:0 auto;padding:20px 16px 48px}h1{font-size:22px;margin:8px 0}h2{font-size:17px;margin:24px 0 8px}
section{background:#fff;border-radius:12px;padding:14px 16px;margin:12px 0;box-shadow:0 1px 2px rgba(0,0,0,.05)}
.muted{color:#64748b;font-size:13px}.tag{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:6px;padding:0 6px;font-size:12px;margin-right:4px}
.notice{background:#fff7ed;border:1px solid #fed7aa}.card{border-top:1px solid #e5e7eb;padding-top:8px;margin-top:8px}
dl{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;margin:0}dt{color:#64748b}dd{margin:0}"""


def render_missing():
    return (f"<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content=noindex><title>学伴报告</title><style>{STYLE}</style><main><section>"
            "<h1>链接不可用</h1><p>这个报告链接不存在、已过期或已被撤销。请联系托管班老师获取新的链接。</p></section></main></html>")


def render_report(body):
    e = lambda value: html.escape(str(value)) if value is not None else ""  # noqa: E731
    report, snap = body["report"], body["report"]["snapshot"]
    period, coverage, tasks, help_ = snap["period"], snap["coverage"], snap["school_tasks"], snap["help"]
    end_inclusive = (date.fromisoformat(period["end_exclusive"]) - timedelta(days=1)).isoformat()
    parts = [f"<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<meta name=robots content=noindex><title>学伴半月报告</title><style>{STYLE}</style><main>",
             f"<p class=muted>学伴 · 托管班半月学习报告</p><h1>{e(body['student_name'])} · {e(period['start'])} 至 {e(end_inclusive)}</h1>",
             f"<p class=muted>第 {e(report['version'])} 版 · 发布于 {e((report['published_at'] or '')[:10])}</p>"]
    if body["corrections"]:
        notes = "".join(f"<li>第 {e(c['version'])} 版（{e((c['published_at'] or '')[:10])}）：{e(c['correction_note'])}</li>"
                        for c in body["corrections"])
        parts.append(f"<section class=notice><h2>更正说明</h2><p>老师复核后更正了本期部分事实，以下为最新版本。</p><ul>{notes}</ul></section>")
    partial = f"<p class=muted>孩子于 {e(coverage['first_seen_date'])} 开始使用，本期不足完整周期，不与完整周期直接比较。</p>" if coverage["partial_period"] else ""
    parts.append(f"<section><h2>学校任务概况</h2>{partial}<dl><dt>有记录的到班日</dt><dd>{e(coverage['observed_days'])} 天</dd>"
                 f"<dt>拍照作业</dt><dd>{e(coverage['homeworks'])} 份，共 {e(tasks['questions'])} 道题</dd>"
                 f"<dt>老师已核实</dt><dd>{e(tasks['staff_verified'])} / {e(tasks['questions'])} 道</dd>"
                 f"<dt>其中答对</dt><dd>{e(tasks['verdicts']['correct'])} 道；答错 {e(tasks['verdicts']['incorrect'])} 道；部分正确 {e(tasks['verdicts']['partially_correct'])} 道</dd>"
                 f"<dt>订正完成</dt><dd>{e(tasks['corrected_resolved'])} 道；仍在订正 {e(tasks['correction_open'])} 道</dd>"
                 f"<dt>待老师核实</dt><dd>{e(tasks['awaiting_verification'] + tasks['not_judged'])} 道</dd></dl></section>")
    if snap["focus"]:
        parts.append("<h2>具体问题</h2>")
        for item in snap["focus"]:
            scope = "".join(f"<span class=tag>已核实范围</span>{e(s)} " for s in item["verified_scope"])
            causes = "".join(f"<p class=muted>可能原因（尚未核实）：{e(c)}</p>" for c in item["possible_causes"])
            cards = "".join(
                f"<div class=card><dl><dt>日期</dt><dd>{e(c['date'])}{(' · ' + e(c['homework_title'])) if c['homework_title'] else ''}"
                f"{(' · 第 ' + e(c['page']) + ' 页') if c['page'] else ''}</dd><dt>题目</dt><dd>{e(c['question'])}</dd>"
                f"<dt>帮助条件</dt><dd>{e(c['assistance'])}</dd><dt>可信程度</dt><dd>{e(c['certainty'])}</dd>"
                + (f"<dt>老师说明</dt><dd>{e(c['staff_explanation'])}</dd>" if c["staff_explanation"] else "") + "</dl></div>"
                for c in item["cards"])
            parts.append(f"<section><h2 style='margin-top:0'>{e(item['name'])}</h2><p>本期在 {e(item['denominator'])} 次已核实作答中出错 "
                         f"{e(item['errors'])} 次，答对 {e(item['counter_evidence'])} 次。期末观察：{e(item['status_label'])}。</p>"
                         f"{scope}{causes}{cards}<p><b>最小下一步：</b>{e(item['next_step'])}</p></section>")
    if snap["steady"]:
        rows = "".join(f"<li>{e(s['name'])}：答对 {e(s['correct'])} 次（独立完成 {e(s['independent_correct'])} 次）</li>" for s in snap["steady"])
        parts.append(f"<section><h2>本期做得好的</h2><ul>{rows}</ul></section>")
    if not snap["sufficient"]:
        parts.append("<section><h2>学科证据</h2><p>本期暂无足够的已核实作答来判断具体知识点情况。这不表示孩子有薄弱，也不需要为此加做测验。</p></section>")
    if help_["staff_help"] or help_["requests"]:
        rows = "".join(f"<li>{e(h['date'])}：{e(h['question'])} —— {e(h['note'])}</li>" for h in help_["staff_help"])
        parts.append(f"<section><h2>现场帮助</h2><p>本期求助 {e(help_['requests'])} 次，已解决 {e(help_['resolved'])} 次；"
                     f"查看提示 {e(help_['hints_viewed'])} 次。</p><ul>{rows}</ul></section>")
    optional = snap["sections"].get("optional_activity")
    if optional is not None:
        parts.append(f"<section><h2>额外活动（自愿）</h2><p>{e(optional.get('summary'))}</p></section>")
    if snap["unknown_current"]:
        names = "、".join(e(u["name"]) for u in snap["unknown_current"])
        parts.append(f"<section><h2>还不知道的</h2><p>近期课程中的 {names} 本期没有足够作答记录，暂不判断。</p></section>")
    if report["staff_note"] or report["next_step"]:
        parts.append(f"<section><h2>老师的话</h2><p>{e(report['staff_note'])}</p>"
                     + (f"<p><b>下一步：</b>{e(report['next_step'])}</p>" if report["next_step"] else "") + "</section>")
    parts.append(f"<p class=muted>{e(snap['limitations'])}</p></main></html>")
    return "".join(parts)
