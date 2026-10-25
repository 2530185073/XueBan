"""Scope-gated single-mode checks persist without claiming a model call."""
from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401 -- shared real-DB fixture
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Entity, Task
from xueban_backend.learning_models import StudentLearningProfile
from xueban_backend.providers import ArkClient, ArkSettings
from xueban_backend.providers.correction_pipeline import CorrectionPipeline
from xueban_backend.providers.responses import ResponsesClient
from xueban_backend.worker import CorrectionWorker


@pytest.mark.parametrize(
    ("grade", "subject", "scope_status"),
    [(None, "数学", "unsupported_scope"), (7, None, "unresolved_subject")],
    ids=["missing-grade", "unresolved-subject"],
)
def test_scope_gate_persists_no_call_candidate(flow, grade, subject, scope_status):
    case = flow.ready()
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "subject": subject}
        if grade is not None:
            session.add(StudentLearningProfile(
                student_id="student_a", tenant_id="tenant_a", revision=1,
                document={"grade": grade, "term": None, "textbook_revision": None,
                          "current_knowledge_ids": [], "note": None},
                updated_by="actor_a", updated_at=now(),
            ))
        session.commit()

    def reject_request(_request):
        pytest.fail("Scope-gated checks must not send provider requests")

    settings = ArkSettings(
        base_url="https://synthetic.example.invalid/v1",
        api_key="synthetic-scope-test-key", model="synthetic-primary", max_attempts=1,
    )
    primary = ArkClient(settings, transport=httpx.MockTransport(reject_request))
    fallback = ResponsesClient(
        replace(settings, model="synthetic-fallback"),
        transport=httpx.MockTransport(reject_request),
    )
    primary.generate = Mock(wraps=primary.generate)
    fallback.generate = Mock(wraps=fallback.generate)
    job = flow.start_check(case)
    with CorrectionPipeline(primary, fallback) as pipeline:
        pipeline.check = Mock(wraps=pipeline.check)
        worker = CorrectionWorker(flow.database.engine, flow.cfg, pipeline)
        assert worker.run_once()
        pipeline.check.assert_called_once()
        context = pipeline.check.call_args.kwargs
        assert context["student_grade"] == grade
        assert context["curriculum_context"]["scope"] is None
        assert context["curriculum_context"]["status"] == scope_status
        primary.generate.assert_not_called()
        fallback.generate.assert_not_called()

    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        assert task.status == "succeeded"
        assert len(task.audit["executions"]) == 1
        execution = task.audit["executions"][0]
        assert execution["outcome"] == "succeeded"
        assert "failure_code" not in execution
        metadata = execution["metadata"]
        assert metadata["requested_model"] == ""
        assert metadata["model"] is None
        assert metadata["latency_ms"] == 0
        assert metadata["attempts"] == []
        assert metadata["usage"] is None
        assert metadata["request_id"] is metadata["response_id"] is None
        candidate = execution["candidate"]
        assert candidate["verdict"] == "unable_to_judge"
        assert candidate["reason_code"] == "needs_staff"
        assert candidate["evidence_verified"] is False

        check = session.get(Entity, task.inputs["check_id"])
        assert check.document["model_version"] is None
        decisions = list(session.scalars(select(Entity).where(
            Entity.homework_id == flow.homework_id, Entity.kind == "decision",
        )))
        assert len(decisions) == 1
        decision = decisions[0].document
        assert check.document["decision_ids"] == [decision["id"]]
        assert decision["verdict"] == "unable_to_judge"
        assert decision["validity"] == "needs_review"
        assert decision["knowledge_points"] == []
        assert decision["confirmed_by"] is decision["confirmation_source"] is None
        stored_case = session.get(Entity, case["id"]).document
        assert stored_case["current_decision"]["id"] == decision["id"]
        assert stored_case["progress"] == "pending"
        worker.contracts.validate("CheckRun", check.document)
        worker.contracts.validate("Decision", decision)


@pytest.mark.parametrize(
    ("returned_model", "expected_model"),
    [("synthetic-returned", "synthetic-returned"), (None, "synthetic-requested")],
    ids=["returned-model", "requested-model-fallback"],
)
def test_check_preserves_available_model_provenance(flow, returned_model, expected_model):
    case = flow.ready()
    flow.pipeline.metadata = replace(
        flow.pipeline.metadata, model=returned_model, requested_model="synthetic-requested",
    )
    job = flow.start_check(case)
    assert flow.worker.run_once()
    with Session(flow.database.engine) as session:
        task = session.get(Task, job["id"])
        assert task.status == "succeeded"
        check = session.get(Entity, task.inputs["check_id"])
        assert check.document["model_version"] == expected_model
        metadata = task.audit["executions"][0]["metadata"]
        assert metadata["model"] == returned_model
        assert metadata["requested_model"] == "synthetic-requested"
