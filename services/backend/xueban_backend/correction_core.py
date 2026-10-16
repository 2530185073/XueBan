"""Transaction helpers shared by HTTP commands and the durable worker."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .correction_models import Asset, CorrectionEvent, Entity, EntitySnapshot, Task
from .errors import Problem
from .models import Homework, IdempotencyRecord

ENTITY_SCHEMAS = {"question": "CapturedQuestionRevision", "attempt": "Attempt", "case": "CorrectionCase", "check": "CheckRun", "decision": "Decision", "hint": "Hint", "review": "Review", "help": "HelpRequest"}


def now():
    return datetime.now(timezone.utc)


def iso(value=None):
    return (value or now()).replace(tzinfo=(value.tzinfo if value else timezone.utc) or timezone.utc).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(kind):
    return kind + "_" + uuid4().hex


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def conflict(revision=None, code="revision_conflict"):
    raise Problem(409, "conflict", code, "内容已变化或操作不再适用，请读取最新记录后重试。", revision)


def expect(actual, expected):
    if actual != expected:
        conflict(actual)


def event(session, homework_id, aggregate_id, revision, kind, payload=None):
    identifier = new_id("event")
    session.add(CorrectionEvent(id=identifier, homework_id=homework_id, aggregate_id=aggregate_id, aggregate_revision=revision, kind=kind, payload=payload or {}, created_at=now()))
    return identifier


def save_entity(session, contracts, homework_id, kind, document, *, row=None):
    document = deepcopy(document)
    contracts.validate(ENTITY_SCHEMAS[kind], document)
    identifier = document.get("id") or document["question_id"]
    revision = document.get("revision", 1)
    if row is None:
        row = Entity(id=identifier, homework_id=homework_id, kind=kind, revision=revision, document=document)
        session.add(row)
        session.flush()
    else:
        row.document = document
        row.revision = revision
    session.add(EntitySnapshot(entity_id=row.id, revision=revision, document=document))
    event(session, homework_id, identifier, revision, kind + ".saved")
    return row


def update_case(session, contracts, row, **changes):
    doc = deepcopy(row.document)
    doc.update(changes)
    doc["revision"] += 1
    doc["updated_at"] = iso()
    save_entity(session, contracts, row.homework_id, "case", doc, row=row)
    return doc


def get_entity(session, identifier, kind):
    row = session.get(Entity, identifier)
    if row is None or row.kind != kind:
        raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
    return row


def reserve_idempotency(session, actor, student_id, operation, key, payload):
    digest = fingerprint(payload)
    record = IdempotencyRecord(id=new_id("idempotency"), principal_id=actor.id, student_id=student_id, operation=operation, key=key, payload_hash=digest, created_at=now())
    try:
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:
        record = session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.principal_id == actor.id, IdempotencyRecord.student_id == student_id, IdempotencyRecord.operation == operation, IdempotencyRecord.key == key))
        if record is None or record.response_json is None:
            raise Problem(503, "service", "service_unavailable", "提交尚未完成，请使用原幂等键重试。") from None
        if record.payload_hash != digest:
            conflict(code="idempotency_conflict")
        return record, deepcopy(record.response_json)
    return record, None


def replay_idempotent(session, actor, student_id, operation, key, payload):
    """Optional-key variant for later-phase creates: replay a finished request before any validation runs."""
    if key is None:
        return None
    record = session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.principal_id == actor.id, IdempotencyRecord.student_id == student_id, IdempotencyRecord.operation == operation, IdempotencyRecord.key == key))
    if record is None:
        return None
    if record.payload_hash != fingerprint(payload):
        conflict(code="idempotency_conflict")
    return deepcopy(record.response_json)


def remember_idempotent(session, actor, student_id, operation, key, payload, result):
    """Store the response under the key just before commit; a concurrent twin wins and this write is discarded."""
    if key is None:
        return result
    record, replay = reserve_idempotency(session, actor, student_id, operation, key, payload)
    if replay is not None:
        session.rollback()
        return replay
    record.response_json = result
    return result


def create_task(session, contracts, homework, kind, inputs, resource=None):
    identifier = new_id("job")
    timestamp = iso()
    doc = {"id": identifier, "revision": 1, "kind": kind, "status": "queued", "created_at": timestamp, "updated_at": timestamp, "retry_after_seconds": 1, "failure": None, "result": None}
    contracts.validate("Job", doc)
    task = Task(id=identifier, homework_id=homework.id, revision=1, kind=kind, status="queued", document=doc, inputs=deepcopy(inputs), tries=0, available_at=now())
    session.add(task)
    event(session, homework.id, identifier, 1, "job.queued")
    return task, {"job": doc, "created_resource": resource}


def set_task(session, contracts, task, status, *, result=None, failure=None):
    task.revision += 1
    task.status = status
    task.document = {**task.document, "revision": task.revision, "status": status, "updated_at": iso(), "failure": failure, "result": result, "retry_after_seconds": 2 if status in {"queued", "running", "retryable_failed"} else None}
    contracts.validate("Job", task.document)
    event(session, task.homework_id, task.id, task.revision, "job." + status)


def input_versions(case, question, attempt, assets):
    # Case revision is an audit/CAS value, never a content-invalidity predicate.
    content = {"question_id": question.id, "question_revision": question.revision, "attempt_id": attempt.id, "attempt_recognition_revision": attempt.document["recognition_revision"], "assets": sorted((a.id, a.document["sha256"]) for a in assets)}
    return {key: content[key] for key in ("question_id", "question_revision", "attempt_id", "attempt_recognition_revision")} | {"case_revision_at_submission": case.revision, "fingerprint": fingerprint(content)}


def assets_for(session, homework_id, identifiers):
    assets = [session.get(Asset, identifier) for identifier in identifiers]
    if any(a is None or a.homework_id != homework_id or a.document["status"] != "uploaded" for a in assets):
        raise Problem(422, "validation", "asset_unavailable", "必须使用当前作业已完整上传的图片。")
    if len(assets) > 4 or sum(a.document["byte_size"] for a in assets) > 8 * 1024 * 1024:
        raise Problem(422, "validation", "image_batch_too_large", "一次最多提交 4 张图片，总计不超过 8 MiB。")
    return assets


def invalidate(session, contracts, case, reason):
    """Retain prior evidence, but withdraw it from current guidance/closure."""
    event_id = event(session, case.homework_id, case.id, case.revision + 1, "evidence.invalidated", {"reason": reason})
    previous = case.document.get("current_decision")
    if previous:
        decision = get_entity(session, previous["id"], "decision")
        document = deepcopy(decision.document)
        document.update(revision=decision.revision + 1, validity="stale", invalidated_by_event_id=event_id)
        save_entity(session, contracts, case.homework_id, "decision", document, row=decision)
    for hint_id in case.document["hint_ids"]:
        hint = get_entity(session, hint_id, "hint")
        if hint.document["validity"] == "active":
            document = {**hint.document, "revision": hint.revision + 1, "validity": "stale"}
            save_entity(session, contracts, case.homework_id, "hint", document, row=hint)
    return None
