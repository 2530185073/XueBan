"""No model calls: a skipped alignment cannot create a billed task or evidence."""
from datetime import date
import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_learning import prepare_latest_math_curriculum, learning
from test_partial_output_persistence import make_client
from test_two_model_pipeline import answer
from test_whole_page_pipeline import FakeClient
from xueban_backend.app import create_app
from xueban_backend.correction_models import Task
from xueban_backend.learning_models import LearningEvidence
from xueban_backend.learning_reports import build_snapshot
from xueban_backend.models import Student
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def test_unusable_alignment_is_not_dispatched_or_billed_and_does_not_change_learning_scope(flow):
    prepare_latest_math_curriculum(flow)
    known = answer("1", stem_text="计算1+1", answer_text="3", correct_answer="2",
                   correction_method="两个1相加得到2。", knowledge_points=["有理数的加减法"])
    primary, primary_calls = make_client([known, {**known, "label": "2", "stem_text": "计算2+2"}], "synthetic-a")
    secondary, secondary_calls = make_client([known, {**known, "label": "3", "stem_text": "计算3+3"}], "synthetic-b")
    forbidden = FakeClient("synthetic-unused-aligner", RuntimeError("No compatible printed edge"))
    pipeline = TwoModelWholePagePipeline(primary, secondary, reconciliation_client=forbidden)
    flow.worker.pipeline = pipeline
    try:
        _, job = flow.recognize()
        assert flow.worker.run_once() and not flow.worker.run_once()
        result = flow.client.get(f"/v1/jobs/{job['id']}").json()
        assert result["status"] == "succeeded" and "alignment_dispatches" not in json.dumps(result)
        learning(flow).drain()
        homework = flow.homework()
        assert len(homework["job_ids"]) == 1 and len(homework["case_ids"]) == 3
        fresh_app = create_app(flow.cfg)
        with TestClient(fresh_app) as fresh:
            fresh.headers.update({"Authorization": "Bearer " + flow.signing.token()})
            assert fresh.get(f"/v1/homeworks/{flow.homework_id}").json() == homework
            cases = [fresh.get(f"/v1/cases/{identity}").json() for identity in homework["case_ids"]]
        fresh_app.state.engine.dispose()
        known_cases = [case for case in cases if case["current_decision"]["verdict"] == "incorrect"]
        pending = [case for case in cases if case["current_decision"]["verdict"] == "unable_to_judge"]
        assert len(known_cases) == 1 and len(pending) == 2
        for case in pending:
            decision = case["current_decision"]
            assert decision["validity"] == "needs_review" and decision["knowledge_points"] == []
            assert "unmatched_label" in decision["verification"]["reason_codes"]
            assert "provider_unavailable" not in decision["verification"]["reason_codes"]
        with Session(flow.database.engine) as session:
            task = session.get(Task, job["id"])
            execution, = task.audit["executions"]
            audit = execution["candidate"]["multimodel_audit"]
            dispatch, = audit["alignment_dispatches"]
            assert dispatch["decision"] == "not_dispatched" and dispatch["reason"] == "no_eligible_printed_edge"
            assert dispatch["candidate_edges"] == 1 and dispatch["compatible_edges"] == 0
            assert [call["stage"] for call in audit["calls"]] == ["primary", "secondary"]
            assert len(execution["metadata"]["attempts"]) == 2
            assert execution["metadata"]["usage"]["total_tokens"] == 68
            evidence = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
            assert len(evidence) == 1 and evidence[0].document["decision_id"] == known_cases[0]["current_decision"]["id"]
            snapshot = build_snapshot(session, session.get(Student, "student_a"), date(2026, 10, 16),
                                      date(2026, 10, 17), "daily")
            assert snapshot["school_tasks"]["ai_verdicts"]["incorrect"] == 1 and len(snapshot["mistakes"]) == 1
        assert len(primary_calls) == len(secondary_calls) == 1 and not forbidden.calls
    finally:
        pipeline.close()
