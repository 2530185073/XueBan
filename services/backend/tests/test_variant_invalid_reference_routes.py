"""Legacy ai_verified references must be checked at every student boundary.

All records use the migrated temporary test database. Model generation is
synthetic; no live inference, database, or service is contacted.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_practice_photo import Client as PhotoClient, image_payload
from test_practice_solution import no_live_models  # noqa: F401 (autouse fixture)
from test_student_variants import Client, candidate, queue, run, source, verification
from xueban_backend.asset_store import LocalAssetStore
from xueban_backend.errors import Problem
from xueban_backend.learning_models import (
    LearningEvidence, LearningOutbox, PracticeAssessment, PracticeItem,
    PracticeResponse, PracticeSession, VariantGeneration,
)
from xueban_backend.models import IdempotencyRecord
from xueban_backend.practice import AnswerInput, now
from xueban_backend.practice_generation import (
    _assessment_context, _item_digest, assess_student_practice, grade_generated,
)
from xueban_backend.practice_photo import assess_practice_photo
from xueban_backend.providers.variant_arithmetic import item_arithmetic_issues


START_URL = "/v1/students/student_a/practice-sessions"
NOTEBOOK_URL = "/v1/students/student_a/variant-notebook"
BAD_SOLUTION = "旧版错误参考：(-24)÷6=4。"


@pytest.fixture
def signed_reference(flow):
    case = source(flow, "fill_blank")
    task = queue(flow, case)
    generated = candidate()
    generated.update(stem_text="(-24) ÷ 6 = ____。", hint_text="先判断商的符号。",
                     field_answers=[{"field_id": "blank1", "answer_kind": "number", "answer_values": ["-4"]}],
                     solution_text="异号相除得负：(-24)÷6=-4。")
    assert run(flow, task, Client([generated, verification(generated)]))["status"] == "ready"
    start_body = {"purpose": "variant", "willing": True, "generation_job_id": task["job_id"], "count": 1}
    start_key = {"Idempotency-Key": "signed-reference-start"}
    started = flow.client.post(START_URL, json=start_body, headers=start_key)
    assert started.status_code == 201, started.text
    activity = started.json()
    return SimpleNamespace(flow=flow, client=flow.client, database=flow.database,
        cfg=flow.cfg, case_id=case["id"], generation_id=task["job_id"],
        session_id=activity["id"], item_id=activity["items"][0]["id"],
        url=f"/v1/practice-sessions/{activity['id']}", start_body=start_body, start_key=start_key)


def invalidate(s, *, assessment_id=None):
    """Recreate a legacy wrong-sign reference without changing its version/status."""
    with Session(s.database.engine) as session, session.begin():
        item = session.get(PracticeItem, s.item_id)
        item.field_answers = [{"field_id": "blank1", "answer_kind": "number", "answer_values": ["4"]}]
        item.solution_text = BAD_SOLUTION
        assert item.status == "ai_verified" and item.verification["status"] == "verified"
        assert item_arithmetic_issues(item)
        if assessment_id:
            # Simulate a job originally queued against this bad reference.
            # A stale digest/revision must not accidentally make this test pass.
            assessment = session.get(PracticeAssessment, assessment_id)
            assessment.item_digest = _item_digest(item)
            assert assessment.item_revision == item.revision


def snapshot(s):
    with Session(s.database.engine) as session:
        records = {model.__tablename__: [dict(row) for row in session.execute(
            select(model.__table__).order_by(*model.__table__.primary_key.columns)).mappings()]
            for model in (PracticeItem, PracticeSession, PracticeResponse, PracticeAssessment,
                          VariantGeneration, LearningOutbox, LearningEvidence, IdempotencyRecord)}
    records["asset_files"] = sorted(str(path.relative_to(s.cfg.asset_storage_dir))
                                    for path in s.cfg.asset_storage_dir.rglob("*") if path.is_file())
    return records


def body(s, answer="-4"):
    return {"item_id": s.item_id, "answers": [{"field_id": "blank1", "answer_text": answer}]}


def rejected(s, request, *, code="verification_failed"):
    before = snapshot(s)
    response = request()
    assert response.status_code == 409, response.text
    assert response.json()["code"] in ({code} if isinstance(code, str) else set(code))
    assert BAD_SOLUTION not in response.text
    assert not {"solution_text", "field_answers", "answer_values", "hint_text"} & response.json().keys()
    assert snapshot(s) == before


def historical_response(s, result="incorrect"):
    with Session(s.database.engine) as session, session.begin():
        activity = session.get(PracticeSession, s.session_id)
        session.add(PracticeResponse(id="response_legacy_signed", tenant_id=activity.tenant_id,
            student_id=activity.student_id, session_id=activity.id, item_id=s.item_id,
            graph_version=activity.graph_version, knowledge_id=None, event_type="optional_variant",
            answer_text="-4", result=result, hint_used=False,
            details={"answers": body(s)["answers"], "feedback": "旧判定反馈"}, occurred_at=now()))
        activity.status, activity.ended_at = "completed", now()


@pytest.mark.parametrize("route", ["responses", "hint", "solution", "photo-responses"])
def test_old_session_rejects_invalid_verified_reference_before_writes(signed_reference, route):
    s = signed_reference
    invalidate(s)
    if route in ("hint", "solution"):
        request = lambda: s.client.post(f"{s.url}/items/{s.item_id}/{route}")
    elif route == "responses":
        request = lambda: s.client.post(f"{s.url}/responses", json=body(s))
    else:
        request = lambda: s.client.post(f"{s.url}/photo-responses", json={"item_id": s.item_id, "image": image_payload()})
    rejected(s, request)


def test_invalid_generation_is_effectively_superseded_without_rewriting_persisted_records(signed_reference):
    s = signed_reference
    invalidate(s)
    before = snapshot(s)
    response = s.client.get(f"/v1/variant-generations/{s.generation_id}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "superseded"
    assert response.json()["failure_code"] == "verification_failed"
    assert response.json()["item_id"] is None
    assert snapshot(s) == before
    rejected(s, lambda: s.client.post(START_URL, json=s.start_body))


@pytest.mark.parametrize("result", ["correct", "incorrect"])
def test_historical_session_and_list_mask_result_and_solution_without_rewriting_response(signed_reference, result):
    s = signed_reference
    historical_response(s, result)
    invalidate(s)
    before = snapshot(s)
    detail = s.client.get(s.url)
    listing = s.client.get(START_URL)
    assert detail.status_code == listing.status_code == 200
    listed = next(row for row in listing.json()["items"] if row["id"] == s.session_id)
    for activity in (detail.json(), listed):
        entry = activity["items"][0]
        assert entry["response"]["result"] == "unscorable"
        assert entry["response"]["answer_text"] == "-4"
        assert entry["response"]["answers"] == body(s)["answers"]
        assert entry["response"]["hint_used"] is False
        assert "旧判定反馈" not in entry["response"]["feedback"]
        assert "solution_text" not in entry
        assert not {"field_answers", "answer_values"} & entry.keys()
    assert snapshot(s) == before


@pytest.mark.parametrize("result", ["correct", "incorrect"])
def test_notebook_reclassifies_bad_reference_as_check_failed_without_rewriting_history(signed_reference, result):
    s = signed_reference
    historical_response(s, result)
    invalidate(s)
    before = snapshot(s)
    response = s.client.get(NOTEBOOK_URL)
    assert response.status_code == 200, response.text
    entry = next(row for row in response.json()["items"] if row["case_id"] == s.case_id)
    assert entry["status"] == "check_failed"
    assert entry["generation_status"] == "superseded"
    assert entry["session_id"] == s.session_id
    assert BAD_SOLUTION not in response.text
    assert snapshot(s) == before


@pytest.mark.parametrize("submission", ["numeric", "semantic", "photo"])
def test_idempotent_submission_replay_cannot_bypass_current_reference_guard(signed_reference, submission):
    s = signed_reference
    headers = {"Idempotency-Key": "signed-reference-submission"}
    route = "photo-responses" if submission == "photo" else "responses"
    payload = ({"item_id": s.item_id, "image": image_payload()} if submission == "photo"
               else body(s, "负四" if submission == "semantic" else "-4"))
    first = s.client.post(f"{s.url}/{route}", json=payload, headers=headers)
    assert first.status_code == (201 if submission == "numeric" else 202), first.text
    invalidate(s)
    rejected(s, lambda: s.client.post(f"{s.url}/{route}", json=payload, headers=headers))


def test_idempotent_start_replay_cannot_reopen_invalid_generation(signed_reference):
    s = signed_reference
    invalidate(s)
    rejected(s, lambda: s.client.post(START_URL, json=s.start_body, headers=s.start_key),
             code=("stale_source", "verification_failed"))


def test_idempotent_retry_replay_cannot_reopen_invalid_generation(signed_reference):
    s = signed_reference
    answer = s.client.post(f"{s.url}/responses", json=body(s, "4"))
    assert answer.status_code == 201 and answer.json()["result"] == "incorrect"
    headers = {"Idempotency-Key": "signed-reference-retry"}
    retried = s.client.post(f"{s.url}/retry", json={}, headers=headers)
    assert retried.status_code == 201, retried.text
    assert retried.json()["id"] != s.session_id
    invalidate(s)
    rejected(s, lambda: s.client.post(f"{s.url}/retry", json={}, headers=headers),
             code=("stale_source", "verification_failed"))


def test_completed_assessment_is_masked_without_rewriting_legacy_result(signed_reference):
    s = signed_reference
    queued = s.client.post(f"{s.url}/responses", json=body(s, "负四"))
    assert queued.status_code == 202, queued.text
    identifier = queued.json()["assessment_id"]
    historical_response(s)
    with Session(s.database.engine) as session, session.begin():
        assessment = session.get(PracticeAssessment, identifier)
        assessment.status, assessment.result, assessment.feedback = "completed", "incorrect", "旧判定反馈"
        session.get(LearningOutbox, assessment.outbox_id).status = "succeeded"
    invalidate(s, assessment_id=identifier)
    before = snapshot(s)
    response = s.client.get(f"/v1/practice-assessments/{identifier}")
    detail = s.client.get(s.url)
    assert response.status_code == detail.status_code == 200
    for assessment in (response.json(), detail.json()["items"][0]["assessment"]):
        assert assessment["status"] == "superseded"
        assert assessment["result"] == "unscorable" and assessment["solution_text"] is None
        assert assessment["feedback"] != "旧判定反馈"
    assert snapshot(s) == before


@pytest.mark.parametrize("rule_only", [False, True])
def test_direct_grading_rejects_bad_verified_reference_without_model_call(signed_reference, rule_only):
    s = signed_reference
    invalidate(s)
    provider = PhotoClient()
    before = snapshot(s)
    with Session(s.database.engine) as session:
        with pytest.raises(Problem) as error:
            grade_generated(session.get(PracticeItem, s.item_id), AnswerInput.model_validate(body(s)),
                            provider, rule_only=rule_only)
    assert error.value.status == 409 and error.value.code == "verification_failed"
    assert provider.calls == []
    assert snapshot(s) == before


@pytest.mark.parametrize("submission", ["semantic", "photo"])
def test_old_async_context_with_matching_digest_cannot_grade_bad_reference(signed_reference, submission):
    s = signed_reference
    route = "photo-responses" if submission == "photo" else "responses"
    payload = ({"item_id": s.item_id, "image": image_payload()} if submission == "photo" else body(s, "负四"))
    queued = s.client.post(f"{s.url}/{route}", json=payload)
    assert queued.status_code == 202, queued.text
    identifier = queued.json()["assessment_id"]
    invalidate(s, assessment_id=identifier)
    before = snapshot(s)
    public = s.client.get(f"/v1/practice-assessments/{identifier}")
    assert public.status_code == 200, public.text
    assert public.json()["status"] == "superseded"
    assert public.json()["result"] == "unscorable" and public.json()["solution_text"] is None
    assert snapshot(s) == before
    provider = PhotoClient()
    worker = SimpleNamespace(client=provider, fallback_client=None, asset_store=LocalAssetStore(s.cfg.asset_storage_dir))
    with Session(s.database.engine) as session, session.begin():
        assessment = session.get(PracticeAssessment, identifier)
        assert _assessment_context(session, assessment) is None
        assert _assessment_context(session, assessment, lock=True) is None
        job = session.get(LearningOutbox, assessment.outbox_id)
        job.status, job.tries = "running", 1
        handler = assess_practice_photo if submission == "photo" else assess_student_practice
        assert handler(session, job, worker) == {"status": "superseded", "result": "unscorable"}
        assert session.scalar(select(PracticeResponse.id).where(PracticeResponse.session_id == s.session_id)) is None
        assert session.get(PracticeSession, s.session_id).status == "active"
        assert session.get(PracticeItem, s.item_id).status == "ai_verified"
    assert provider.calls == []


def test_valid_signed_reference_still_submits_and_reveals_solution(signed_reference):
    s = signed_reference
    revealed = s.client.post(f"{s.url}/items/{s.item_id}/solution")
    assert revealed.status_code == 200 and "-4" in revealed.json()["answer_text"]
    answer = s.client.post(f"{s.url}/responses", json=body(s))
    assert answer.status_code == 201 and answer.json()["result"] == "correct"
