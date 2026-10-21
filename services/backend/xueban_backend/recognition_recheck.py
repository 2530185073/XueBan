"""Bounded original-photo recognition revisions, never a new student attempt.

R1 only accepts a complete unchanged printed target. Changed stems, layouts and
split/merged units require a separate workflow. Model execution stays outside
transactions; this module owns the durable request and application boundaries.
"""
from copy import deepcopy
import hashlib

from sqlalchemy import select, update

from .correction_core import (assets_for, conflict, create_task, event, expect,
    fingerprint, get_entity, input_versions, invalidate, iso, save_entity, update_case)
from .correction_models import Entity, Task
from .errors import Problem
from .providers import question_units, reconciliation
from .providers.whole_page import canonical_label, normalize_literal_math, normalize_page_stem

KIND = "recognition_recheck"
PROTOCOL = "original-image-recheck.r1"
LIMIT = 2
ACTIVE = {"queued", "running", "retryable_failed"}
OUTCOMES = {"applied", "unchanged_pending", "target_unconfirmed", "scope_change_required",
            "service_failed", "stale_input", "execution_unknown"}


def epoch(case):
    return ((case.annotations or {}).get("recovery") or {}).get("epoch", 0)


def _bucket(attempt, assets):
    by_id = {asset.id: asset for asset in assets}
    return fingerprint({"attempt_id": attempt.id,
        "assets": sorted((identifier, by_id[identifier].document["sha256"])
                         for identifier in attempt.document["asset_ids"])})


def _rows(session, case, attempt):
    return list(session.scalars(select(Task).where(Task.homework_id == case.homework_id,
                                                  Task.kind == KIND)).all())


def active_job(session, case):
    identifier = ((case.annotations or {}).get("recovery") or {}).get("active_job_id")
    task = session.get(Task, identifier) if identifier else None
    return task if (task is not None and task.kind == KIND and task.status in ACTIVE
                    and task.inputs.get("attempt_id") == case.document["current_attempt_id"]) else None


def assert_no_active(session, case):
    if active_job(session, case):
        conflict(case.revision, "recognition_recheck_active")


def status(session, case):
    attempt = get_entity(session, case.document["current_attempt_id"], "attempt")
    assets = assets_for(session, case.homework_id, attempt.document["asset_ids"])
    bucket = _bucket(attempt, assets)
    tasks = [task for task in _rows(session, case, attempt) if task.inputs.get("budget_bucket") == bucket]
    used = sum((task.audit or {}).get("recheck_dispatched") is True for task in tasks)
    active = active_job(session, case)
    last_id = ((case.annotations or {}).get("recovery") or {}).get("last_job_id")
    last = next((task for task in tasks if task.id == last_id), None)
    outcome = (last.audit or {}).get("recovery_outcome") if last else None
    if outcome not in OUTCOMES:
        outcome = "stale_input" if last and last.status == "superseded" else "service_failed" if last and last.status == "failed" else None
    decision = case.document.get("current_decision") or {}
    question = get_entity(session, case.document["question_id"], "question")
    reason = None
    if case.document["lifecycle"] != "active":
        reason = "inactive_case"
    elif active:
        reason = "active_recovery"
    elif decision.get("origin") == "staff" and decision.get("validity") == "confirmed":
        reason = "staff_confirmed"
    elif attempt.document["recognition_status"] == "pending":
        reason = "initial_recognition_pending"
    elif len(attempt.document["asset_ids"]) != 1:
        reason = "multiple_source_pages"
    elif (attempt.document["recognition_status"] == "ready"
          and decision.get("verdict") not in (None, "unable_to_judge")
          and decision.get("validity") not in ("needs_review", "stale", "retracted")
          and not decision.get("uncertainties") and not attempt.document.get("recognition_uncertainties")
          and not question.document.get("recognition_uncertainties")):
        reason = "already_resolved"
    elif used >= LIMIT:
        reason = "budget_exhausted"
    return {"case_id": case.id, "attempt_id": attempt.id, "limit": LIMIT,
            "used": min(LIMIT, used), "remaining": max(0, LIMIT - used),
            "active_job_id": active.id if active else None,
            "last_job_id": last.id if last else None, "last_outcome": outcome,
            "eligible": reason is None, "blocked_reason": reason}


def accept(session, contracts, homework, case, payload):
    """Caller authorizes and locks the homework; CAS also fences SQLite writers."""
    attempt = get_entity(session, payload["attempt_id"], "attempt")
    if attempt.homework_id != homework.id or attempt.document["case_id"] != case.id:
        raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
    if case.document["current_attempt_id"] != attempt.id:
        conflict(case.revision)
    question = get_entity(session, case.document["question_id"], "question")
    expect(question.revision, payload["question_revision"])
    expect(attempt.document["recognition_revision"], payload["attempt_recognition_revision"])
    current = status(session, case)
    if current["active_job_id"]:
        existing = session.get(Task, current["active_job_id"])
        return {"job": existing.document, "created_resource": {"resource_type": "attempt", "resource_id": attempt.id}, "recovery": current}
    if not current["eligible"]:
        conflict(case.revision, "recognition_recheck_" + current["blocked_reason"])
    asset_ids = list(dict.fromkeys([r["asset_id"] for r in question.document["regions"]] + attempt.document["asset_ids"]))
    assets = assets_for(session, homework.id, asset_ids)
    next_epoch = epoch(case) + 1
    decision = case.document.get("current_decision") or {}
    inputs = {"case_id": case.id, "question_id": question.id, "attempt_id": attempt.id,
              "input_versions": input_versions(case, question, attempt, assets),
              "attempt_revision": attempt.revision, "asset_ids": asset_ids,
              "decision_id_at_submission": decision.get("id"), "decision_revision_at_submission": decision.get("revision"),
              "recovery_epoch": next_epoch, "budget_bucket": _bucket(attempt, assets), "protocol": PROTOCOL,
              "target_unit": question_units.stored_unit(question.annotations)}
    task, accepted = create_task(session, contracts, homework, KIND, inputs,
                                {"resource_type": "attempt", "resource_id": attempt.id})
    annotations = deepcopy(case.annotations or {})
    annotations["recovery"] = {"epoch": next_epoch, "active_job_id": task.id, "last_job_id": task.id}
    doc = {**case.document, "revision": case.revision + 1, "updated_at": iso()}
    changed = session.execute(update(Entity).where(Entity.id == case.id, Entity.revision == case.revision)
                              .values(revision=doc["revision"], document=doc, annotations=annotations)
                              .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        conflict(case.revision)
    session.refresh(case)
    save_entity(session, contracts, homework.id, "case", doc, row=case)
    return {**accepted, "recovery": status(session, case)}


def current(session, task, case, question, attempt):
    versions = task.inputs["input_versions"]
    decision = case.document.get("current_decision") or {}
    if (case.document["lifecycle"] != "active" or case.document["current_attempt_id"] != attempt.id
            or case.document["question_id"] != question.id
            or question.revision != versions["question_revision"]
            or attempt.document["recognition_revision"] != versions["attempt_recognition_revision"]
            or attempt.revision != task.inputs["attempt_revision"]
            or epoch(case) != task.inputs["recovery_epoch"]
            or ((case.annotations or {}).get("recovery") or {}).get("active_job_id") != task.id
            or decision.get("id") != task.inputs["decision_id_at_submission"]
            or decision.get("revision") != task.inputs["decision_revision_at_submission"]):
        return False
    assets = assets_for(session, task.homework_id, task.inputs["asset_ids"])
    return input_versions(case, question, attempt, assets)["fingerprint"] == versions["fingerprint"]


def target_document(question):
    document = deepcopy(question.document)
    annotations = question.annotations or {}
    document.update(target_unit=question_units.stored_unit(annotations))
    for name in ("target_context", "unit_complete", "label", "visible_locator"):
        if name in annotations:
            document[name] = deepcopy(annotations[name])
    return document


def _options(item):
    return sorted((row["label"], normalize_literal_math(row["text"])) for row in item.get("options", []))


def _target_number_conflict(original, candidate):
    """Veto explicit numbering contradictions, including legacy saved labels.

    The initial read may have missed another identical exercise, so a unique
    saved stem cannot excuse a conflicting observed question number. Missing
    or parent-only locators remain unknown/related, never positive identity.
    Do not synthesize visible locators from model labels or alter source text.
    """
    if question_units.visible_locators_conflict(original, candidate):
        return True
    old_locator = original.get("visible_locator") or {}
    new_locator = candidate.get("visible_locator") or {}
    old_numbers = (original.get("label"), old_locator.get("printed_label"))
    new_numbers = (candidate.get("label"), new_locator.get("printed_label"))
    return any(question_units.printed_label_relation(old, new) == "conflict"
               for old in old_numbers for new in new_numbers)


def matching_item(question, attempt, candidates, other_questions=()):
    """R1: no changed condition or target scope, no answer-equality requirement."""
    original = target_document(question)
    stem = normalize_page_stem(original)
    if not stem or reconciliation.has_unreadable_marker(stem):
        return None, "target_unconfirmed"
    def same_printed_target(item):
        observed = normalize_page_stem(item)
        # Identity only: a translated evaluation heading may enclose the
        # exact same complete arithmetic body. Keep both archived wordings;
        # this does not approve the new reading or verdict (ready still does).
        return observed == stem or reconciliation.instruction_presentation_same(stem, observed)
    same_print = [item for item in candidates if same_printed_target(item)
                  and _options(item) == _options(original)
                  and item.get("asset_id") in attempt.document["asset_ids"]
                  and not _target_number_conflict(original, item)]
    matches = [item for item in same_print if question_units.compatible_saved_target(
        question.document, question.annotations, item, canonical_label)]
    if same_print and not matches:
        return None, "scope_change_required"
    # Identical printed targets on the same photograph require the actual
    # existing subquestion label too; array order and equal answers do not bind.
    shared = sum(same_printed_target(target_document(row)) for row in other_questions) > 1
    if shared:
        label = canonical_label(original.get("label") or "")
        matches = [item for item in matches if label and canonical_label(item.get("label") or "") == label]
    if len(matches) != 1:
        return None, "target_unconfirmed"
    return matches[0], None


def ready(item):
    assessment = item.get("assessment") or {}
    verification = assessment.get("verification") or {}
    return (item.get("recognition_status") == "ready" and item.get("answer_status") in {"present", "blank"}
            and not item.get("uncertainties") and not assessment.get("uncertainties")
            and not reconciliation.has_unreadable_observation(item)
            and not question_units.unit_issues(item, question_units.source_unit(item))
            and assessment.get("verdict") in {"correct", "incorrect", "partially_correct", "unanswered"}
            and verification.get("mode") in {"two_model", "multimodel"}
            and verification.get("status") in {"matched", "reviewed"}
            and verification.get("independent_readback") is True and not verification.get("reason_codes")
            and (item.get("answer_status") == "blank") == (assessment.get("verdict") == "unanswered")
            and (assessment.get("verdict") not in {"incorrect", "partially_correct"}
                 or bool(assessment.get("correct_answer")) and bool(assessment.get("correction_method")))
            and ((item.get("answer_status") == "blank" and item.get("answer_text") is None)
                 or isinstance(item.get("answer_text"), str) and bool(item["answer_text"].strip())))


def record_outcome(task, value, **details):
    task.audit = {**(task.audit or {}), "recovery_outcome": value, "recovery_application": details}


def apply_result(worker, session, task, homework, result):
    case = get_entity(session, task.inputs["case_id"], "case")
    question = get_entity(session, task.inputs["question_id"], "question")
    attempt = get_entity(session, task.inputs["attempt_id"], "attempt")
    if not current(session, task, case, question, attempt):
        record_outcome(task, "stale_input")
        return None
    candidates = result.data.get("items")
    if not isinstance(candidates, list):
        from .providers import ProviderError
        raise ProviderError("invalid_output")
    others = list(session.scalars(select(Entity).where(Entity.homework_id == homework.id, Entity.kind == "question")))
    others = [row for row in others if row.document["lifecycle"] == "active"
              and {r["asset_id"] for r in row.document["regions"]} & set(attempt.document["asset_ids"])]
    item, rejection = matching_item(question, attempt, candidates, others)
    reference = {"resource_type": "attempt", "resource_id": attempt.id}
    if item is None:
        record_outcome(task, rejection)
        return reference
    if not ready(item):
        reasons = (item.get("assessment", {}).get("verification") or {}).get("reason_codes", [])
        record_outcome(task, "service_failed" if set(reasons) & {"provider_unavailable", "reconciliation_unavailable"} else "unchanged_pending")
        return reference
    assets = {asset.id: asset for asset in assets_for(session, homework.id, attempt.document["asset_ids"])}
    for region in item.get("answer_regions", []):
        asset = assets.get(region.get("asset_id"))
        if (asset is None or min(region.get("x", -1), region.get("y", -1)) < 0
                or region.get("width", 0) <= 0 or region.get("height", 0) <= 0
                or region["x"] + region["width"] > asset.document["width"]
                or region["y"] + region["height"] > asset.document["height"]):
            from .providers import ProviderError
            raise ProviderError("invalid_output")
    before = deepcopy(task.inputs["input_versions"])
    invalidate(session, worker.contracts, case, "model_recognition_revision")
    if question.document.get("recognition_uncertainties"):
        # R1 never changes printed content; clearing stale question notes is a
        # real revision and intentionally invalidates mappings of the old one.
        document = {**question.document, "revision": question.revision + 1, "recognition_uncertainties": []}
        save_entity(session, worker.contracts, homework.id, "question", document, row=question)
        for sibling in session.scalars(select(Entity).where(Entity.homework_id == homework.id, Entity.kind == "case")):
            if sibling.id != case.id and sibling.document["question_id"] == question.id and sibling.document["lifecycle"] == "active":
                invalidate(session, worker.contracts, sibling, "model_recognition_revision")
                update_case(session, worker.contracts, sibling, current_question_revision=question.revision,
                            current_decision=None, progress="paused" if sibling.document["progress"] == "paused" else "pending")
    document = {**attempt.document, "revision": attempt.revision + 1,
                "recognition_revision": attempt.document["recognition_revision"] + 1,
                "answer_text": item["answer_text"], "answer_status": item["answer_status"],
                "answer_regions": deepcopy(item.get("answer_regions", [])),
                "recognition_uncertainties": [], "recognition_status": "ready"}
    save_entity(session, worker.contracts, homework.id, "attempt", document, row=attempt)
    attempt.annotations = {**(attempt.annotations or {}), "answer_status": item["answer_status"],
                           "answer_slots": deepcopy(item.get("answer_slots", [])),
                           "slots_recognition_revision": document["recognition_revision"],
                           "slots_question_revision": question.revision, "target_binding": "original_recheck",
                           "recognition_origin": {"job_id": task.id, "protocol": PROTOCOL}}
    # No old question source label, unit, diagram or attempt metadata is copied
    # from the new model. R1 only replaces this same attempt's transcription.
    update_case(session, worker.contracts, case, current_question_revision=question.revision,
                current_decision=None)
    worker._apply_page_assessment(session, task, homework, result, item, question, attempt, case)
    from .learning_projection import project_student
    project_student(session, homework.tenant_id, homework.student_id)
    record_outcome(task, "applied", before=before,
                   after=deepcopy(case.document["current_decision"]["input_versions"]),
                   question_id=question.id, attempt_id=attempt.id)
    return reference


def lease_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()
