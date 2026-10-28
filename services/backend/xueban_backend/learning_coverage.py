"""Read-only coverage of current reliable errors, not a mastery projection.

No provider calls, jobs, mappings or evidence are created here. Reports capture
counts at generation; their saved snapshots never become live queries.
"""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from .correction_models import Entity
from .curriculum_catalog import (canonical_subject, curriculum_matches_scope, graph_is_usable,
                                 node_matches_scope, scope_payload, scopes_for_profile)
from .learning_link_scope import select_current_links
from .learning_mapping import MAPPING_VERSION, model_input_binding
from .learning_models import (KnowledgeNode, LearningOutbox, PracticeItem, PracticeResponse, PracticeSession,
                              QuestionKnowledgeLink, StudentLearningProfile, VariantGeneration)
from .learning_projection import published_graph
from .learning_rules import authoritative_decision, practice_feedback_kind
from .models import Homework
from .providers.variant_arithmetic import item_arithmetic_issues

NOTICE = ("只统计当前所选教材范围内、当前作答仍判错或部分正确的题目；其他学科和旧版教材作业不在此范围。"
          "未关联表示归类信息不足，不表示新增薄弱点，也不需要为此额外做题。")


def subject_feedback(session, student, scopes):
    """Current homework samples and separately submitted verified variants.

    Works without a graph. No mapping or node is inferred from a generated
    question, and absent help is not treated as staff-verified independence.
    The API must authorize the student before invoking this read-only helper.
    """
    result = {subject: {"correct_count": 0, "incorrect_count": 0, "partial_count": 0,
        "scorable_count": 0, "homework_feedback": {"model_count": 0, "staff_count": 0, "pending_count": 0},
        "practice_feedback": {"total": 0, "first_unassisted_count": 0, "first_unassisted_correct": 0,
            "first_unassisted_incorrect": 0, "assisted_count": 0, "retry_count": 0,
            "items": [], "truncated": False}} for subject in scopes}
    homeworks = {row.id: row for row in session.scalars(select(Homework).where(
        Homework.student_id == student.id, Homework.tenant_id == student.tenant_id,
        Homework.processing_blocked.is_(False)))}
    if not student.active or not homeworks or not scopes:
        return result
    entities = list(session.scalars(select(Entity).where(Entity.homework_id.in_(homeworks),
        Entity.kind.in_(("case", "question", "attempt", "decision")))))
    by_id = {row.id: row for row in entities}
    decisions = defaultdict(list)
    for row in entities:
        if row.kind == "decision":
            decisions[row.document.get("case_id")].append(row.document)
    scoped_cases = {}
    for case in entities:
        c = case.document
        if case.kind != "case" or c.get("lifecycle") != "active":
            continue
        question = by_id.get(c.get("question_id"))
        if (question is None or question.kind != "question" or question.homework_id != case.homework_id
                or question.document.get("lifecycle", "active") != "active"):
            continue
        q = question.document
        subject = canonical_subject((question.annotations or {}).get("subject_override") or q.get("subject"))
        if subject not in scopes or not curriculum_matches_scope(homeworks[case.homework_id].curriculum or {}, scopes[subject]):
            continue
        scoped_cases[case.id] = (case, question, subject)
        counts = result[subject]
        feedback = counts["homework_feedback"]
        attempt = by_id.get(c.get("current_attempt_id"))
        decision = authoritative_decision(attempt.id, decisions[case.id], {}) if attempt else None
        decision_row = by_id.get(decision.get("id")) if decision else None
        versions = decision.get("input_versions", {}) if decision else {}
        if (attempt is None or attempt.kind != "attempt" or attempt.homework_id != case.homework_id
                or decision is None or decision != c.get("current_decision")
                or decision_row is None or decision_row.homework_id != case.homework_id
                or decision.get("revision") != decision_row.revision
                or q.get("question_id") != question.id or q.get("revision") != question.revision
                or c.get("current_question_revision") != question.revision
                or attempt.document.get("case_id") != case.id or attempt.document.get("question_id") != question.id
                or versions.get("question_id") != question.id or versions.get("question_revision") != question.revision
                or versions.get("attempt_recognition_revision") != attempt.document.get("recognition_revision")
                or decision.get("verdict") not in {"correct", "incorrect", "partially_correct"}
                or decision["origin"] == "model" and (
                    attempt.document.get("recognition_status") != "ready" or attempt.document.get("answer_status") != "present"
                    or not (attempt.document.get("answer_text") or "").strip()
                    or attempt.document.get("recognition_uncertainties") or q.get("recognition_uncertainties"))):
            feedback["pending_count"] += 1
            continue
        counts["scorable_count"] += 1
        counts["correct_count" if decision["verdict"] == "correct" else "incorrect_count"] += 1
        counts["partial_count"] += decision["verdict"] == "partially_correct"
        feedback["model_count" if decision["origin"] == "model" else "staff_count"] += 1

    # Join all ownership boundaries, not merely a session or item id supplied
    # by a stored row. Failed generation/assessment without a response counts
    # neither as practice nor as a wrong answer.
    responses = list(session.scalars(select(PracticeResponse).where(
        PracticeResponse.student_id == student.id, PracticeResponse.tenant_id == student.tenant_id)
        .order_by(PracticeResponse.occurred_at, PracticeResponse.id)))
    activities = {row.id: row for row in session.scalars(select(PracticeSession).where(
        PracticeSession.student_id == student.id, PracticeSession.tenant_id == student.tenant_id))}
    generations = {row.id: row for row in session.scalars(select(VariantGeneration).where(
        VariantGeneration.student_id == student.id, VariantGeneration.tenant_id == student.tenant_id))}
    items = {row.id: row for row in session.scalars(select(PracticeItem).where(
        PracticeItem.owner_student_id == student.id, PracticeItem.tenant_id == student.tenant_id))}
    def is_retry(response):
        activity = activities.get(response.session_id)
        return bool(activity and (activity.gate or {}).get("retry_of_session_id"))

    # A retry can share a timestamp with its parent (e.g. imported records).
    # Preserve the initial observation instead of ordering by random UUIDs.
    responses.sort(key=lambda row: (row.occurred_at, is_retry(row), row.id))
    seen = set()
    for response in responses:
        repeated = response.item_id in seen
        seen.add(response.item_id)
        activity, item = activities.get(response.session_id), items.get(response.item_id)
        gate = (activity.gate or {}) if activity else {}
        generation = generations.get(gate.get("generation_job_id"))
        if (activity is None or item is None or generation is None
                or activity.purpose != "variant" or response.event_type != "optional_variant"
                or response.result not in {"correct", "incorrect"}
                or activity.item_ids != [item.id] or generation.item_id != item.id
                or activity.source_case_id != generation.source_case_id
                or activity.knowledge_ids != ([item.knowledge_id] if item.knowledge_id else [])
                or response.graph_version != activity.graph_version or item.graph_version != activity.graph_version
                or generation.graph_version != item.graph_version or response.knowledge_id != item.knowledge_id
                or generation.knowledge_id != item.knowledge_id
                or generation.status not in {"ready", "superseded"}
                or item.status != "ai_verified" or (item.verification or {}).get("status") != "verified"
                or (item.verification or {}).get("source") != "ai"
                or (item.generation or {}).get("job_id") != generation.id
                or (item.generation or {}).get("source_fingerprint") != generation.source_fingerprint):
            continue
        # Older rows can claim AI verification despite malformed blank
        # mappings or a numerically false reference. Use the same persisted
        # item guard as grading; never rewrite history or regrade here.
        if item_arithmetic_issues(item):
            continue
        source = scoped_cases.get(generation.source_case_id)
        if source is None:
            continue
        case, question, subject = source
        snapshot = generation.snapshot
        if (snapshot.get("student_id") != student.id or snapshot.get("tenant_id") != student.tenant_id
                or snapshot.get("source_case_id") != case.id
                or snapshot.get("homework_id") != case.homework_id or snapshot.get("question_id") != question.id
                or snapshot.get("question_revision") != question.revision
                or not curriculum_matches_scope(snapshot.get("source_curriculum") or {}, scopes[subject])):
            continue
        kind = practice_feedback_kind(hint_used=response.hint_used,
            repeated=repeated or is_retry(response))
        feedback = result[subject]["practice_feedback"]
        feedback["total"] += 1
        feedback[f"{kind}_count"] += 1
        if kind == "first_unassisted":
            feedback[f"first_unassisted_{response.result}"] += 1
        feedback["items"].append({"response_id": response.id, "session_id": activity.id, "item_id": item.id,
            "case_id": case.id, "homework_id": case.homework_id, "result": response.result,
            "kind": kind, "hint_used": response.hint_used, "occurred_at": response.occurred_at.isoformat(),
            "stem_preview": item.stem_text[:180]})
    for counts in result.values():
        feedback = counts["practice_feedback"]
        feedback["truncated"] = feedback["total"] > 20
        feedback["items"] = list(reversed(feedback["items"]))[:20]
    return result


def _moment(value):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _mapping_state(binding, subject, jobs):
    matching = [job for job in jobs if job.payload.get("protocol") == MAPPING_VERSION
                and job.payload.get("graph_version") == binding["graph_version"]
                and job.payload.get("subject") == subject
                and sum(item == binding for item in job.payload.get("inputs", [])) == 1]
    if not matching:
        return "not_linked"
    job = max(matching, key=lambda row: (row.created_at, row.id))
    if job.status in {"queued", "running"}:
        return "mapping_pending"
    outcome = job.payload.get("outcome") or {}
    rows = [row for row in outcome.get("results", []) if isinstance(row, dict)
            and row.get("question_id") == binding["question_id"]
            and row.get("input_key") == binding["input_key"]]
    if (job.status == "succeeded" and outcome.get("status") == "mapped"
            and outcome.get("protocol") == MAPPING_VERSION and outcome.get("graph_version") == binding["graph_version"]
            and len(rows) == 1 and rows[0].get("status") == "no_match"
            and rows[0].get("link_ids") == []):
        return "no_direct_match"
    return "not_linked"


def mapping_coverage(session, student, *, start=None, end=None, include_states=True, limit=30):
    """Count current attempts only; optional window uses attempt occurred_at.

    The API must authorize the student before invoking this internal helper.
    A confirmed secondary association cannot locate the cause of an error.
    """
    profile = session.get(StudentLearningProfile, student.id)
    graph = published_graph(session, student.id)
    result = {"scope": "current_reliable_errors_in_selected_graph", "time_basis": "current_attempt_occurred_at",
              "graph_version": graph.version if graph else None,
              "window": {"start": start.isoformat() if start else None, "end_exclusive": end.isoformat() if end else None},
              "available": False, "total": 0, "linked": 0, "unlinked": 0,
              "model_count": 0, "staff_count": 0, "items": [], "truncated": False, "notice": NOTICE}
    if (not student.active or profile is None or profile.tenant_id != student.tenant_id
            or not graph_is_usable(graph)):
        return result
    scopes = {scope.subject: scope for scope in scopes_for_profile(profile.document)
              if scope_payload(scope)["catalog_status"] == "available"}
    nodes = list(session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version)))
    node_ids = {subject: {node.id for node in nodes if node_matches_scope(node, scope)}
                for subject, scope in scopes.items()}
    scopes = {subject: scope for subject, scope in scopes.items() if node_ids[subject]}
    result["available"] = bool(scopes)
    homeworks = {row.id: row for row in session.scalars(select(Homework).where(
        Homework.student_id == student.id, Homework.tenant_id == student.tenant_id,
        Homework.processing_blocked.is_(False)))}
    if not scopes or not homeworks:
        return result
    entities = list(session.scalars(select(Entity).where(Entity.homework_id.in_(homeworks),
        Entity.kind.in_(("case", "question", "attempt", "decision")))))
    by_id = {row.id: row for row in entities}
    decisions = defaultdict(list)
    for row in entities:
        if row.kind == "decision":
            decisions[row.document.get("case_id")].append(row.document)
    links = defaultdict(list)
    for row in session.scalars(select(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.student_id == student.id, QuestionKnowledgeLink.tenant_id == student.tenant_id,
            QuestionKnowledgeLink.graph_version == graph.version)):
        if row.homework_id in homeworks:
            links[row.question_id].append(row)
    jobs = list(session.scalars(select(LearningOutbox).where(LearningOutbox.student_id == student.id,
        LearningOutbox.tenant_id == student.tenant_id, LearningOutbox.kind == "map_model_batch"))) if include_states else []
    missing = []
    for case in entities:
        c = case.document
        if case.kind != "case" or c.get("lifecycle") != "active":
            continue
        question, attempt = by_id.get(c.get("question_id")), by_id.get(c.get("current_attempt_id"))
        if (question is None or question.kind != "question" or attempt is None or attempt.kind != "attempt"
                or question.homework_id != case.homework_id or attempt.homework_id != case.homework_id):
            continue
        q, a = question.document, attempt.document
        subject = canonical_subject((question.annotations or {}).get("subject_override") or q.get("subject"))
        scoped = scopes.get(subject)
        if scoped is None or not curriculum_matches_scope(homeworks[case.homework_id].curriculum or {}, scoped):
            continue
        # Current coverage never resurrects a historical decision made stale by
        # new_attempt; unlike the history projection it passes no stale reasons.
        decision = authoritative_decision(attempt.id, decisions[case.id], {})
        if (decision is None or decision != c.get("current_decision")
                or decision.get("verdict") not in {"incorrect", "partially_correct"}):
            continue
        versions = decision.get("input_versions") or {}
        if (q.get("lifecycle", "active") != "active" or not q.get("stem_text") or q.get("question_id") != question.id
                or q.get("revision") != question.revision or c.get("current_question_revision") != question.revision
                or a.get("id") != attempt.id or a.get("case_id") != case.id or a.get("question_id") != question.id
                or versions.get("question_id") != question.id or versions.get("question_revision") != question.revision
                or versions.get("attempt_recognition_revision") != a.get("recognition_revision")):
            continue
        decision_row = by_id.get(decision.get("id"))
        if decision_row is None or decision_row.homework_id != case.homework_id or decision.get("revision") != decision_row.revision:
            continue
        if decision["origin"] == "model" and (a.get("recognition_status") != "ready"
                or a.get("answer_status") != "present" or not a.get("answer_text")
                or a.get("recognition_uncertainties") or q.get("recognition_uncertainties")):
            continue
        occurred = _moment(a.get("occurred_at"))
        if occurred is None or start is not None and occurred < start or end is not None and occurred >= end:
            continue
        result["total"] += 1
        result["model_count" if decision["origin"] == "model" else "staff_count"] += 1
        current = select_current_links(links[question.id], question_revision=question.revision,
            graph_version=graph.version, attempt=attempt, decision=decision_row)
        if any(link.status == "confirmed" and link.role == "primary" and link.knowledge_id in node_ids[subject]
               for link in current.values()):
            result["linked"] += 1
            continue
        item = {"case_id": case.id, "question_id": question.id, "homework_id": case.homework_id,
                "label": str((question.annotations or {}).get("label") or "")[:80], "subject": subject,
                "stem_preview": (q.get("stem_text") or "")[:180], "source_role": decision["origin"],
                "occurred_at": occurred.isoformat()}
        if include_states:
            item["state"] = (_mapping_state(model_input_binding(case, question, attempt, decision_row,
                homeworks[case.homework_id], graph, profile, scoped), subject, jobs)
                if decision["origin"] == "model" else "not_linked")
        missing.append(item)
    result["unlinked"] = result["total"] - result["linked"]
    result["items"] = sorted(missing, key=lambda row: (row["occurred_at"], row["case_id"]), reverse=True)[:limit]
    result["truncated"] = len(missing) > limit
    return result
