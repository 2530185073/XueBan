"""Private first-read persistence. No business entities, grading or paid admission."""
import base64
from copy import deepcopy
from datetime import timezone
import hashlib

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .correction_core import assets_for, iso, now
from .correction_models import Task
from .models import Homework
from .providers.read_checkpoint import PROTOCOL, ReadCheckpoint, digest
from .student_locks import lock_task_student, task_scope_current

KEY = "recognition_draft_checkpoint"


def capture(session, task, homework, images):
    assets = assets_for(session, homework.id, task.inputs["asset_ids"])
    image_hashes = {im.asset_id: hashlib.sha256(base64.b64decode(
        im.data_url.split(",", 1)[1], validate=True)).hexdigest() for im in images}
    snapshots = [{"asset_id": a.id, "revision": a.revision, "document_sha256": digest(a.document),
                  "image_sha256": a.document["sha256"]} for a in assets]
    if image_hashes != {a["asset_id"]: a["image_sha256"] for a in snapshots}:
        raise ValueError("checkpoint_asset_mismatch")
    return {"homework_id": homework.id, "student_id": homework.student_id,
            "homework_revision": homework.revision, "inputs_sha256": digest(task.inputs),
            "assets": snapshots}


def live(task):
    return (task.kind == "recognize_homework" and task.status == "running"
            and task.lease_token is not None and task.lease_expires_at is not None
            and task.lease_expires_at.replace(tzinfo=timezone.utc) > now())


def matches(session, task, homework, binding):
    if (binding.get("homework_id") != homework.id or binding.get("student_id") != homework.student_id
            or binding.get("homework_revision") != homework.revision
            or binding.get("inputs_sha256") != digest(task.inputs)):
        return False
    try:
        assets = assets_for(session, homework.id, task.inputs["asset_ids"])
    except Exception:
        return False
    return binding.get("assets") == [{"asset_id": a.id, "revision": a.revision,
        "document_sha256": digest(a.document), "image_sha256": a.document["sha256"]} for a in assets]


def persist(engine, task_id, token, binding, event=None, *, failed=False):
    # Student-first matches finish/deletion. A callback never holds inference's
    # transaction or creates a new Attempt/Decision/knowledge observation.
    with Session(engine) as session:
        scope = lock_task_student(session, task_id)
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update()
                              .execution_options(populate_existing=True))
        if task is None or not live(task) or task.lease_token != token:
            return False
        homework = session.scalar(select(Homework).where(Homework.id == task.homework_id).with_for_update()
                                  .execution_options(populate_existing=True))
        if (not task_scope_current(scope, homework) or not scope["student"].active
                or homework.processing_blocked or not matches(session, task, homework, binding)):
            return False
        if not failed:
            if type(event) is not ReadCheckpoint or event.stage not in {"primary", "secondary"}:
                return False
            asset = next((a for a in binding["assets"] if a["asset_id"] == event.asset_id), None)
            if asset is None or asset["image_sha256"] != event.image_sha256:
                return False
        ledger = deepcopy(task.audit or {})
        owner = hashlib.sha256(token.encode()).hexdigest()
        record = ledger.get(KEY)
        if not record or record.get("owner_sha256") != owner:
            record = {"protocol": PROTOCOL, "owner_sha256": owner, "binding": deepcopy(binding),
                      "observations": [], "diagnostics": []}
        if failed:
            if "checkpoint_write_failed" not in record["diagnostics"]:
                record["diagnostics"].append("checkpoint_write_failed")
        else:
            if any(r["asset_id"] == event.asset_id and r["stage"] == event.stage for r in record["observations"]):
                return False  # Immutable per execution/source; never replace.
            record["observations"].append({"asset_id": event.asset_id, "stage": event.stage,
                "source_sha256": event.source_sha256, "image_sha256": event.image_sha256,
                "recorded_at": iso(), "transcript": event.transcript()})
        ledger[KEY] = record
        revision = task.revision + 1
        changed = session.execute(update(Task).where(Task.id == task.id, Task.revision == task.revision,
            Task.lease_token == token, Task.status == "running", Task.lease_expires_at > now())
            .values(audit=ledger, revision=revision,
                    document={**task.document, "revision": revision, "updated_at": iso()})
            .execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            session.rollback()
            return False
        session.commit()
        return True


def public_progress(session, task, homework):
    out = {"protocol": PROTOCOL, "job_id": task.id, "homework_id": homework.id,
           "student_id": homework.student_id, "state": "unavailable", "pages": []}
    # A final result supersedes every draft even if its private audit is retained.
    if task.kind != "recognize_homework":
        return out
    if task.status == "succeeded":
        return {**out, "state": "final_available"}
    if not live(task):
        return out
    record = (task.audit or {}).get(KEY)
    if (not isinstance(record, dict) or record.get("protocol") != PROTOCOL
            or record.get("owner_sha256") != hashlib.sha256(task.lease_token.encode()).hexdigest()
            or not matches(session, task, homework, record.get("binding", {}))):
        return out
    pages = []
    for asset in record["binding"]["assets"]:
        observations = [r for r in record["observations"] if r["asset_id"] == asset["asset_id"]]
        if not observations:
            continue
        # First completion is a draft, not a vote. Other raw source stays private.
        pages.append({"asset_id": asset["asset_id"], "completed_reads": len(observations),
                      "draft": [{key: row[key] for key in ("label", "stem_text", "answer_text")}
                                for row in observations[0]["transcript"]]})
    return {**out, "state": "checking" if pages and len(pages) == len(record["binding"]["assets"])
            and all(p["completed_reads"] == 2 for p in pages) else "reading", "pages": pages}
