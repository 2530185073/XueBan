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
from .learning_models import KnowledgeNode, LearningOutbox, QuestionKnowledgeLink, StudentLearningProfile
from .learning_projection import published_graph
from .learning_rules import authoritative_decision
from .models import Homework

NOTICE = ("只统计当前所选教材范围内、当前作答仍判错或部分正确的题目；其他学科和旧版教材作业不在此范围。"
          "未关联表示归类信息不足，不表示新增薄弱点，也不需要为此额外做题。")


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
