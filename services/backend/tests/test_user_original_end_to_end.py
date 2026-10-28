"""Original provider -> authenticated HTTP/SQLite worker -> learning, offline."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import socket
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401 -- migrated DB and real upload API
from test_learning import prepare_latest_math_curriculum
from test_learning_mapping_batches import BatchClient
from test_user_original_pipeline import envelope, request_task, responses_envelope
from xueban_backend.correction_models import Entity, Task
from xueban_backend.learning_jobs import LearningWorker
from xueban_backend.learning_models import LearningEvidence, QuestionKnowledgeLink
from xueban_backend.providers.correction_pipeline import CorrectionPipeline
from xueban_backend.providers import user_original
from xueban_backend.providers.user_original import correction_pipeline as original
from xueban_backend.worker import CorrectionWorker


STEMS = {"1": "计算 1 + 1。", "2": "比较 -2 和 3，写出较大的数。"}
GRAPH = "test-bnup-math-g7-latest"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This acceptance test must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.fixture
def original_transport(monkeypatch):
    """Keep the public factory and both real adapters; replace only HTTP I/O."""
    requests = []
    environment = {
        "CORRECTION_MODE": "user_original",
        "ARK_BASE_URL": "https://ds.example.invalid/api/v3",
        "ARK_API_KEY": "synthetic-e2e-ds-key",
        "ZYAI_BASE_URL": "https://astra.example.invalid/v1",
        "ZYAI_API_KEY": "synthetic-e2e-astra-key",
    }

    def handle(request):
        task = request_task(request)
        host = request.url.host
        assert host in {"ds.example.invalid", "astra.example.invalid"}
        if "targets" in task:
            assert host == "astra.example.invalid"
            output = {"readings": [
                {"target": index, "page": 1, "label": label, "stem_text": stem,
                 "answer_text": "3", "source": "handwritten", "answer_slots": [],
                 "uncertainties": [], "question_type": "calculation", "options": [], "blank_count": 0}
                for index, (label, stem) in enumerate(STEMS.items())
            ]}
            stage = "source_read"
        elif "question_label" not in task:
            assert host == "ds.example.invalid" and not requests
            output = {"items": [
                {"page": 1, "label": label, "subject": "数学", "stem_text": stem,
                 "answer_text": "3", "answer_status": "present", "answer_slots": [],
                 "uncertainties": [], "question_type": "calculation", "options": [],
                 "blank_count": 0, "has_diagram": False, "diagram": None}
                for label, stem in STEMS.items()
            ]}
            stage = "recognize"
        else:
            label = task["question_label"]
            assert task["provisional_transcription"]["stem_text"] == STEMS[label]
            assert task["provisional_transcription"]["answer_text"] == "3"
            wrong = label == "1"
            output = {
                "verdict": "incorrect" if wrong else "correct", "reason_code": None,
                "basis_summary": "1 加 1 等于 2。" if wrong else "正数 3 大于负数 -2。",
                "uncertainties": [], "knowledge_points": [],
                "correct_answer": "2" if wrong else None,
                "correction_method": "把两个 1 相加，得到 2。" if wrong else None,
            }
            if host == "astra.example.invalid":
                assert label == "2"
                assert task["primary_candidate_for_independent_review"]["verdict"] == "correct"
            stage = "check"
        requests.append({"host": host, "stage": stage, "task": deepcopy(task)})
        body = envelope(output) if host == "ds.example.invalid" else responses_envelope(output)
        return httpx.Response(200, json=body, headers={"x-request-id": "synthetic-e2e"})

    monkeypatch.setattr(original, "ArkClient", lambda settings: user_original.ArkClient(
        settings, transport=httpx.MockTransport(handle), sleep=lambda _: None))
    monkeypatch.setattr(original, "ResponsesClient", lambda settings: user_original.ResponsesClient(
        settings, transport=httpx.MockTransport(handle), sleep=lambda _: None))
    with CorrectionPipeline.from_env(environment) as pipeline:
        yield SimpleNamespace(pipeline=pipeline, requests=requests)


def get(flow, path):
    response = flow.client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def learning_records(engine):
    """Stable identities and raw documents, not merely an aggregate row count."""
    with Session(engine) as session:
        links = session.scalars(select(QuestionKnowledgeLink).order_by(QuestionKnowledgeLink.id)).all()
        evidence = session.scalars(select(LearningEvidence).order_by(LearningEvidence.id)).all()
        return {
            "links": [(row.id, row.question_id, row.graph_version, row.question_revision,
                       row.knowledge_id, row.source, row.status, row.role,
                       deepcopy(row.observation_bindings)) for row in links],
            "evidence": [(row.id, row.source_key, row.validity, deepcopy(row.document))
                         for row in evidence],
        }


def test_public_factory_dispatches_user_original_without_network(original_transport):
    pipeline = original_transport.pipeline
    assert type(pipeline) is user_original.UserOriginalCorrectionPipeline
    assert type(pipeline.client) is user_original.ArkClient
    assert type(pipeline.fallback_client) is user_original.ResponsesClient
    assert pipeline.uses_original_decision_policy is True
    assert pipeline.client.settings.model == "deepseek-v4-1-flash-260910"
    assert pipeline.fallback_client.settings.model == "gpt-6-astra"
    assert original_transport.requests == []


@pytest.mark.parametrize("database", ["sqlite"], indirect=True)
def test_original_page_checks_learning_and_error_source_are_idempotent(flow, original_transport):
    prepare_latest_math_curriculum(flow)
    worker = CorrectionWorker(flow.database.engine,
                              replace(flow.cfg, auto_check_after_recognition=True),
                              original_transport.pipeline)
    with Session(flow.database.engine) as session:
        schema_before = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert schema_before == "0016_student_registration"

    asset, recognition_job = flow.recognize()
    assert worker.run_once()
    assert get(flow, f"/v1/jobs/{recognition_job['id']}")["status"] == "succeeded"
    homework = flow.homework()
    assert len(homework["question_ids"]) == len(homework["case_ids"]) == 2
    questions = [get(flow, f"/v1/questions/{qid}")["record"] for qid in homework["question_ids"]]
    assert [question["stem_text"] for question in questions] == list(STEMS.values())
    for question in questions:
        assert question["regions"] == [{"asset_id": asset["id"], "x": 0, "y": 0,
            "width": 120, "height": 80, "coordinate_space": "original_pixels"}]
    for case_id in homework["case_ids"]:
        case = get(flow, f"/v1/cases/{case_id}")
        assert case["current_decision"] is None
        assert len(case["check_ids"]) == 1
    with Session(flow.database.engine) as session:
        checks = session.scalars(select(Task).where(Task.homework_id == flow.homework_id,
                                                    Task.kind == "check")).all()
        check_jobs = {task.id for task in checks}
        assert len(check_jobs) == 2 and all(task.status == "queued" for task in checks)
    assert len(original_transport.requests) == 2

    assert worker.run_once()
    assert worker.run_once()
    assert not worker.run_once()
    assert all(get(flow, f"/v1/jobs/{job_id}")["status"] == "succeeded" for job_id in check_jobs)
    bundle = get(flow, f"/v1/homeworks/{flow.homework_id}/detail")
    assert bundle["protocol"] == "homework-detail.v1"
    assert bundle["homework"] == flow.homework()
    assert len(bundle["questions"]) == len(bundle["attempts"]) == len(bundle["checks"]) == 2
    cases = {case["question_id"]: case for case in bundle["case_records"]}
    by_label = {question["label"]: cases[question["question_id"]] for question in questions}
    assert {label: case["current_decision"]["verdict"] for label, case in by_label.items()} == {
        "1": "incorrect", "2": "correct"}
    for case in cases.values():
        assert case == get(flow, f"/v1/cases/{case['id']}")
        attempt = get(flow, f"/v1/attempts/{case['current_attempt_id']}")["record"]
        decision = case["current_decision"]
        assert attempt["answer_text"] == "3" and attempt["recognition_status"] == "ready"
        assert decision["origin"] == "model" and decision["validity"] == "candidate"
        assert decision["confirmation_source"] is decision["confirmed_by"] is decision["confirmed_at"] is None
        assert "verification" not in decision
        assert decision["knowledge_points"] == []
        versions = decision["input_versions"]
        assert versions["question_id"] == case["question_id"]
        assert versions["question_revision"] == case["current_question_revision"]
        assert versions["attempt_id"] == attempt["id"]
        assert versions["attempt_recognition_revision"] == attempt["recognition_revision"]
        worker.contracts.validate("Decision", decision)
    wrong, right = by_label["1"], by_label["2"]
    assert wrong["progress"] == "needs_correction" and right["progress"] == "pending"
    assert wrong["current_decision"]["correct_answer"] == "2"
    assert Counter((request["host"], request["stage"], request["task"].get("question_label"))
                   for request in original_transport.requests) == Counter({
        ("ds.example.invalid", "recognize", None): 1,
        ("astra.example.invalid", "source_read", None): 1,
        ("ds.example.invalid", "check", "1"): 1,
        ("ds.example.invalid", "check", "2"): 1,
        ("astra.example.invalid", "check", "2"): 1,
    })
    assert flow.pipeline.calls == []  # The SyntheticPipeline from flow was never used.

    # Empty model labels prevent the correction worker's synchronous shortcut.
    # These links must be created by the real LearningWorker and batch mapper.
    assert learning_records(flow.database.engine) == {"links": [], "evidence": []}

    def map_comparison(rows, payload):
        assert {question["question_id"] for question in payload["questions"]} == set(cases)
        for row in rows:
            if row["question_id"] == right["question_id"]:
                row["reason"] = "本题要求比较一个负数和一个正数。"
                row["links"][0].update(knowledge_id="kp-rational-compare",
                    rationale="题干明确要求比较两个有理数的大小，选出其中较大的数。")

    mapper = BatchClient(map_comparison)
    learning = LearningWorker(flow.database.engine, client=mapper)
    learning.drain()
    assert len(mapper.calls) == 1
    overview = get(flow, "/v1/students/student_a/knowledge")
    assert overview["projection"]["pending"] is False
    states = {state["knowledge_id"]: state for state in overview["states"]}
    assert set(states) == {"kp-rational-add-sub", "kp-rational-compare"}
    error_state = states["kp-rational-add-sub"]
    assert error_state["learning_status"] == "needs_help_now"
    assert error_state["observed_error_count"] == error_state["model_evidence_count"] == 1
    assert error_state["staff_verified_evidence_count"] == 0
    assert states["kp-rational-compare"]["observed_error_count"] == 0

    records = learning_records(flow.database.engine)
    assert len(records["links"]) == len(records["evidence"]) == 2
    for _, question_id, graph, revision, _, source, status, role, bindings in records["links"]:
        case = cases[question_id]
        decision = case["current_decision"]
        assert graph == GRAPH and revision == case["current_question_revision"]
        assert (source, status, role) == ("model", "confirmed", "primary")
        assert bindings == [{"attempt_id": case["current_attempt_id"],
            "attempt_recognition_revision": decision["input_versions"]["attempt_recognition_revision"],
            "decision_id": decision["id"], "basis": "decision"}]
    for _, _, validity, document in records["evidence"]:
        assert validity == "valid" and document["source_role"] == "model"
        assert document["decision_id"] == cases[document["question_id"]]["current_decision"]["id"]

    knowledge_detail = get(flow, "/v1/students/student_a/knowledge/kp-rational-add-sub")
    [error_evidence] = knowledge_detail["evidence"]
    assert error_evidence["case_id"] == wrong["id"]
    assert error_evidence["question_text"] == STEMS["1"]
    assert error_evidence["source_role"] == "model"

    sources = get(flow, "/v1/students/student_a/variant-practice")
    [group] = sources["groups"]
    # This acceptance checks source availability, not variant generation or its
    # topic grouping. The current overview can use auto-topic when it reads
    # links without the attempt/decision binding; knowledge binding is checked
    # strictly above through both the DB and the knowledge API.
    assert group["error_count"] == 1 and group["source_case_id"] == wrong["id"]
    [source] = group["questions"]
    assert source["case_id"] == wrong["id"] and source["question_id"] == wrong["question_id"]
    assert source["question_text"] == STEMS["1"] and source["correct_answer"] == "2"

    # Different idempotency keys still reuse each completed check, including
    # after a worker restart; learning rescans preserve the exact evidence IDs.
    for case in cases.values():
        assert flow.start_check(case)["id"] in check_jobs
    assert not CorrectionWorker(flow.database.engine, worker.settings, original_transport.pipeline).run_once()
    learning.drain()
    LearningWorker(flow.database.engine, client=mapper).drain()
    assert len(mapper.calls) == 1 and len(original_transport.requests) == 5
    assert learning_records(flow.database.engine) == records
    assert get(flow, "/v1/students/student_a/variant-practice")["groups"] == sources["groups"]
    with Session(flow.database.engine) as session:
        entities = session.scalars(select(Entity).where(Entity.homework_id == flow.homework_id)).all()
        assert Counter(row.kind for row in entities) == {
            "question": 2, "attempt": 2, "case": 2, "check": 2, "decision": 2}
        assert len(session.scalars(select(Task).where(Task.homework_id == flow.homework_id)).all()) == 3
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == schema_before
