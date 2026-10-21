"""Private initial-source ledger; v1 display, optional v2 bounded dispatch/resume."""
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
        if record and record.get("protocol") == RECOVERY_PROTOCOL:
            return False  # v2 owns immutable sources and never runs the v1 owner reset.
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
    if (not isinstance(record, dict) or record.get("protocol") not in {PROTOCOL, RECOVERY_PROTOCOL}
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


# Private v2 extends the same checkpoint record; public DTO stays v1/transcript.
RECOVERY_PROTOCOL = 'recognition-draft.initial-resume.v2'
MAX_SOURCE_BYTES = 2_097_152
RESUME_INTERRUPTED_MESSAGE = '本次识读已中断，尚未形成完整结果；原图已保留。'


def _recovery_error():
    from .providers.ark import ProviderError
    return ProviderError('recognition_resume_unavailable')


def _owner(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _locked_recovery(session, task_id, token, binding):
    from .correction_models import Entity
    scope = lock_task_student(session, task_id)
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update()
                          .execution_options(populate_existing=True))
    if task is None or not live(task) or task.lease_token != token:
        raise _recovery_error()
    homework = session.scalar(select(Homework).where(Homework.id == task.homework_id).with_for_update()
                              .execution_options(populate_existing=True))
    if (not task_scope_current(scope, homework) or not scope['student'].active
            or homework.processing_blocked or not matches(session, task, homework, binding)
            or session.scalar(select(Entity.id).where(Entity.homework_id == homework.id,
                                                     Entity.kind == 'case').limit(1)) is not None):
        raise _recovery_error()
    return task, deepcopy(task.audit or {})


def _save_recovery(session, task, token, ledger):
    from sqlalchemy.exc import SQLAlchemyError
    revision = task.revision + 1
    try:
        changed = session.execute(update(Task).where(Task.id == task.id, Task.revision == task.revision,
            Task.lease_token == token, Task.status == 'running', Task.lease_expires_at > now())
            .values(audit=ledger, revision=revision,
                    document={**task.document, 'revision': revision, 'updated_at': iso()})
            .execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise _recovery_error()
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        # Lock/CAS failure never grants admission and never exposes SQL values.
        raise _recovery_error() from None


def _record(ledger, token):
    record = ledger.get(KEY)
    if (not isinstance(record, dict) or record.get('protocol') != RECOVERY_PROTOCOL
            or record.get('owner_sha256') != _owner(token)):
        raise _recovery_error()
    return record


def _completed_source(slot, contract):
    source = slot.get('source')
    if (not isinstance(source, dict) or digest(source) != slot.get('source_sha256')
            or source.get('role') != contract['role']
            or not isinstance(slot.get('receipt_id'), str)):
        raise _recovery_error()
    metadata = source.get('metadata')
    attempts = metadata.get('attempts') if isinstance(metadata, dict) else None
    if (not isinstance(source.get('data'), dict) or not isinstance(attempts, list)
            or len(attempts) != 1 or not isinstance(attempts[0], dict)
            or attempts[0].get('code') != 'ok' or attempts[0].get('status_code') != 200
            or metadata.get('requested_model') != contract['requested_model']):
        raise _recovery_error()
    return source


def first_execution(task):
    executions = (task.audit or {}).get('executions', [])
    return (task.tries == 1 and isinstance(executions, list) and len(executions) == 1
            and isinstance(executions[0], dict) and executions[0].get('outcome') == 'started'
            and not set(executions[0]) - {'number', 'started_at', 'charge_status', 'outcome'})


def initialize_recovery(engine, task_id, token, binding, contracts):
    """Initialize before any generate; old/missing history is never available."""
    with Session(engine) as session:
        task, ledger = _locked_recovery(session, task_id, token, binding)
        record = ledger.get(KEY)
        if record is None:
            if not first_execution(task):
                raise _recovery_error()
            record = {'protocol': RECOVERY_PROTOCOL, 'binding': deepcopy(binding),
                      'contracts_sha256': digest(contracts), 'owner_sha256': _owner(token),
                      'observations': [], 'diagnostics': [], 'downstream_started': False,
                      'slots': {asset: {stage: {'state': 'available', 'contract_sha256': digest(contract)}
                                       for stage, contract in stages.items()}
                                for asset, stages in contracts.items()}, 'reuse_events': []}
            ledger[KEY] = record
        elif (not isinstance(record, dict) or record.get('protocol') != RECOVERY_PROTOCOL
              or record.get('binding') != binding or record.get('contracts_sha256') != digest(contracts)
              or set(record.get('slots', {})) != set(contracts)
              or record.get('downstream_started')):
            raise _recovery_error()
        else:
            for asset, stages in contracts.items():
                saved = record['slots'][asset]
                if set(saved) != {'primary', 'secondary'}:
                    raise _recovery_error()
                for stage, contract in stages.items():
                    slot = saved[stage]
                    if slot.get('contract_sha256') != digest(contract):
                        raise _recovery_error()
                    if slot.get('state') == 'reserved':
                        # The old owner never committed a terminal response.
                        # Keep its receipt, but make unknown send/charge explicit.
                        slot.update(state='unknown', unknown_at=iso())
                        _save_recovery(session, task, token, ledger)
                        raise _recovery_error()
                    if slot.get('state') not in {'available', 'completed'}:
                        raise _recovery_error()
                    if slot['state'] == 'completed':
                        _completed_source(slot, contract)
            record['owner_sha256'] = _owner(token)  # display authorization, not a slot reset
        _save_recovery(session, task, token, ledger)


def reserve_or_reuse(engine, task_id, token, binding, asset_id, stage, contract):
    from .correction_core import new_id
    with Session(engine) as session:
        task, ledger = _locked_recovery(session, task_id, token, binding)
        record = _record(ledger, token)
        slot = record.get('slots', {}).get(asset_id, {}).get(stage)
        if (not slot or slot.get('contract_sha256') != digest(contract)
                or record.get('downstream_started') and record.get('downstream_owner_sha256') != _owner(token)):
            raise _recovery_error()
        if slot['state'] == 'completed':
            source = _completed_source(slot, contract)
            event = {'receipt_id': slot['receipt_id'], 'owner_sha256': _owner(token)}
            if event not in record['reuse_events']:
                record['reuse_events'].append(event)
            _save_recovery(session, task, token, ledger)
            return {'receipt_id': slot['receipt_id'], 'source': deepcopy(source)}
        if slot['state'] != 'available':
            raise _recovery_error()
        slot.update(state='reserved', receipt_id=new_id('initial_read'),
                    dispatched_owner_sha256=_owner(token), reserved_at=iso(), charge_status='unknown')
        _save_recovery(session, task, token, ledger)
        return {'receipt_id': slot['receipt_id'], 'source': None}


def complete_recovery(engine, task_id, token, binding, event, metadata, role):
    import json
    from dataclasses import asdict
    from .providers.ark import Metadata
    if type(event) is not ReadCheckpoint or not isinstance(metadata, Metadata):
        raise _recovery_error()
    if (len(metadata.attempts) != 1 or metadata.attempts[0].code != 'ok'
            or metadata.attempts[0].status_code != 200):
        raise _recovery_error()
    data = event.full_source()
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > MAX_SOURCE_BYTES:
        raise _recovery_error()
    with Session(engine) as session:
        task, ledger = _locked_recovery(session, task_id, token, binding)
        record = _record(ledger, token)
        slot = record.get('slots', {}).get(event.asset_id, {}).get(event.stage)
        asset = next((a for a in binding['assets'] if a['asset_id'] == event.asset_id), None)
        if (not asset or asset['image_sha256'] != event.image_sha256 or not slot
                or slot.get('state') != 'reserved' or slot.get('dispatched_owner_sha256') != _owner(token)):
            raise _recovery_error()
        source = {'role': role, 'data': data, 'metadata': asdict(metadata)}
        slot.update(state='completed', source=source, source_sha256=digest(source), completed_at=iso())
        record['observations'].append({'asset_id': event.asset_id, 'stage': event.stage,
            'source_sha256': event.source_sha256, 'image_sha256': event.image_sha256,
            'recorded_at': iso(), 'transcript': event.transcript()})
        _save_recovery(session, task, token, ledger)


def fail_recovery(engine, task_id, token, binding, asset_id, stage, metadata):
    from dataclasses import asdict
    from .providers.ark import Metadata
    with Session(engine) as session:
        task, ledger = _locked_recovery(session, task_id, token, binding)
        record = _record(ledger, token)
        slot = record.get('slots', {}).get(asset_id, {}).get(stage)
        if (not slot or slot.get('state') != 'reserved'
                or slot.get('dispatched_owner_sha256') != _owner(token)):
            return False
        slot.update(state='failed', finished_at=iso(),
                    metadata=asdict(metadata) if isinstance(metadata, Metadata) else None)
        _save_recovery(session, task, token, ledger)
        return True


def mark_downstream(engine, task_id, token, binding):
    with Session(engine) as session:
        task, ledger = _locked_recovery(session, task_id, token, binding)
        record = _record(ledger, token)
        if record.get('downstream_started'):
            if record.get('downstream_owner_sha256') != _owner(token):
                raise _recovery_error()
            return
        record.update(downstream_started=True, downstream_owner_sha256=_owner(token), downstream_started_at=iso())
        _save_recovery(session, task, token, ledger)
