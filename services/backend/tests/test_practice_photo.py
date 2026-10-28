"""Real HTTP/SQLite/private images; every inference call is synthetic or mocked."""
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import io
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from xueban_backend.app import create_app
from xueban_backend.asset_store import LocalAssetStore, MAX_ASSET_BYTES
from xueban_backend.correction_models import Asset
from xueban_backend.governance import purge_student
from xueban_backend.learning_jobs import LearningWorker
from xueban_backend.learning_models import LearningOutbox, PracticeAssessment, PracticeItem, PracticeResponse, PracticeSession
from xueban_backend.models import Homework, PrincipalRole, Student, StudentGrant
from xueban_backend.practice import now
from xueban_backend import practice_generation, practice_photo
from xueban_backend.providers import ProviderError
from xueban_backend.providers.ark import ArkClient, ArkSettings, Metadata
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.responses import ResponsesClient
from xueban_backend.providers.user_original.ark import ArkClient as UserOriginalArkClient


class Client:
    def __init__(self, *outputs):
        self.settings = SimpleNamespace(enabled=True, api_key="synthetic-photo-secret")
        self.outputs, self.calls = list(outputs), []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        result = self.outputs.pop(0)
        if callable(result):
            result = result()
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(data=deepcopy(result), metadata=Metadata("synthetic", "synthetic", None, None, None, 1, ()))


def image_payload(color="white", fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (120, 80), color).save(stream, format=fmt)
    raw = stream.getvalue()
    return {"data_base64": base64.b64encode(raw).decode(), "mime_type": "image/" + fmt.lower().replace("jpg", "jpeg"),
            "width": 120, "height": 80, "sha256": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw)}


def readback(**changes):
    return {"item_id": "pitem_photo", "matches_question": True, "answer_status": "present",
            "answer_text": "3+4=7", "selected_options": None, "answers": None, "uncertainties": [], **changes}


@pytest.fixture
def photo(settings, database, signing, tmp_path):
    cfg = replace(settings, asset_storage_dir=tmp_path / "practice-images")
    app = create_app(cfg)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + signing.token()
    stamp = now()
    with Session(database.engine) as session, session.begin():
        session.add(PracticeItem(id="pitem_photo", tenant_id="tenant_a", owner_student_id="student_a",
            graph_version=None, knowledge_id=None, item_family_id="family_photo", stem_text="计算3+4，写出必要步骤",
            question_type="calculation", question_kind="solution", options=[], answer_fields=[], field_answers=[],
            answer_kind="text", answer_values=["7"], hint_text="把两个数合起来", solution_text="参考步骤：3+4=7",
            difficulty=1, source="model", status="ai_verified", revision=1, verification={"source": "ai"},
            created_at=stamp, updated_at=stamp))
        session.add(PracticeSession(id="practice_photo", tenant_id="tenant_a", student_id="student_a",
            graph_version=None, purpose="variant", knowledge_ids=[], item_ids=["pitem_photo"], hints_used=[],
            gate={"generation_job_id": "generation_photo", "no_expiry": True}, status="active",
            started_at=stamp, budget_ends_at=stamp + timedelta(minutes=10)))
    value = SimpleNamespace(client=client, database=database, cfg=cfg, signing=signing,
                            path="/v1/practice-sessions/practice_photo/photo-responses")
    value.payload = {"item_id": "pitem_photo", "image": image_payload()}
    yield value
    client.close()
    app.state.engine.dispose()


def submit(photo, *, payload=None, key=None):
    return photo.client.post(photo.path, json=payload or photo.payload,
                             headers={"Idempotency-Key": key} if key else {})


def assess(photo, assessment_id, client, fallback=None, tries=1):
    worker = SimpleNamespace(client=client, fallback_client=fallback,
                             asset_store=LocalAssetStore(photo.cfg.asset_storage_dir))
    with Session(photo.database.engine) as session, session.begin():
        row = session.get(PracticeAssessment, assessment_id)
        job = session.get(LearningOutbox, row.outbox_id)
        job.tries, job.status = tries, "running"
        result = practice_photo.assess_practice_photo(session, job, worker)
        job.status = "succeeded"
    return result


def assessment(photo, identifier):
    response = photo.client.get("/v1/practice-assessments/" + identifier)
    assert response.status_code == 200, response.text
    return response.json()


def count(photo, model):
    with Session(photo.database.engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_photo_queue_private_storage_dto_and_worker_completion(photo):
    queued = submit(photo)
    assert queued.status_code == 202, queued.text
    identifier = queued.json()["assessment_id"]
    assert queued.json()["result"] == "pending" and queued.json()["solution_text"] is None
    with Session(photo.database.engine) as session:
        row = session.get(PracticeAssessment, identifier)
        stored = row.submission["photo"]
        assert "data_base64" not in json.dumps(row.submission)
        assert session.get(LearningOutbox, row.outbox_id).kind == "assess_practice_photo"
        path = LocalAssetStore(photo.cfg.asset_storage_dir).path(stored["asset_id"])
        assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    dto = assessment(photo, identifier)
    assert dto["status"] == "queued" and dto["submission"] == {"answer_text": None, "selected_options": None, "answers": None}
    assert "asset_" not in json.dumps(dto) and "storage_root" not in json.dumps(dto)
    client = Client(readback(), {"result": "correct", "feedback": "本次步骤和结果正确。"})
    worker = LearningWorker(photo.database.engine, client=client)
    worker.asset_store = LocalAssetStore(photo.cfg.asset_storage_dir)
    worker.next_scan = now() + timedelta(hours=1)
    assert worker.run_once()
    dto = assessment(photo, identifier)
    assert dto["status"] == "completed" and dto["result"] == "correct"
    assert dto["submission"]["answer_text"] == "3+4=7"
    target = json.loads(client.calls[0]["text"])["target_question"]
    assert target["id"] == "pitem_photo" and target["stem_text"] == "计算3+4，写出必要步骤"
    assert set(target) == {"id", "stem_text", "question_type", "question_kind", "options", "answer_fields"}
    assert client.calls[0]["images"][0].startswith("data:image/png;base64,")
    assert "参考步骤" not in client.calls[0]["text"]
    grading = json.loads(client.calls[1]["text"])
    assert grading["student_submission"]["answer_text"] == "3+4=7"
    assert grading["independently_verified_reference"]["solution_text"] == "参考步骤：3+4=7"
    assert count(photo, PracticeResponse) == 1
    assert count(photo, Asset) == count(photo, Homework) == 0
    assert not worker.run_once()


@pytest.mark.parametrize("change", [
    {"data_base64": "not base64!"}, {"data_base64": "data:image/png;base64,abc"},
    {"sha256": "0" * 64}, {"sha256": "g" * 64}, {"byte_size": 1}, {"byte_size": MAX_ASSET_BYTES + 1},
    {"mime_type": "image/gif"}, {"mime_type": "image/jpeg"}, {"width": 121}, {"height": 0},
    {"width": True}, {"height": "80"}, {"width": 24_000_000}, {"storage_root": "/client/path"},
])
def test_rejects_invalid_image_without_assets_or_jobs(photo, change):
    payload = deepcopy(photo.payload)
    payload["image"].update(change)
    response = submit(photo, payload=payload)
    assert response.status_code == 422, response.text
    assert count(photo, PracticeAssessment) == count(photo, LearningOutbox) == 0
    assert not photo.cfg.asset_storage_dir.exists()


def test_rejects_oversized_base64_before_decode(photo):
    payload = deepcopy(photo.payload)
    payload["image"]["data_base64"] = "A" * (4 * ((MAX_ASSET_BYTES + 2) // 3) + 4)
    assert submit(photo, payload=payload).status_code == 422
    assert not photo.cfg.asset_storage_dir.exists()


def test_rejects_header_only_truncated_image(photo):
    payload = deepcopy(photo.payload)
    raw = base64.b64decode(payload["image"]["data_base64"])[:40]
    payload["image"].update(data_base64=base64.b64encode(raw).decode(), byte_size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    assert submit(photo, payload=payload).status_code == 422


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_supports_verified_jpeg_and_webp(photo, fmt):
    assert submit(photo, payload={"item_id": "pitem_photo", "image": image_payload(fmt=fmt)}).status_code == 202


@pytest.mark.parametrize("mode,expected", [("other_tenant", 404), ("ungranted", 404), ("revoked", 404),
                                         ("inactive", 404), ("teacher", 403), ("no_auth", 401)])
def test_student_ownership_required_before_storage(photo, mode, expected):
    with Session(photo.database.engine) as session, session.begin():
        if mode == "other_tenant":
            photo.client.headers["Authorization"] = "Bearer " + photo.signing.token("subject-b")
        elif mode == "ungranted":
            session.get(PracticeSession, "practice_photo").student_id = "student_ungranted"
        elif mode == "revoked":
            session.scalar(select(StudentGrant).where(StudentGrant.student_id == "student_a")).active = False
        elif mode == "inactive":
            session.get(Student, "student_a").active = False
        elif mode == "teacher":
            session.scalar(select(PrincipalRole).where(PrincipalRole.principal_id == "actor_a")).role = "sitting_teacher"
        else:
            del photo.client.headers["Authorization"]
    assert submit(photo).status_code == expected
    assert not photo.cfg.asset_storage_dir.exists()


@pytest.mark.parametrize("mode", ["foreign_item", "foreign_owner", "foreign_tenant", "unverified", "preview", "not_generated"])
def test_item_must_belong_to_current_generated_variant(photo, mode):
    payload = deepcopy(photo.payload)
    with Session(photo.database.engine) as session, session.begin():
        item, activity = session.get(PracticeItem, "pitem_photo"), session.get(PracticeSession, "practice_photo")
        if mode == "foreign_item":
            payload["item_id"] = "pitem_other"
        elif mode == "foreign_owner":
            item.owner_student_id = "student_ungranted"
        elif mode == "foreign_tenant":
            item.owner_student_id, item.tenant_id = "student_b", "tenant_b"
        elif mode == "unverified":
            item.status = "candidate"
        elif mode == "preview":
            activity.purpose = "preview"
        else:
            activity.gate = {"no_expiry": True}
    assert submit(photo, payload=payload).status_code == 409
    assert not photo.cfg.asset_storage_dir.exists()


def test_idempotency_queue_completed_and_conflict(photo):
    first = submit(photo, key="photo-stable").json()
    assert submit(photo, key="photo-stable").json() == first
    assert submit(photo).json() == first
    changed = {"item_id": "pitem_photo", "image": image_payload("black")}
    assert submit(photo, payload=changed, key="photo-stable").json()["code"] == "idempotency_conflict"
    assert submit(photo, payload=changed).json()["code"] == "assessment_pending"
    assert count(photo, PracticeAssessment) == 1 and count(photo, LearningOutbox) == 1
    assert len(list(photo.cfg.asset_storage_dir.iterdir())) == 1
    assess(photo, first["assessment_id"], Client(readback(), {"result": "incorrect", "feedback": "请核对步骤。"}))
    assert submit(photo, key="photo-stable").json() == first
    assert submit(photo).json()["result"] == "incorrect"
    assert submit(photo, payload=changed).status_code == 409
    assert count(photo, PracticeResponse) == 1


def test_pending_photo_blocks_text_answer_and_reverse(photo):
    queued = submit(photo).json()
    answer = photo.client.post("/v1/practice-sessions/practice_photo/responses", json={"item_id": "pitem_photo", "answer_text": "3+4=7"})
    assert answer.status_code == 409 and answer.json()["code"] == "assessment_pending"
    with Session(photo.database.engine) as session, session.begin():
        row = session.get(PracticeAssessment, queued["assessment_id"])
        row.submission = {"item_id": "pitem_photo", "answer_text": "typed", "selected_options": None, "answers": None}
    assert submit(photo).json()["code"] == "assessment_pending"


@pytest.mark.parametrize("output", [
    ProviderError("upstream_timeout"), RuntimeError("synthetic-photo-secret"),
    readback(item_id="some_other_question"), readback(matches_question=False), readback(answer_status="unreadable"),
    readback(uncertainties=["看不清运算符"]), readback(answer_text=None),
    readback(answer_text="synthetic-photo-secret"), readback(selected_options=["B"]),
])
def test_read_failures_are_retryable_without_student_incorrect(photo, output):
    identifier = submit(photo).json()["assessment_id"]
    client = Client(output)
    assert assess(photo, identifier, client)["status"] == "failed"
    dto = assessment(photo, identifier)
    assert dto["result"] == "unscorable" and dto["retryable"] and dto["solution_text"] is None
    assert len(client.calls) == 1 and count(photo, PracticeResponse) == 0
    with Session(photo.database.engine) as session:
        row = session.get(PracticeAssessment, identifier)
        assert "synthetic-photo-secret" not in json.dumps(row.audit)
        assert session.get(PracticeSession, "practice_photo").status == "active"


@pytest.mark.parametrize("outcome", [ProviderError("upstream_unavailable"), {"result": "unscorable", "feedback": "需重试"}])
def test_failed_semantic_grade_can_explicitly_retry(photo, outcome):
    first = submit(photo, key="photo-first").json()
    assess(photo, first["assessment_id"], Client(readback(), outcome))
    assert assessment(photo, first["assessment_id"])["retryable"]
    assert count(photo, PracticeResponse) == 0
    second = submit(photo, key="photo-retry").json()
    assert second["assessment_id"] != first["assessment_id"]
    assert submit(photo, key="photo-first").json() == first
    result = assess(photo, second["assessment_id"], Client(readback(), {"result": "correct", "feedback": "正确"}))
    assert result == {"status": "completed", "result": "correct"}
    assert count(photo, PracticeResponse) == 1
    assert count(photo, PracticeAssessment) == 2


@pytest.mark.parametrize("mode", ["no_expiry", "expired"])
def test_only_explicit_generated_no_expiry_skips_deadline(photo, mode):
    with Session(photo.database.engine) as session, session.begin():
        activity = session.get(PracticeSession, "practice_photo")
        activity.budget_ends_at = now() - timedelta(days=1)
        activity.gate = {"generation_job_id": "generation_photo", "no_expiry": mode == "no_expiry"}
    response = submit(photo)
    assert response.status_code == (202 if mode == "no_expiry" else 409)
    if mode == "no_expiry":
        assert assess(photo, response.json()["assessment_id"], Client(readback(), {"result": "correct", "feedback": "正确"}))["status"] == "completed"


@pytest.mark.parametrize("mode", ["stale_item", "exited", "inactive", "interrupted", "missing_asset"])
def test_worker_revalidates_scope_and_never_repeats_interrupted_call(photo, mode):
    identifier = submit(photo).json()["assessment_id"]
    with Session(photo.database.engine) as session, session.begin():
        if mode == "stale_item":
            session.get(PracticeItem, "pitem_photo").stem_text = "另一道题"
        elif mode == "exited":
            session.get(PracticeSession, "practice_photo").status = "exited"
        elif mode == "inactive":
            session.get(Student, "student_a").active = False
        elif mode == "missing_asset":
            image = session.get(PracticeAssessment, identifier).submission["photo"]
            LocalAssetStore(photo.cfg.asset_storage_dir).delete(image["asset_id"])
    client = Client()
    result = assess(photo, identifier, client, tries=2 if mode == "interrupted" else 1)
    assert result["status"] == ("failed" if mode in ("interrupted", "missing_asset") else "superseded")
    assert count(photo, PracticeResponse) == 0 and client.calls == []


def test_worker_rechecks_item_after_provider_returns(photo):
    identifier = submit(photo).json()["assessment_id"]
    with Session(photo.database.engine) as session, session.begin():
        row = session.get(PracticeAssessment, identifier)
        job = session.get(LearningOutbox, row.outbox_id)
        def mutate():
            item = session.get(PracticeItem, "pitem_photo")
            item.stem_text = "已更新的题目"
            session.flush()
            return {"result": "correct", "feedback": "正确"}
        result = practice_photo.assess_practice_photo(session, job, SimpleNamespace(client=Client(readback(), mutate)))
        assert result["status"] == "superseded"
    assert count(photo, PracticeResponse) == 0


@pytest.mark.parametrize("stage", ["read", "grade"])
def test_configured_fallback_only_after_primary_failure(photo, stage):
    identifier = submit(photo).json()["assessment_id"]
    if stage == "read":
        primary = Client(ProviderError("upstream_timeout"), {"result": "correct", "feedback": "正确"})
        fallback = Client(readback())
    else:
        primary = Client(readback(), {"result": "unscorable", "feedback": "暂未核验"})
        fallback = Client({"result": "correct", "feedback": "正确"})
    assert assess(photo, identifier, primary, fallback)["result"] == "correct"
    assert len(primary.calls) == 2 and len(fallback.calls) == 1
    if stage == "read":
        assert "参考步骤" not in fallback.calls[0]["text"] and fallback.calls[0]["images"]


@pytest.mark.parametrize("result", ["correct", "incorrect"])
def test_decisive_primary_result_does_not_call_fallback(photo, result):
    identifier = submit(photo).json()["assessment_id"]
    fallback = Client()
    assert assess(photo, identifier, Client(readback(), {"result": result, "feedback": "已核验"}), fallback)["result"] == result
    assert fallback.calls == []


def test_purge_removes_private_files_before_assessments(photo):
    identifier = submit(photo).json()["assessment_id"]
    with Session(photo.database.engine) as session, session.begin():
        identifiers = practice_photo.practice_photo_asset_ids(session, "tenant_a", "student_a")
        assert len(identifiers) == 1
        assert practice_photo.practice_photo_asset_ids(session, "tenant_b", "student_a") == []
        assert session.get(PracticeAssessment, identifier)
        purge_student(session, LocalAssetStore(photo.cfg.asset_storage_dir), "tenant_a", "student_a")
    assert count(photo, PracticeAssessment) == count(photo, LearningOutbox) == 0
    assert not list(photo.cfg.asset_storage_dir.iterdir())


def test_enqueue_failure_cleans_only_new_photo(photo, monkeypatch):
    existing = LocalAssetStore(photo.cfg.asset_storage_dir)
    image = image_payload("black")
    existing.put("asset_unrelated", base64.b64decode(image["data_base64"]), image)
    def fail(*args, **kwargs):
        raise OperationalError("synthetic", {}, Exception("unavailable"))
    monkeypatch.setattr(practice_photo, "enqueue", fail)
    assert submit(photo).status_code == 503
    assert [p.name for p in photo.cfg.asset_storage_dir.iterdir()] == ["asset_unrelated"]
    assert count(photo, PracticeAssessment) == 0


def test_commit_failure_cleans_new_photo(photo, monkeypatch):
    original_commit = Session.commit
    def fail(session):
        if session.scalar(select(PracticeAssessment.id)):
            raise OperationalError("synthetic", {}, Exception("unavailable"))
        return original_commit(session)
    monkeypatch.setattr(Session, "commit", fail)
    assert submit(photo).status_code == 503
    assert not list(photo.cfg.asset_storage_dir.iterdir())
    assert count(photo, PracticeAssessment) == count(photo, LearningOutbox) == 0


@pytest.mark.parametrize("kind,valid", [("choice", True), ("choice", False), ("fill_blank", True), ("fill_blank", False)])
def test_structured_photo_answers_use_current_options_and_fields(photo, kind, valid):
    with Session(photo.database.engine) as session, session.begin():
        item = session.get(PracticeItem, "pitem_photo")
        item.question_kind = kind
        if kind == "choice":
            item.question_type, item.answer_kind, item.answer_values = "single_choice", "choice", ["E"]
            item.options = [{"label": "B", "text": "6"}, {"label": "E", "text": "7"}]
        else:
            item.question_type, item.answer_kind, item.answer_values = "fill_blank", "text", []
            item.stem_text = "3 + 4 = ____。\n4 + 4 = ____。"
            item.solution_text = "第一空：3+4=7；第二空：4+4=8。"
            item.answer_fields = [{"id": "blank_a", "label": "第一空"}, {"id": "blank_b", "label": "第二空"}]
            item.field_answers = [{"field_id": key, "answer_kind": "number", "answer_values": [value]}
                                  for key, value in [("blank_a", "7"), ("blank_b", "8")]]
    output = readback(answer_text=None)
    if kind == "choice":
        output["selected_options"] = ["E" if valid else "A"]
    else:
        output["answers"] = [{"field_id": "blank_a", "answer_text": "14/2"},
                             {"field_id": "blank_b" if valid else "old_blank", "answer_text": "8"}]
    queued = submit(photo)
    assert queued.status_code == 202, queued.text
    identifier = queued.json()["assessment_id"]
    client = Client(output)
    result = assess(photo, identifier, client)
    assert result["status"] == ("completed" if valid else "failed")
    assert result["result"] == ("correct" if valid else "unscorable")
    assert len(client.calls) == 1
    assert count(photo, PracticeResponse) == int(valid)


def test_tombstone_can_delete_photo_after_assessment_records_are_gone(photo):
    submit(photo)
    store = LocalAssetStore(photo.cfg.asset_storage_dir)
    with Session(photo.database.engine) as session, session.begin():
        identifiers = practice_photo.practice_photo_asset_ids(session, "tenant_a", "student_a")
        row = session.scalar(select(PracticeAssessment))
        session.delete(row)
    with Session(photo.database.engine) as session, session.begin():
        addressed = purge_student(session, store, "tenant_a", "student_a", extra_asset_ids=identifiers)
        assert set(identifiers) <= set(addressed)
    assert not list(photo.cfg.asset_storage_dir.iterdir())


@pytest.mark.parametrize("client_type", [ArkClient, UserOriginalArkClient])
def test_real_primary_clients_use_owned_bounded_wrapper(photo, monkeypatch, client_type):
    identifier = submit(photo).json()["assessment_id"]
    requests, wrappers = [], []
    outputs = [readback(), {"result": "correct", "feedback": "正确"}]
    def serve(request):
        requests.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.extensions["timeout"]["read"] <= 35
        return httpx.Response(200, json={"model": "deepseek-v4-1-flash-260910", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(outputs.pop(0))}}]})
    def factory(settings):
        assert settings.total_timeout_seconds == settings.timeout_seconds == 35 and settings.max_attempts == 1
        client = BoundedChatClient(settings, transport=httpx.MockTransport(serve))
        wrappers.append(client)
        return client
    monkeypatch.setattr(practice_generation, "BoundedChatClient", factory)
    settings = ArkSettings(base_url="https://synthetic.invalid/v1", api_key="synthetic", total_timeout_seconds=200)
    with client_type(settings, transport=httpx.MockTransport(lambda _: pytest.fail("unbounded call"))) as primary:
        assert assess(photo, identifier, primary)["result"] == "correct"
        assert not primary._closed and primary.settings.total_timeout_seconds == 200
    assert len(requests) == len(wrappers) == 2 and all(client._closed for client in wrappers)


def test_astra_fallback_preserves_responses_transport_and_timeout(photo, monkeypatch):
    identifier = submit(photo).json()["assessment_id"]
    original_init = ResponsesClient.__init__
    requests, wrappers = [], []
    outputs = [readback(), {"result": "correct", "feedback": "正确"}]
    def serve(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.url.path == "/v1/responses" and body["model"] == "gpt-6-astra"
        assert body["store"] is False and body["max_output_tokens"] == 3072
        assert request.extensions["timeout"]["read"] <= 35
        return httpx.Response(200, json={"id": "resp_synthetic", "status": "completed", "model": "gpt-6-astra",
            "output": [{"type": "message", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": json.dumps(outputs.pop(0))}]}]})
    def initialize(self, settings, **kwargs):
        kwargs["transport"] = httpx.MockTransport(serve)
        original_init(self, settings, **kwargs)
        wrappers.append(self)
    monkeypatch.setattr(ResponsesClient, "__init__", initialize)
    settings = ArkSettings(base_url="https://synthetic.invalid/v1", api_key="test-key", model="gpt-6-astra", total_timeout_seconds=200)
    with ResponsesClient(settings, stream=False) as fallback:
        primary = Client(ProviderError("upstream_unavailable"), ProviderError("upstream_unavailable"))
        assert assess(photo, identifier, primary, fallback)["result"] == "correct"
        assert not fallback._closed and fallback.settings.total_timeout_seconds == 200
    assert len(requests) == 2 and len(wrappers) == 3
    assert all(client._closed for client in wrappers)
