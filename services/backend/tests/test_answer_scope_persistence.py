"""Actual HTTP/worker/storage scope isolation, with synthetic transport only."""
from datetime import date
import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import prepare_latest_math_curriculum, learning
from test_partial_output_persistence import make_client
from test_two_model_pipeline import answer, correct
from test_whole_page_pipeline import FakeClient
from xueban_backend.app import create_app
from xueban_backend.correction_models import Entity, Task
from xueban_backend.learning_models import LearningEvidence
from xueban_backend.learning_reports import build_snapshot
from xueban_backend.models import Student
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def test_incomplete_answer_scope_stays_ai_work_after_refresh_without_learning_or_staff_task(flow):
    prepare_latest_math_curriculum(flow)
    incomplete = correct("1(3)", stem_text="(-18)÷9=____(18÷9)=____", answer_text="2",
                         basis_summary="(-18)÷9=-2；末空应填2。", knowledge_points=["有理数的乘除法"])
    valid = answer("2", stem_text="计算1+1", answer_text="3", correct_answer="2",
                   correction_method="两个1相加得到2。", knowledge_points=["有理数的加减法"])
    primary, primary_calls = make_client([incomplete, valid], "synthetic-scope-a")
    secondary, secondary_calls = make_client([incomplete, valid], "synthetic-scope-b")
    unused = FakeClient("synthetic-unused-scope", RuntimeError("No consumable scope recovery protocol"))
    pipeline = TwoModelWholePagePipeline(primary, secondary, reconciliation_client=unused,
                                        visual_client=unused, judgment_client=unused)
    flow.worker.pipeline = pipeline
    try:
        _, job = flow.recognize()
        assert flow.worker.run_once() and not flow.worker.run_once()
        result = flow.client.get(f"/v1/jobs/{job['id']}").json()
        assert result["status"] == "succeeded"
        learning(flow).drain()
        homework = flow.homework()
        assert len(homework["job_ids"]) == 1 and len(homework["case_ids"]) == 2
        fresh_app = create_app(flow.cfg)
        with TestClient(fresh_app) as fresh:
            fresh.headers.update({"Authorization": "Bearer " + flow.signing.token()})
            assert fresh.get(f"/v1/homeworks/{flow.homework_id}").json() == homework
            cases = [fresh.get(f"/v1/cases/{identity}").json() for identity in homework["case_ids"]]
        fresh_app.state.engine.dispose()
        pending, = [case for case in cases if case["current_decision"]["verdict"] == "unable_to_judge"]
        wrong, = [case for case in cases if case["current_decision"]["verdict"] == "incorrect"]
        decision = pending["current_decision"]
        assert decision["validity"] == "needs_review" and decision["knowledge_points"] == []
        assert "target_scope_unverified" in decision["verification"]["reason_codes"]
        assert "visual_unreadable" not in decision["verification"]["reason_codes"]
        assert "范围" in decision["basis"][0]["summary"]
        assert all(word not in decision["basis"][0]["summary"] for word in ("看不清", "老师", "字迹"))
        assert decision["correct_answer"] is None and decision["correction_method"] is None
        assert decision.get("answer_checks") is None
        with Session(flow.database.engine) as session:
            attempt = session.get(Entity, pending["current_attempt_id"]).document
            question = session.get(Entity, pending["question_id"]).document
            assert attempt["answer_text"] == "2" and attempt["answer_status"] == "present"
            assert attempt["recognition_status"] == "needs_revision"
            assert question["stem_text"] == incomplete["stem_text"]
            task = session.get(Task, job["id"])
            execution, = task.audit["executions"]
            assert [call["stage"] for call in execution["candidate"]["multimodel_audit"]["calls"]] == ["primary", "secondary"]
            assert len(execution["metadata"]["attempts"]) == 2
            assert execution["metadata"]["usage"]["total_tokens"] == 68
            assert "scope_resolution_unavailable" in json.dumps(execution["candidate"]["multimodel_audit"])
            evidence = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
            assert len(evidence) == 1 and evidence[0].document["decision_id"] == wrong["current_decision"]["id"]
            snapshot = build_snapshot(session, session.get(Student, "student_a"), date(2026, 10, 16),
                                      date(2026, 10, 17), "daily")
            assert snapshot["school_tasks"]["ai_verdicts"]["incorrect"] == 1 and len(snapshot["mistakes"]) == 1
        teacher(flow)
        assert flow.client.get("/v1/review-queue").json()["items"] == []
        all_work = flow.client.get("/v1/review-queue?view=all").json()["items"]
        assert next(row for row in all_work if row["case_id"] == pending["id"])["reason"] == "ai_pending"
        assert len(primary_calls) == len(secondary_calls) == 1 and not unused.calls
    finally:
        pipeline.close()
