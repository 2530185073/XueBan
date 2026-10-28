"""Read-only, per-original-case notebook, independent of curriculum projections.

Opening or generating a variant is not a submitted practice attempt. ``practiced``
means an answer/assessment has been persisted, including an unsuccessful check.
Practice never changes the original verdict or removes a notebook entry.
"""
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Actor
from .correction_models import Entity, CorrectionEvent as Event
from .correction_history import prior_attempts
from .learning_models import LearningOutbox, PracticeAssessment, PracticeResponse, PracticeSession, VariantGeneration, PracticeItem
from .models import Homework
from .providers.variant_arithmetic import item_arithmetic_issues


def _utc(value):
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


def _occurred_at(attempt, homework):
    value = attempt.document.get("occurred_at") if attempt else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return _utc(parsed)
        except ValueError:
            pass
    return _utc(homework.occurred_at)


def _result_status(result):
    if result == "correct":
        return "correct"
    if result in {"incorrect", "partially_correct"}:
        return "needs_practice"
    if result == "pending":
        return "submitted"
    return "check_failed"


def _generation_status(generation, jobs):
    if not generation:
        return None
    status = generation.status
    job = jobs.get(generation.outbox_id)
    # Worker stages may remain uncommitted until completion. The queue's
    # persisted lease/dead state keeps closed dialogs and fresh page loads honest.
    if status in {"queued", "generating", "verifying"} and job:
        if job.status == "dead":
            return "failed"
        if status == "queued" and job.status == "running":
            return "generating"
    return status


def _session_status(activity, responses, assessments, jobs):
    """Use saved outcomes before the coarse active/completed session flag."""
    latest = {}
    for assessment in assessments:
        status = assessment.status
        job = jobs.get(assessment.outbox_id)
        if status in {"queued", "grading"} and job and job.status == "dead":
            status = "failed"
        outcome = ("submitted" if status in {"queued", "grading"} else
                   _result_status(assessment.result) if status == "completed" else "check_failed")
        # Submission time determines which attempt is newer. A delayed update
        # to an old failed assessment must not replace a subsequent response.
        key = (_utc(assessment.created_at), 0, assessment.id)
        if assessment.item_id not in latest or key > latest[assessment.item_id][0]:
            latest[assessment.item_id] = (key, outcome)
    for response in responses:
        key = (_utc(response.occurred_at), 1, response.id)
        if response.item_id not in latest or key > latest[response.item_id][0]:
            latest[response.item_id] = (key, _result_status(response.result))
    outcomes = {entry[1] for entry in latest.values()}
    for status in ("submitted", "check_failed", "needs_practice"):
        if status in outcomes:
            return status
    if activity.item_ids and len(latest) == len(set(activity.item_ids)) and outcomes == {"correct"}:
        return "correct"
    return "uncorrected"


def notebook_document(session, student):
    """Load owned rows in batches; never apply a graph gate or a top-N limit."""
    homework_query = select(Homework).where(
        Homework.student_id == student.id, Homework.tenant_id == student.tenant_id,
        Homework.processing_blocked.is_(False))
    homeworks = {row.id: row for row in session.scalars(homework_query)}
    entities = [row for row in session.scalars(select(Entity).where(
        Entity.homework_id.in_(homework_query.with_only_columns(Homework.id)),
        Entity.kind.in_(("case", "question", "attempt", "decision")))) if row.homework_id in homeworks]
    by_id = {(row.homework_id, row.kind, row.id): row for row in entities}
    attempts = {row.id: row.document for row in entities if row.kind == "attempt"}
    decisions = [row.document for row in entities if row.kind == "decision"]
    stale_reasons = {row.id: row.payload.get("reason") for row in session.scalars(select(Event).where(
        Event.homework_id.in_(homeworks), Event.kind == "evidence.invalidated"))}
    cases = []
    for case in entities:
        if case.kind != "case" or case.document.get("lifecycle", "active") != "active":
            continue
        decision = case.document.get("current_decision") or {}
        # Match the student's visible wrong verdict, including starred model
        # judgments. Collecting practice is not promoting mastery evidence.
        attempt = by_id.get((case.homework_id, "attempt", case.document.get("current_attempt_id")))
        original_status = None
        if (decision.get("verdict") not in {"incorrect", "partially_correct"}
                or decision.get("validity") not in {"candidate", "confirmed"}):
            # A submitted correction clears the current judgment. Keep its
            # original wrong card until staff confirms the new answer; this
            # must not alter the independent variant-practice progress.
            if decision.get("origin") == "staff" and decision.get("validity") == "confirmed" and decision.get("verdict") == "correct":
                continue
            history = prior_attempts({**case.document, "id": case.id}, attempts, decisions, stale_reasons)
            previous = next((prior for prior, judgment in history if judgment
                             and judgment.get("verdict") in {"incorrect", "partially_correct"}
                             and prior.get("answer_status") != "blank"), None)
            if previous is None:
                continue
            attempt = by_id.get((case.homework_id, "attempt", previous["id"]))
            original_status = ("awaiting_review" if decision.get("verdict") == "correct"
                               and decision.get("origin") == "model" else "rechecking")
        # Blank answers are explicitly unanswered; unknown/missing answer text
        # must not erase a real saved incorrect/partial decision.
        if attempt and attempt.document.get("answer_status") == "blank":
            continue
        question_id = case.document.get("question_id")
        if not isinstance(question_id, str):
            continue
        question = by_id.get((case.homework_id, "question", question_id))
        cases.append((case, question, _occurred_at(attempt, homeworks[case.homework_id]), original_status))
    if not cases:
        return {"student_id": student.id, "items": []}

    generations = {}
    for row in session.scalars(select(VariantGeneration).where(
            VariantGeneration.student_id == student.id, VariantGeneration.tenant_id == student.tenant_id)
            .order_by(VariantGeneration.created_at.desc(), VariantGeneration.id.desc())):
        generations.setdefault(row.source_case_id, row)
    generation_jobs = {row.id: row for row in session.scalars(select(LearningOutbox).where(
        LearningOutbox.tenant_id == student.tenant_id, LearningOutbox.student_id == student.id,
        LearningOutbox.id.in_([row.outbox_id for row in generations.values() if row.outbox_id])))}
    session_query = select(PracticeSession).where(
        PracticeSession.student_id == student.id, PracticeSession.tenant_id == student.tenant_id,
        PracticeSession.purpose == "variant")
    activities = list(session.scalars(session_query.order_by(PracticeSession.started_at.desc(), PracticeSession.id.desc())))
    item_ids = {item_id for activity in activities for item_id in activity.item_ids}
    item_ids.update(row.item_id for row in generations.values() if row.item_id)
    invalid_items = {item.id for item in session.scalars(select(PracticeItem).where(
        PracticeItem.tenant_id == student.tenant_id, PracticeItem.id.in_(item_ids)))
        if item_arithmetic_issues(item)}
    by_session = {row.id: row for row in activities}
    by_case = defaultdict(list)
    for row in activities:
        by_case[row.source_case_id].append(row)

    responses, assessments = defaultdict(list), defaultdict(list)
    for model, destination in ((PracticeResponse, responses), (PracticeAssessment, assessments)):
        for row in session.scalars(select(model).where(
                model.tenant_id == student.tenant_id, model.student_id == student.id,
                model.session_id.in_(session_query.with_only_columns(PracticeSession.id)))):
            # READ COMMITTED can see a just-created session in the subquery.
            # Include its progress on the next read, with its parent row.
            parent = by_session.get(row.session_id)
            if parent and row.item_id in parent.item_ids:
                destination[row.session_id].append(row)
    jobs = {row.id: row for row in session.scalars(select(LearningOutbox).where(
        LearningOutbox.tenant_id == student.tenant_id, LearningOutbox.student_id == student.id,
        LearningOutbox.id.in_(select(PracticeAssessment.outbox_id).where(
            PracticeAssessment.tenant_id == student.tenant_id, PracticeAssessment.student_id == student.id,
            PracticeAssessment.session_id.in_(session_query.with_only_columns(PracticeSession.id))))))}

    items = []
    for case, question, occurred_at, original_status in sorted(cases, key=lambda entry: (entry[2], entry[0].id), reverse=True):
        generation = generations.get(case.id)
        history = by_case[case.id]
        practiced = any(responses[row.id] or assessments[row.id] for row in history)
        if generation:
            # Retries may create another session for the same generation. Scope
            # every pointer to this student AND case, never trust a stored ID.
            history = [row for row in history if (
                (row.gate or {}).get("generation_job_id") == generation.id
                or (row.id == generation.session_id
                    and (row.gate or {}).get("generation_job_id") in (None, generation.id)))]
            # The retry transaction advances this pointer. It is authoritative
            # even when two sessions share a timestamp (IDs are random).
            current = next((row for row in history if row.id == generation.session_id), None)
            if current:
                history = [current, *[row for row in history if row.id != current.id]]
        activity = history[0] if history else None
        status = (_session_status(activity, responses[activity.id], assessments[activity.id], jobs)
                  if activity else "uncorrected")
        if (activity and activity.status == "active" and status == "uncorrected"
                and not responses[activity.id] and not assessments[activity.id]):
            # A freshly reopened wrong-answer retry keeps the last result
            # visible until a new submission has its own persisted progress.
            for previous in history[1:]:
                previous_status = _session_status(previous, responses[previous.id], assessments[previous.id], jobs)
                if previous_status != "uncorrected":
                    if previous_status == "needs_practice":
                        status = previous_status
                    break
        invalid_generation = bool(generation and generation.item_id in invalid_items)
        if activity and any(item_id in invalid_items for item_id in activity.item_ids):
            status = "check_failed"
        items.append({
            "case_id": case.id, "question_id": case.document["question_id"],
            "question_text": question.document.get("stem_text") if question else None,
            "homework_title": homeworks[case.homework_id].title,
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
            "status": status, "practiced": practiced,
            "generation_id": generation.id if generation else None,
            "generation_status": "superseded" if invalid_generation else _generation_status(generation, generation_jobs),
            "session_id": activity.id if activity else None,
            **({"original_status": original_status} if original_status else {}),
        })
    return {"student_id": student.id, "items": items}


def install_variant_notebook(app, database_session, current_actor):
    from .app import require_student

    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]

    @app.get("/v1/students/{student_id}/variant-notebook", operation_id="getVariantNotebook")
    def get_notebook(student_id: str, actor: ActorDep, session: SessionDep):
        student = require_student(session, actor, student_id)
        return notebook_document(session, student)
