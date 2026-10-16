"""Projector and durable job runner for the per-student learning layer.

Correction code knows nothing about this module. A cheap per-student
fingerprint of correction events and learning inputs decides who must be
re-projected; a projection always rebuilds from full current records, so a
missed or repeated trigger only costs time, never correctness.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .correction_models import CorrectionEvent, Entity
from .governance import register_downstream_purger
from .learning_models import (AttemptCondition, CurriculumGraph, KnowledgeDiagnosis, KnowledgeNode, LearningEvidence,
                              LearningOutbox, LearningProjection, QuestionKnowledgeLink, StudentKnowledgeState,
                              StudentLearningProfile)
from .learning_rules import RULE_VERSION, compute_state, derive_evidence
from .models import Homework, Student

log = logging.getLogger(__name__)
LEASE = timedelta(minutes=5)
MAX_TRIES = 5
EVIDENCE_LISTENERS = []
JOB_HANDLERS = {}
PROJECTION_FOLLOWUPS = []


def now():
    return datetime.now(timezone.utc)


def new_id(prefix):
    return f"{prefix}_{uuid4().hex}"


def register_evidence_listener(function):
    """Called in the projection transaction with (session, tenant_id, student_id, invalidated, changed_ids)."""
    EVIDENCE_LISTENERS.append(function)
    return function


def register_job_handler(kind):
    def decorator(function):
        JOB_HANDLERS[kind] = function
        return function
    return decorator


def register_projection_followup(function):
    """Called after a projection commits, to enqueue dependent jobs."""
    PROJECTION_FOLLOWUPS.append(function)
    return function


def published_graph(session) -> CurriculumGraph:
    return session.scalar(select(CurriculumGraph).where(CurriculumGraph.status == "published")
                          .order_by(CurriculumGraph.published_at.desc()))


def enqueue(session, tenant_id, student_id, kind, payload, dedupe_key, available_at=None):
    pending = session.scalar(select(LearningOutbox.id).where(LearningOutbox.dedupe_key == dedupe_key,
                                                             LearningOutbox.status.in_(("queued", "running"))))
    if pending:
        return pending
    row = LearningOutbox(id=new_id("ljob"), tenant_id=tenant_id, student_id=student_id, kind=kind, dedupe_key=dedupe_key,
                         payload=payload, status="queued", tries=0, available_at=available_at or now(), created_at=now())
    session.add(row)
    return row.id


def _digest(parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def fingerprints(session, student_ids=None) -> dict[str, str]:
    """One grouped query per input table, not one query per learner."""
    graph = published_graph(session)
    parts = defaultdict(list)
    events = (select(Homework.student_id, func.count(CorrectionEvent.id), func.max(CorrectionEvent.created_at))
              .join(Homework, Homework.id == CorrectionEvent.homework_id).where(Homework.processing_blocked.is_(False))
              .group_by(Homework.student_id))
    if student_ids is not None:
        events = events.where(Homework.student_id.in_(student_ids))
    for student_id, count, latest in session.execute(events):
        parts[student_id].append(f"e{count}:{latest}")
    for model in (QuestionKnowledgeLink, AttemptCondition, KnowledgeDiagnosis):
        query = select(model.student_id, func.count(model.id), func.max(model.created_at)).group_by(model.student_id)
        if student_ids is not None:
            query = query.where(model.student_id.in_(student_ids))
        for student_id, count, latest in session.execute(query):
            parts[student_id].append(f"{model.__tablename__}{count}:{latest}")
    return {student_id: _digest([graph.version if graph else None, RULE_VERSION, *sorted(values)])
            for student_id, values in parts.items()}


def dirty_students(session) -> list[tuple[str, str]]:
    current = fingerprints(session)
    stored = {row[0]: row[1] for row in session.execute(select(LearningProjection.student_id, LearningProjection.source_fingerprint))}
    active = {row[0]: row[1] for row in session.execute(select(Student.id, Student.tenant_id).where(Student.active.is_(True)))}
    return [(active[s], s) for s, digest in sorted(current.items()) if s in active and stored.get(s) != digest]


def _records(session, student_id):
    homeworks = select(Homework.id).where(Homework.student_id == student_id, Homework.processing_blocked.is_(False))
    rows = defaultdict(list)
    for entity in session.scalars(select(Entity).where(Entity.homework_id.in_(homeworks),
                                                       Entity.kind.in_(("case", "attempt", "decision", "hint", "question")))):
        rows[entity.kind].append(entity.document)
    stale = {e.id: (e.payload or {}).get("reason") for e in session.scalars(
        select(CorrectionEvent).where(CorrectionEvent.homework_id.in_(homeworks), CorrectionEvent.kind == "evidence.invalidated"))}
    links = [{"id": l.id, "question_id": l.question_id, "knowledge_id": l.knowledge_id, "role": l.role, "status": l.status,
              "created_at": l.created_at.isoformat()} for l in session.scalars(
        select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.student_id == student_id))]
    conditions = {}
    for row in session.scalars(select(AttemptCondition).where(AttemptCondition.student_id == student_id)
                               .order_by(AttemptCondition.created_at, AttemptCondition.id)):
        conditions[row.attempt_id] = row.assistance_level
    return rows, stale, links, conditions


def project_student(session: Session, tenant_id: str, student_id: str) -> dict:
    """Rebuild one learner's evidence and state inside the caller's transaction."""
    student = session.get(Student, student_id)
    if student is None or student.tenant_id != tenant_id:
        return {"status": "skipped"}
    graph = published_graph(session)
    if graph is None:
        return {"status": "no_published_graph"}
    session.scalar(select(Student).where(Student.id == student_id).with_for_update())
    digest = fingerprints(session, [student_id]).get(student_id, _digest([graph.version if graph else None, RULE_VERSION]))
    projection = session.get(LearningProjection, student_id)
    if projection is None:
        projection = LearningProjection(student_id=student_id, tenant_id=tenant_id, source_fingerprint="", evidence_revision=0,
                                        state_version=0, projected_at=now())
        session.add(projection)
    rows, stale, links, conditions = _records(session, student_id)
    known = set(session.scalars(select(KnowledgeNode.id).where(KnowledgeNode.graph_version == graph.version)))
    derived = [d for d in derive_evidence(cases=rows["case"], attempts=rows["attempt"], decisions=rows["decision"],
                                          hints=rows["hint"], questions={q["question_id"]: q for q in rows["question"]},
                                          links=links, conditions=conditions, stale_reasons=stale)
               if d["knowledge_id"] in known]
    existing = {row.source_key: row for row in session.scalars(
        select(LearningEvidence).where(LearningEvidence.student_id == student_id, LearningEvidence.validity == "valid"))}
    wanted = {d["source_key"]: d for d in derived}
    timestamp = now()
    invalidated, changed = [], set()
    replaced = {(d["attempt_id"], d["knowledge_id"]) for key, d in wanted.items() if key not in existing}
    for key, row in existing.items():
        if key not in wanted:
            row.validity, row.invalidated_at = "invalidated", timestamp
            row.invalidation_reason = "superseded" if (row.document["attempt_id"], row.knowledge_id) in replaced else "withdrawn"
            invalidated.append(row)
            changed.add(row.knowledge_id)
    for key, document in wanted.items():
        if key in existing:
            continue
        session.add(LearningEvidence(id=new_id("evidence"), tenant_id=tenant_id, campus_id=student.campus_id, student_id=student_id,
                                     knowledge_id=document["knowledge_id"], source_key=key, document=document,
                                     occurred_at=datetime.fromisoformat(document["occurred_at"].replace("Z", "+00:00")),
                                     recorded_at=timestamp, validity="valid"))
        changed.add(document["knowledge_id"])
    session.flush()
    if changed:
        projection.evidence_revision += 1
    valid = defaultdict(list)
    for row in session.scalars(select(LearningEvidence).where(LearningEvidence.student_id == student_id,
                                                              LearningEvidence.validity == "valid")):
        valid[row.knowledge_id].append({**row.document, "id": row.id})
    diagnoses = defaultdict(list)
    for row in session.scalars(select(KnowledgeDiagnosis).where(KnowledgeDiagnosis.student_id == student_id,
                                                                KnowledgeDiagnosis.status == "active")):
        diagnoses[row.knowledge_id].append({"id": row.id, "kind": row.kind, "scope_text": row.scope_text, "evidence_ids": row.evidence_ids})
    states = {row.knowledge_id: row for row in session.scalars(
        select(StudentKnowledgeState).where(StudentKnowledgeState.student_id == student_id))}
    targets = set(valid) | set(diagnoses) | set(states)
    computed = {k: compute_state(k, valid.get(k, []), diagnoses.get(k, []), timestamp) for k in targets}
    state_changed = any(states.get(k) is None or states[k].document != doc or states[k].rule_version != RULE_VERSION
                        for k, doc in computed.items())
    if state_changed:
        projection.state_version += 1
        for knowledge_id, document in computed.items():
            row = states.get(knowledge_id)
            if row is None:
                session.add(StudentKnowledgeState(student_id=student_id, knowledge_id=knowledge_id, tenant_id=tenant_id,
                                                  graph_version=graph.version, rule_version=RULE_VERSION,
                                                  state_version=projection.state_version, document=document, computed_at=timestamp))
            elif row.document != document or row.rule_version != RULE_VERSION:
                row.document, row.rule_version, row.graph_version = document, RULE_VERSION, graph.version
                row.state_version, row.computed_at = projection.state_version, timestamp
    projection.source_fingerprint, projection.projected_at = digest, timestamp
    if invalidated or changed:
        for listener in EVIDENCE_LISTENERS:
            listener(session, tenant_id, student_id, invalidated, changed)
    return {"status": "projected", "evidence_revision": projection.evidence_revision, "state_version": projection.state_version,
            "added": len([k for k in wanted if k not in existing]), "invalidated": len(invalidated)}


@register_job_handler("project_student")
def _project_job(session, job, context):
    return project_student(session, job.tenant_id, job.student_id)


class LearningWorker:
    """Shares the correction worker process; every job runs in its own transaction."""

    def __init__(self, engine, *, client=None, scan_interval=timedelta(seconds=10)):
        self.engine, self.client, self.scan_interval = engine, client, scan_interval
        self.next_scan = datetime.min.replace(tzinfo=timezone.utc)

    def scan(self, force=False):
        if not force and now() < self.next_scan:
            return 0
        self.next_scan = now() + self.scan_interval
        with Session(self.engine) as session, session.begin():
            dirty = dirty_students(session)
            for tenant_id, student_id in dirty:
                enqueue(session, tenant_id, student_id, "project_student", {}, f"project:{student_id}")
        return len(dirty)

    def claim(self):
        with Session(self.engine) as session, session.begin():
            query = (select(LearningOutbox).where(
                ((LearningOutbox.status == "queued") & (LearningOutbox.available_at <= now()))
                | ((LearningOutbox.status == "running") & (LearningOutbox.lease_expires_at < now())))
                .order_by(LearningOutbox.available_at).limit(1))
            if session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            job = session.scalar(query)
            if job is None:
                return None
            job.status, job.tries = "running", job.tries + 1
            job.lease_token, job.lease_expires_at = uuid4().hex, now() + LEASE
            return job.id, job.lease_token

    def run_once(self) -> bool:
        self.scan()
        claimed = self.claim()
        if claimed is None:
            return False
        job_id, token = claimed
        try:
            with Session(self.engine) as session, session.begin():
                job = session.get(LearningOutbox, job_id)
                if job.lease_token != token:
                    return True
                kind, tenant_id, student_id = job.kind, job.tenant_id, job.student_id
                handler = JOB_HANDLERS.get(kind)
                if handler is None:
                    job.status, job.last_error = "dead", "unknown_kind"
                    return True
                handler(session, job, self)
                job.status, job.lease_token, job.lease_expires_at = "succeeded", None, None
            if kind == "project_student":
                with Session(self.engine) as session, session.begin():
                    for followup in PROJECTION_FOLLOWUPS:
                        followup(session, tenant_id, student_id, self)
        except Exception as error:  # noqa: BLE001 - recorded as a fixed code, never a message
            code = getattr(error, "code", type(error).__name__)[:64]
            log.warning("learning job %s failed: %s", job_id, code)
            with Session(self.engine) as session, session.begin():
                job = session.get(LearningOutbox, job_id)
                if job is not None and job.lease_token == token:
                    job.last_error, job.lease_token, job.lease_expires_at = code, None, None
                    job.status = "dead" if job.tries >= MAX_TRIES else "queued"
                    job.available_at = now() + timedelta(seconds=min(300, 5 * 2 ** job.tries))
        return True

    def drain(self, limit=100):
        """Test and CLI helper: scan once, then run until no job is ready."""
        self.scan(force=True)
        count = 0
        while count < limit and self.run_once():
            count += 1
        return count


@register_downstream_purger
def purge_learning(session, tenant_id, student_id):
    for model in (LearningOutbox, LearningProjection, StudentKnowledgeState, LearningEvidence, KnowledgeDiagnosis,
                  AttemptCondition, QuestionKnowledgeLink, StudentLearningProfile):
        session.execute(delete(model).where(model.student_id == student_id, model.tenant_id == tenant_id))
