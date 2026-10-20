"""Help history is audit data, not a changed photographed answer."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import candidate, verification, Client, source, queue, run, get, start
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import LearningOutbox, VariantGeneration
from xueban_backend.practice_generation import _digest, generate_student_variant, snapshot_current


def hint_and_view(flow, case):
    current = flow.client.get(f"/v1/cases/{case['id']}").json()
    decision = current["current_decision"]
    accepted = flow.post(f"/v1/cases/{case['id']}/hints", {"expected_revision": current["revision"],
        "attempt_id": current["current_attempt_id"], "source_decision_id": decision["id"],
        "source_decision_revision": decision["revision"], "level": "direction"})
    assert accepted.status_code == 202, accepted.text
    assert flow.worker.run_once()
    job = flow.client.get(f"/v1/jobs/{accepted.json()['job']['id']}").json()
    hint = flow.client.get(f"/v1/hints/{job['result']['resource_id']}").json()
    viewed = flow.post(f"/v1/hints/{hint['id']}/views", {"expected_revision": hint["revision"], "shown_at": "2026-10-21T03:00:00Z"})
    assert viewed.status_code == 200, viewed.text
    return viewed.json()


def test_http_hint_views_before_and_after_generation_keep_same_ready_item_and_audit(flow):
    case = source(flow)
    task = queue(flow, case)
    hint = hint_and_view(flow, case)
    assert get(flow, task)["status"] == "queued"
    c = candidate()
    client = Client([c, verification(c)])
    assert run(flow, task, client)["status"] == "ready"
    assert queue(flow, case)["job_id"] == task["job_id"]
    # Viewing the same hint updates a general record revision today; it does
    # not constitute a newly photographed answer or a recognition revision.
    viewed = flow.post(f"/v1/hints/{hint['id']}/views", {"expected_revision": hint["revision"], "shown_at": "2026-10-21T03:01:00Z"})
    assert viewed.status_code == 200
    assert get(flow, task)["status"] == "ready"
    assert start(flow, task).status_code == 201
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        attempt = session.get(Entity, case["current_attempt_id"])
        assert row.snapshot["source_help"]["help_condition"] == "unknown"
        assert row.audit["executions"][-1]["source_help_after"]["help_condition"] == "hint_used"
        assert row.audit["source_help_at_start"]["record_revision"] == attempt.revision
        assert attempt.document["help_condition"] == "hint_used"
    assert len(client.calls) == 2


def test_help_changes_during_blind_verification_do_not_discard_the_generated_item(flow):
    case = source(flow)
    task = queue(flow, case)
    c = candidate()
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(VariantGeneration, task["job_id"])
        job = session.get(LearningOutbox, row.outbox_id)
        def changed_help():
            attempt = session.get(Entity, case["current_attempt_id"])
            attempt.revision += 1
            attempt.document = {**attempt.document, "revision": attempt.revision, "help_condition": "staff_help"}
            session.flush()
            return verification(c)
        assert generate_student_variant(session, job, SimpleNamespace(client=Client([c, changed_help])))["status"] == "ready"
        assert row.audit["executions"][-1]["source_help_after"]["help_condition"] == "staff_help"


@pytest.mark.parametrize("change", ["answer_text", "asset_ids", "recognition_revision", "answer_status", "recognition_status", "recognition_uncertainties", "answer_regions", "captured_question_revision"])
def test_judgment_relevant_attempt_changes_still_invalidate_source(flow, change):
    case = source(flow)
    task = queue(flow, case)
    with Session(flow.database.engine) as session:
        attempt = session.get(Entity, case["current_attempt_id"])
        changes = {"answer_text": "different answer", "asset_ids": ["different-original-image"],
                   "recognition_revision": attempt.document["recognition_revision"] + 1,
                   "answer_status": "unclear", "recognition_status": "needs_revision",
                   "recognition_uncertainties": ["实际作答有不清之处"], "answer_regions": [],
                   "captured_question_revision": 2}
        assert attempt.document.get(change) != changes[change]
        attempt.document = {**attempt.document, change: changes[change]}
        session.commit()
    client = Client([])
    assert get(flow, task)["status"] == "superseded"
    assert run(flow, task, client)["status"] == "superseded"
    assert client.calls == []


def test_legacy_opaque_fingerprint_remains_usable_only_when_exact_record_is_unchanged(flow):
    case = source(flow)
    task = queue(flow, case)
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(VariantGeneration, task["job_id"])
        legacy = deepcopy(row.snapshot)
        legacy.pop("binding_version")
        legacy.pop("source_help")
        attempt = session.get(Entity, case["current_attempt_id"])
        legacy["attempt_digest"] = _digest(attempt.document)
        row.snapshot, row.source_fingerprint = legacy, _digest(legacy)
        session.flush()
        assert snapshot_current(session, row)
        attempt.document = {**attempt.document, "answer_text": "another answer", "help_condition": "hint_used"}
        session.flush()
        assert not snapshot_current(session, row)
