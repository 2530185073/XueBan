"""No network, real image decode + migrated DB + actual HTTP/worker transactions."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import io
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from xueban_backend.app import create_app
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Asset, Entity, Task
from xueban_backend.models import PrincipalRole, StudentGrant
from xueban_backend.providers import ProviderError
from xueban_backend.providers.ark import Metadata, Usage
from xueban_backend.providers.correction_pipeline import PipelineResult
from xueban_backend.worker import CorrectionWorker


def jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(stream, format="JPEG")
    return stream.getvalue()


class SyntheticPipeline:
    """Deterministic injected port, never installed by the production entrypoint."""
    def __init__(self):
        self.verdict = "incorrect"
        self.error = None
        self.callback = None
        self.unknown_location = False
        self.stem = "1 + 1 = ?"
        self.label = "1"
        self.extra_item_label = None
        self.calls = []
        self.metadata = Metadata("synthetic-test-model", "synthetic-test-model", None, None, None, 1, ())

    def recognize(self, *, images):
        self.calls.append("recognize")
        if self.error:
            raise self.error
        image = images[0]
        region = {"asset_id": image.asset_id, "x": 1, "y": 1, "width": min(100, image.width_px - 1), "height": min(60, image.height_px - 1), "coordinate_space": "original_pixels"}
        result = PipelineResult({"items": [{"asset_id": image.asset_id, "label": self.label, "stem_text": self.stem, "answer_text": "3", "answer_status": "present", "regions": [] if self.unknown_location else [region], "answer_regions": [] if self.unknown_location else [region], "recognition_status": "ready", "location_status": "unknown" if self.unknown_location else "located", "uncertainties": []}], "coverage": {"collection_scope": "partial", "unrecognized_regions": [], "note": None}}, self.metadata)
        if self.extra_item_label:
            result.data["items"].append({**deepcopy(result.data["items"][0]), "label": self.extra_item_label})
        return result

    def check(self, **context):
        self.calls.append("check")
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return PipelineResult({"origin": "model", "validity": "needs_review" if self.verdict == "unable_to_judge" else "candidate", "verdict": self.verdict, "reason_code": "unclear_image" if self.verdict == "unable_to_judge" else None, "basis": [{"kind": "model_hypothesis", "summary": "合成验证依据，不能视为老师确认。", "source_ref": None, "regions": []}], "first_error_region": None, "input_versions": context["input_versions"], "confirmation_source": None, "confirmed_by": None, "confirmed_at": None}, self.metadata)

    def recheck(self, **context):
        result = self.check(**context)
        self.calls[-1] = "recheck"
        return result

    def hint(self, **context):
        self.calls.append("hint")
        return PipelineResult({"level": context["level"], "content": "请先核对这一处的运算。", "exposes_answer": False, "source_is_provisional": context["decision"]["validity"] != "confirmed"}, self.metadata)


@pytest.fixture
def flow(settings, database, signing, tmp_path, payload):
    cfg = replace(settings, asset_storage_dir=tmp_path / "private-images", public_base_url="http://testserver")
    pipeline = SyntheticPipeline()
    app = create_app(cfg)
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + signing.token()}
    client.headers.update(headers)
    def post(path, data, key=None):
        return client.post(path, json=data, headers={"Idempotency-Key": key or "test-" + uuid4().hex})
    response = post("/v1/students/student_a/homeworks", payload)
    assert response.status_code == 201, response.text
    homework_id = response.json()["id"]
    value = SimpleNamespace(client=client, cfg=cfg, pipeline=pipeline, worker=CorrectionWorker(database.engine, cfg, pipeline), post=post, homework_id=homework_id, database=database, signing=signing)
    def homework():
        return client.get(f"/v1/homeworks/{homework_id}").json()
    value.homework = homework
    def upload(*, body=None, sha=None, dimensions=(120, 80), mime="image/jpeg"):
        content = body or jpeg()
        response = post(f"/v1/homeworks/{homework_id}/uploads", {"expected_revision": homework()["revision"], "page_index": len(homework()["asset_ids"]), "sha256": sha or hashlib.sha256(content).hexdigest(), "byte_size": len(content), "mime_type": mime, "width": dimensions[0], "height": dimensions[1]})
        assert response.status_code == 201, response.text
        ticket = response.json()
        return ticket, content
    value.upload = upload
    def uploaded():
        ticket, content = upload()
        response = client.put(ticket["put_url"], content=content, headers=ticket["required_headers"])
        assert response.status_code == 204, response.text
        asset = ticket["asset"]
        response = post(f"/v1/assets/{asset['id']}/complete", {"expected_revision": asset["revision"], "sha256": asset["sha256"], "byte_size": asset["byte_size"]})
        assert response.status_code == 200, response.text
        return response.json()
    value.uploaded = uploaded
    def recognize():
        asset = uploaded()
        response = post(f"/v1/homeworks/{homework_id}/recognitions", {"expected_revision": homework()["revision"], "asset_ids": [asset["id"]]})
        assert response.status_code == 202, response.text
        return asset, response.json()["job"]
    value.recognize = recognize
    def ready():
        asset, job = recognize()
        assert value.worker.run_once()
        assert client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
        return client.get(f"/v1/cases/{homework()['case_ids'][0]}").json()
    value.ready = ready
    def start_check(case):
        attempt = client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
        response = post(f"/v1/cases/{case['id']}/checks", {"expected_revision": case["revision"], "attempt_id": attempt["id"], "question_revision": case["current_question_revision"], "attempt_recognition_revision": attempt["recognition_revision"]})
        assert response.status_code == 202, response.text
        return response.json()["job"]
    value.start_check = start_check
    yield value
    client.close()
    app.state.engine.dispose()


def test_real_image_upload_job_check_candidate_and_history(flow):
    case = flow.ready()
    job = flow.start_check(case)
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"

    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert case["current_decision"]["validity"] == "candidate"
    assert case["progress"] == "needs_correction"
    decision = flow.client.get("/v1/decisions/" + case["current_decision"]["id"]).json()
    assert decision["currently_effective"] is True
    assert decision["record"]["confirmation_source"] is None
    history = flow.client.get("/v1/students/student_a/homeworks").json()["items"]
    assert history[0]["case_ids"] == [case["id"]]
    assert len(history[0]["job_ids"]) == 2


def test_upload_requires_ticket_and_decodable_verified_bytes(flow):
    ticket, content = flow.upload()
    assert flow.client.put(ticket["put_url"], content=content).status_code == 401
    assert flow.client.put(ticket["put_url"], content=content[:-1], headers=ticket["required_headers"]).status_code == 422
    bad, content = flow.upload(body=b"not-an-image")
    assert flow.client.put(bad["put_url"], content=content, headers=bad["required_headers"]).status_code == 422
    bad, content = flow.upload(dimensions=(10, 10))
    assert flow.client.put(bad["put_url"], content=content, headers=bad["required_headers"]).status_code == 422
    asset = ticket["asset"]
    incomplete = flow.post(f"/v1/assets/{asset['id']}/complete", {"expected_revision": asset["revision"], "sha256": asset["sha256"], "byte_size": asset["byte_size"]})
    assert incomplete.status_code == 503


def test_private_read_ticket_is_short_lived_and_grant_revocation_blocks_it(flow):
    asset = flow.uploaded()
    response = flow.client.get(f"/v1/assets/{asset['id']}/access")
    assert response.status_code == 200
    url = response.json()["read_url"]
    anonymous = TestClient(create_app(flow.cfg))
    assert anonymous.get(url).content == jpeg()
    assert anonymous.get(urlsplit(url).path).status_code == 422
    with Session(flow.database.engine) as session:
        grant = session.get(StudentGrant, ("actor_a", "student_a"))
        grant.active = False
        session.commit()
    assert anonymous.get(url).status_code == 404
    anonymous.close()


def test_asset_ticket_renewal_and_idempotent_replay(flow):
    ticket, content = flow.upload()
    asset = ticket["asset"]
    with Session(flow.database.engine) as session:
        stored = session.get(Asset, asset["id"])
        stored.upload_expires_at = now() - timedelta(seconds=1)
        session.commit()
    assert flow.client.put(ticket["put_url"], content=content, headers=ticket["required_headers"]).status_code == 401
    path = f"/v1/assets/{asset['id']}/upload-ticket"
    body = {"expected_revision": asset["revision"]}
    renewed = flow.post(path, body, "renew-test-123")
    assert renewed.status_code == 200
    assert flow.post(path, body, "renew-test-123").json() == renewed.json()
    assert flow.post(path, {"expected_revision": 999}, "renew-test-123").status_code == 409
    assert flow.client.put(ticket["put_url"], content=content, headers=renewed.json()["required_headers"]).status_code == 204


def test_resources_and_writes_isolate_other_student(flow):
    case = flow.ready()
    other = {"Authorization": "Bearer " + flow.signing.token("subject-b")}
    homework = flow.homework()
    for path in [f"/v1/homeworks/{homework['id']}", f"/v1/assets/{homework['asset_ids'][0]}", f"/v1/jobs/{homework['job_ids'][0]}", f"/v1/questions/{case['question_id']}", f"/v1/attempts/{case['current_attempt_id']}", f"/v1/cases/{case['id']}"]:
        assert flow.client.get(path, headers=other).status_code == 404
    response = flow.client.post(f"/v1/cases/{case['id']}/progress", json={"expected_revision": case["revision"], "action": "pause"}, headers={**other, "Idempotency-Key": "cross-student-test"})
    assert response.status_code == 404


def test_worker_restart_reclaims_expired_lease_once_and_fences_old_owner(flow):
    asset, job = flow.recognize()
    claimed = flow.worker.claim()
    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        task.lease_expires_at = now() - timedelta(seconds=1)
        session.commit()
    replacement = CorrectionWorker(flow.database.engine, flow.cfg, flow.pipeline)
    assert replacement.run_once()
    assert not flow.worker.finish(*claimed, error=ProviderError("worker_failure"))
    assert not replacement.run_once()
    assert len(flow.homework()["case_ids"]) == 1
    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        assert task.tries == 2
        assert len(task.audit["executions"]) == 2
        assert task.audit["executions"][0]["charge_status"] == "unknown"


def test_provider_failure_is_failed_job_not_empty_success(flow):
    _, job = flow.recognize()
    flow.pipeline.error = ProviderError("timeout")
    assert flow.worker.run_once()
    result = flow.client.get(f"/v1/jobs/{job['id']}").json()
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "provider_timeout"
    assert result["result"] is None
    assert flow.homework()["case_ids"] == []


def test_uncertain_location_does_not_create_fake_full_page_question(flow):
    flow.pipeline.unknown_location = True
    _, job = flow.recognize()
    assert flow.worker.run_once()
    assert flow.homework()["question_ids"] == []
    assert flow.homework()["coverage"]["collection_scope"] == "unknown"
    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        assert task.audit["executions"][0]["candidate"]["items"][0]["regions"] == []


def test_pause_revision_does_not_invalidate_check_content(flow):
    case = flow.ready()
    job = flow.start_check(case)
    def pause():
        fresh = flow.client.get(f"/v1/cases/{case['id']}").json()
        assert flow.post(f"/v1/cases/{case['id']}/progress", {"expected_revision": fresh["revision"], "action": "pause"}).status_code == 200
    flow.pipeline.callback = pause
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    assert flow.client.get(f"/v1/cases/{case['id']}").json()["progress"] == "paused"


def teacher(flow):
    with Session(flow.database.engine) as session:
        session.add(PrincipalRole(principal_id="actor_a", role="sitting_teacher"))
        session.commit()


def test_staff_recognition_revision_supersedes_inflight_check_and_keeps_history(flow):
    case = flow.ready()
    job = flow.start_check(case)
    teacher(flow)
    def revise():
        question = flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]
        response = flow.post(f"/v1/questions/{case['question_id']}/revisions", {"expected_revision": question["revision"], "stem_text": "2 + 1 = ?", "regions": question["regions"], "reason": "合成识别修正"})
        assert response.status_code == 201, response.text
    flow.pipeline.callback = revise
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "superseded"
    assert flow.client.get(f"/v1/cases/{case['id']}").json()["current_decision"] is None
    history = flow.client.get(f"/v1/questions/{case['question_id']}?revision=1").json()
    assert history["record"]["stem_text"] == "1 + 1 = ?"
    assert history["currently_effective"] is False


def test_hint_new_correction_recheck_and_only_staff_can_confirm(flow):
    case = flow.ready()
    flow.start_check(case)
    flow.worker.run_once()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    source = case["current_decision"]
    response = flow.post(f"/v1/cases/{case['id']}/hints", {"expected_revision": case["revision"], "attempt_id": case["current_attempt_id"], "source_decision_id": source["id"], "source_decision_revision": source["revision"], "level": "direction"})
    assert response.status_code == 202, response.text
    flow.worker.run_once()
    hint_job = flow.client.get(f"/v1/jobs/{response.json()['job']['id']}").json()
    assert hint_job["status"] == "succeeded", hint_job
    hint = flow.client.get(f"/v1/hints/{hint_job['result']['resource_id']}").json()
    assert flow.post(f"/v1/hints/{hint['id']}/views", {"expected_revision": hint["revision"], "shown_at": "2026-10-17T01:00:00Z"}).status_code == 200
    asset = flow.uploaded()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {"expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"], "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]], "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "none_reported"})
    assert response.status_code == 202, response.text
    flow.worker.run_once()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    flow.pipeline.verdict = "correct"
    flow.start_check(case)
    flow.worker.run_once()
    assert flow.pipeline.calls[-1] == "recheck"
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert case["current_decision"]["validity"] == "candidate"
    assert case["progress"] != "verified_resolved"
    assert flow.client.get(f"/v1/hints/{hint['id']}").json()["validity"] == "stale"
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["help_condition"] == "hint_used"
    decision = case["current_decision"]
    body = {"expected_revision": case["revision"], "target_decision_id": decision["id"], "target_decision_revision": decision["revision"], "action": "confirm", "verdict": "correct", "reason": "老师核对原图", "basis": [{"kind": "staff_observation", "summary": "已核对原图及过程。", "source_ref": None, "regions": []}], "attempt_id": attempt["id"], "question_revision": case["current_question_revision"], "attempt_recognition_revision": attempt["recognition_revision"]}
    assert flow.post(f"/v1/cases/{case['id']}/reviews", body).status_code == 403
    teacher(flow)
    response = flow.post(f"/v1/cases/{case['id']}/reviews", body)
    assert response.status_code == 201, response.text
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert case["progress"] == "verified_resolved"
    assert case["current_decision"]["origin"] == "staff"


def test_unable_to_judge_is_successful_check_but_not_resolution(flow):
    case = flow.ready()
    flow.pipeline.verdict = "unable_to_judge"
    job = flow.start_check(case)
    flow.worker.run_once()
    result = flow.client.get(f"/v1/jobs/{job['id']}").json()
    assert result["status"] == "succeeded"
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert case["current_decision"]["validity"] == "needs_review"
    assert case["progress"] != "verified_resolved"


def test_help_completion_cannot_confirm_correctness(flow):
    case = flow.ready()
    response = flow.post(f"/v1/cases/{case['id']}/help-requests", {"expected_revision": case["revision"], "attempt_id": case["current_attempt_id"], "kind": "question", "note": "需要坐班老师看原图"})
    assert response.status_code == 201
    help_request = response.json()
    assert flow.client.get("/v1/help-requests").status_code == 403
    teacher(flow)
    assert flow.client.get("/v1/help-requests").json()["items"][0]["id"] == help_request["id"]
    response = flow.post(f"/v1/help-requests/{help_request['id']}/claim", {"expected_revision": 1})
    assert response.status_code == 200
    response = flow.post(f"/v1/help-requests/{help_request['id']}/complete", {"expected_revision": response.json()["revision"], "outcome": "resolved", "note": "已解释如何阅读题目"})
    assert response.status_code == 200
    assert flow.client.get("/v1/help-requests").json()["items"] == []
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert case["current_decision"] is None
    assert case["progress"] != "verified_resolved"


def test_retryable_provider_job_is_durable_and_bounded(flow):
    _, job = flow.recognize()
    flow.pipeline.error = ProviderError("upstream_unavailable")
    for attempt in range(3):
        assert flow.worker.run_once()
        document = flow.client.get(f"/v1/jobs/{job['id']}").json()
        assert document["status"] == ("retryable_failed" if attempt < 2 else "failed")
        with Session(flow.database.engine) as session:
            stored = session.get(Task, job["id"])
            stored.available_at = now() - timedelta(seconds=1)
            session.commit()
    assert not flow.worker.run_once()
    assert len(flow.pipeline.calls) == 3


def test_manual_initial_review_after_model_technical_failure(flow):
    case = flow.ready()
    job = flow.start_check(case)
    flow.pipeline.error = ProviderError("upstream_timeout")
    flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["failure"]["code"] == "provider_timeout"
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    teacher(flow)
    response = flow.post(f"/v1/cases/{case['id']}/reviews", {"expected_revision": case["revision"], "target_decision_id": None, "target_decision_revision": None, "action": "manual_initial", "verdict": "incorrect", "reason": "老师直接核对", "basis": [{"kind": "staff_observation", "summary": "原图算式与作答不一致。", "source_ref": None, "regions": []}], "attempt_id": case["current_attempt_id"], "question_revision": case["current_question_revision"], "attempt_recognition_revision": 1})
    assert response.status_code == 201, response.text
    assert flow.client.get(f"/v1/cases/{case['id']}").json()["current_decision"]["origin"] == "staff"


def test_postgres_concurrent_identical_recognition_creates_one_task(flow):
    if flow.database.dialect != "postgresql":
        pytest.skip("requires actual PostgreSQL row locks")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    asset = flow.uploaded()
    payload = {"expected_revision": flow.homework()["revision"], "asset_ids": [asset["id"]]}
    barrier = Barrier(2)
    def submit():
        with TestClient(create_app(flow.cfg)) as client:
            barrier.wait()
            return client.post(f"/v1/homeworks/{flow.homework_id}/recognitions", json=payload, headers={"Authorization": "Bearer " + flow.signing.token(), "Idempotency-Key": "parallel-recognition-123"})
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))
    assert [r.status_code for r in results] == [202, 202]
    assert results[0].json() == results[1].json()
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_postgres_two_workers_only_one_claims_same_task(flow):
    if flow.database.dialect != "postgresql":
        pytest.skip("requires actual PostgreSQL SKIP LOCKED")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    flow.recognize()
    barrier = Barrier(2)
    def claim():
        worker = CorrectionWorker(flow.database.engine, flow.cfg, flow.pipeline)
        barrier.wait()
        return worker.claim()
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: claim(), range(2)))
    assert sum(item is not None for item in claims) == 1


def test_check_idempotency_same_body_does_not_duplicate_and_changed_body_conflicts(flow):
    case = flow.ready()
    body = {"expected_revision": case["revision"], "attempt_id": case["current_attempt_id"], "question_revision": 1, "attempt_recognition_revision": 1}
    path = f"/v1/cases/{case['id']}/checks"
    first = flow.post(path, body, "same-check-key-123")
    assert first.status_code == 202
    assert flow.post(path, body, "same-check-key-123").json() == first.json()
    assert flow.post(path, {**body, "question_revision": 2}, "same-check-key-123").status_code == 409
    assert len(flow.client.get(f"/v1/cases/{case['id']}").json()["check_ids"]) == 1


def test_recheck_after_question_revision_keeps_original_capture_version(flow):
    case = flow.ready()
    teacher(flow)
    question = flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]
    response = flow.post(f"/v1/questions/{question['question_id']}/revisions", {"expected_revision": 1, "stem_text": "已核实的题干", "regions": question["regions"], "reason": "核对原图"})
    assert response.status_code == 201
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    job = flow.start_check(case)
    flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["captured_question_revision"] == 1
    assert case["current_question_revision"] == 2


def test_staff_revision_that_clears_judgment_stays_in_review_queue(flow):
    case = flow.ready()
    teacher(flow)
    queue = lambda: {item["case_id"]: item["reason"] for item in flow.client.get("/v1/review-queue").json()["items"]}
    assert case["id"] not in queue()
    flow.start_check(case)
    flow.worker.run_once()
    question = flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]
    response = flow.post(f"/v1/questions/{question['question_id']}/revisions", {"expected_revision": question["revision"], "stem_text": "已核实的题干", "regions": question["regions"], "reason": "核对原图"})
    assert response.status_code == 201
    assert flow.client.get(f"/v1/cases/{case['id']}").json()["current_decision"] is None
    assert queue()[case["id"]] == "awaiting_check"
    flow.start_check(flow.client.get(f"/v1/cases/{case['id']}").json())
    flow.worker.run_once()
    assert queue().get(case["id"]) != "awaiting_check"


def test_failed_commit_rolls_back_task_and_idempotency_for_retry(flow, monkeypatch):
    import xueban_backend.correction_api as api
    asset = flow.uploaded()
    path = f"/v1/homeworks/{flow.homework_id}/recognitions"
    body = {"expected_revision": flow.homework()["revision"], "asset_ids": [asset["id"]]}
    original = api.create_task
    from sqlalchemy.exc import OperationalError
    def fail_after_task(*args, **kwargs):
        original(*args, **kwargs)
        raise OperationalError("synthetic", {}, Exception("synthetic database outage"))
    monkeypatch.setattr(api, "create_task", fail_after_task)
    response = flow.post(path, body, "atomic-recognition-123")
    assert response.status_code == 503
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 0
    monkeypatch.setattr(api, "create_task", original)
    assert flow.post(path, body, "atomic-recognition-123").status_code == 202


@pytest.mark.parametrize("original,replacement", [
    ("1 + 1 = ?", "另一道题 2 + 2 = ?"),
    ("x²", "x2"),
    ("1 2", "12"),
    ("Expand 3(x - 2).", "Question: Expand 3(x + 2)."),
    ("x - 2", "Question: x 2"),
    ("x²", "Question: x2"),
])
def test_single_wrong_question_correction_is_not_bound_to_target(flow, original, replacement):
    flow.pipeline.stem = original
    case = flow.ready()
    asset = flow.uploaded()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {"expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"], "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]], "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "unknown"})
    assert response.status_code == 202
    flow.pipeline.stem = replacement
    flow.worker.run_once()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None
    assert attempt["answer_regions"] == []
    flow.pipeline.verdict = "correct"
    flow.start_check(case)
    flow.worker.run_once()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert case["current_decision"]["verdict"] == "unable_to_judge"
    assert case["current_decision"]["reason_code"] == "recognition_conflict"


@pytest.mark.parametrize("original,replacement", [
    ("Expand 3(x - 2).", "Question: Expand 3(x - 2)."),
    ("Question: Expand 3(x - 2).", "Expand 3(x - 2)."),
    ("求 x² - 2 的值。", "题干：求 x² - 2 的值。"),
])
def test_explicit_field_prefix_same_question_correction_reaches_recheck(flow, original, replacement):
    flow.pipeline.stem = original
    case = flow.ready()
    parent_attempt_id = case["current_attempt_id"]
    asset = flow.uploaded()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": parent_attempt_id,
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "unknown",
    })
    assert response.status_code == 202
    flow.pipeline.stem = replacement
    assert flow.worker.run_once()
    job = flow.client.get(f"/v1/jobs/{response.json()['job']['id']}").json()
    assert job["status"] == "succeeded"
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "ready"
    assert attempt["answer_text"] == "3"
    assert attempt["parent_attempt_id"] == parent_attempt_id
    assert flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]["stem_text"] == original
    flow.pipeline.verdict = "correct"
    check_job = flow.start_check(case)
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{check_job['id']}").json()["status"] == "succeeded"
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert case["current_decision"]["verdict"] == "correct"
    assert case["current_decision"]["validity"] == "candidate"
    assert case["progress"] != "verified_resolved"
    assert flow.pipeline.calls[-1] == "recheck"


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("WEBP", "image/webp")])
def test_non_jpeg_image_upload_and_complete(flow, format, mime):
    image = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(image, format=format)
    ticket, content = flow.upload(body=image.getvalue(), mime=mime)
    response = flow.client.put(ticket["put_url"], content=content, headers=ticket["required_headers"])
    assert response.status_code == 204, response.text
    asset = ticket["asset"]
    response = flow.post(f"/v1/assets/{asset['id']}/complete", {"expected_revision": 1, "sha256": asset["sha256"], "byte_size": len(content)})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "uploaded"


def test_failure_to_store_paid_result_preserves_call_metadata(flow):
    case = flow.ready()
    job = flow.start_check(case)
    flow.pipeline.metadata = Metadata("synthetic", "m" * 161, "safe-request-id", None, Usage(100, 30, 130), 12, ())
    flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "failed"
    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        execution = task.audit["executions"][0]
        assert execution["outcome"] == "failed"
        assert execution["metadata"]["usage"]["total_tokens"] == 130
        assert execution["metadata"]["request_id"] == "safe-request-id"
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == "decision")) == 0


def test_hint_same_semantics_reuses_queued_and_completed_job_across_keys(flow):
    case = flow.ready()
    flow.start_check(case)
    flow.worker.run_once()
    def request():
        current = flow.client.get(f"/v1/cases/{case['id']}").json()
        source = current["current_decision"]
        return flow.post(f"/v1/cases/{case['id']}/hints", {"expected_revision": current["revision"], "attempt_id": current["current_attempt_id"], "source_decision_id": source["id"], "source_decision_revision": source["revision"], "level": "direction"})
    first, second = request(), request()
    assert first.status_code == second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    flow.worker.run_once()
    third = request()
    assert third.json()["job"]["id"] == first.json()["job"]["id"]
    assert third.json()["job"]["status"] == "succeeded"
    assert not flow.worker.run_once()
    assert flow.pipeline.calls.count("hint") == 1


def test_slow_upload_does_not_lock_other_upload_on_same_homework(flow):
    import asyncio
    import httpx
    first, content = flow.upload()
    second, other_content = flow.upload()
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def slow_stream():
            yield content[:10]
            started.set()
            await release.wait()
            yield content[10:]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=flow.client.app), base_url="http://testserver") as client:
            slow = asyncio.create_task(client.put(first["put_url"], headers=first["required_headers"], content=slow_stream()))
            await asyncio.wait_for(started.wait(), 5)
            try:
                fast = await asyncio.wait_for(client.put(second["put_url"], headers=second["required_headers"], content=other_content), 5)
                assert fast.status_code == 204, fast.text
            finally:
                release.set()
            result = await asyncio.wait_for(slow, 5)
            assert result.status_code == 204, result.text
    asyncio.run(scenario())


def test_same_shared_stem_different_subquestion_is_not_bound(flow):
    flow.pipeline.label = "1(1)"
    flow.pipeline.extra_item_label = "1(2)"
    case = flow.ready()
    flow.pipeline.extra_item_label = None
    asset = flow.uploaded()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {"expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"], "question_revision": 1, "asset_ids": [asset["id"]], "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "unknown"})
    assert response.status_code == 202
    flow.pipeline.label = "2(3)"
    flow.worker.run_once()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None


def test_unique_complete_stem_allows_free_form_label_variation(flow):
    flow.pipeline.label = "Question 1: Add two numbers"
    case = flow.ready()
    asset = flow.uploaded()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {"expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"], "question_revision": 1, "asset_ids": [asset["id"]], "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "unknown"})
    assert response.status_code == 202
    flow.pipeline.label = "1"
    flow.worker.run_once()
    case = flow.client.get(f"/v1/cases/{case['id']}").json()
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "ready"


def test_worker_startup_error_does_not_print_configuration(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["DATABASE_URL"] = "synthetic-secret-value-is-not-a-database-url"
    result = subprocess.run([sys.executable, "-m", "xueban_backend.worker", "--once"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "worker_stopped" in result.stderr
    assert "synthetic-secret-value" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_check_cannot_reference_attempt_from_another_case(flow):
    first = flow.ready()
    flow.recognize()
    flow.worker.run_once()
    other_id = next(identifier for identifier in flow.homework()["case_ids"] if identifier != first["id"])
    other = flow.client.get(f"/v1/cases/{other_id}").json()
    response = flow.post(f"/v1/cases/{first['id']}/checks", {"expected_revision": first["revision"], "attempt_id": other["current_attempt_id"], "question_revision": 1, "attempt_recognition_revision": 1})
    assert response.status_code == 404
