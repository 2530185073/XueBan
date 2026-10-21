"""Complete HTTP/worker/database round trip with real parsers, no network."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_learning import prepare_latest_math_curriculum, learning
from test_two_model_pipeline import answer
from xueban_backend.app import create_app
from xueban_backend.correction_models import Entity, Task
from xueban_backend.learning_models import LearningEvidence
from xueban_backend.learning_reports import build_snapshot
from xueban_backend.models import Student
from xueban_backend.providers.ark import ArkSettings
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def make_client(items, name):
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(200, json={
            "id": "synthetic-response", "model": name,
            "usage": {"prompt_tokens": 11, "completion_tokens": 23, "total_tokens": 34},
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"items": items}, ensure_ascii=False)}}],
        })
    settings = ArkSettings(base_url="https://synthetic.invalid/v1", api_key="synthetic-key-" + name,
                           model=name, max_attempts=1)
    return BoundedChatClient(settings, transport=httpx.MockTransport(serve)), calls


@pytest.mark.parametrize("both_bad", [False, True])
def test_partial_page_persists_service_gap_but_only_valid_two_source_learning_evidence(flow, both_bad):
    prepare_latest_math_curriculum(flow)
    valid = answer("1", stem_text="计算1+1", answer_text="3", correct_answer="2",
                   correction_method="两个1相加得到2。", knowledge_points=["有理数的加减法"])
    other = {**deepcopy(valid), "label": "2", "stem_text": "计算2+2", "answer_text": "5", "correct_answer": "4"}
    malformed = {**deepcopy(other), "answer_text": {"private": "rejected-observation-sentinel"}}
    first, first_calls = make_client([valid, malformed], "synthetic-a")
    second, second_calls = make_client([valid, malformed if both_bad else other], "synthetic-b")
    pipeline = TwoModelWholePagePipeline(first, second, partial_output_enabled=True)
    flow.worker.pipeline = pipeline
    flow.worker.settings = replace(flow.worker.settings, auto_check_after_recognition=True)
    try:
        _, job = flow.recognize()
        assert flow.worker.run_once()
        assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
        assert not flow.worker.run_once()
        learning(flow).drain()
        homework = flow.homework()
        assert len(homework["job_ids"]) == 1
        assert len(homework["case_ids"]) == (1 if both_bad else 2)
        assert "核对服务尚未全部完成" in str(homework)

        fresh_app = create_app(flow.cfg)
        with TestClient(fresh_app) as fresh:
            fresh.headers.update({"Authorization": "Bearer " + flow.signing.token()})
            assert fresh.get(f"/v1/homeworks/{flow.homework_id}").json() == homework
            cases = [fresh.get(f"/v1/cases/{case_id}").json() for case_id in homework["case_ids"]]
            valid_cases = [case for case in cases if case["current_decision"]["verdict"] == "incorrect"]
            assert len(valid_cases) == 1
            pending = [case for case in cases if case["current_decision"]["verdict"] == "unable_to_judge"]
            assert len(pending) == (0 if both_bad else 1)
            if pending:
                decision = pending[0]["current_decision"]
                assert decision["validity"] == "needs_review"
                assert decision["verification"]["status"] == "degraded"
                assert "provider_unavailable" in decision["verification"]["reason_codes"]
                assert "服务暂未完成" in decision["basis"][0]["summary"]
                assert decision["knowledge_points"] == []
                assert decision["correct_answer"] is None
                assert decision["correction_method"] is None
                assert decision.get("answer_checks") is None
        fresh_app.state.engine.dispose()

        with Session(flow.database.engine) as session:
            task = session.get(Task, job["id"])
            executions = task.audit["executions"]
            assert len(executions) == 1
            metadata = executions[0]["metadata"]
            assert len(metadata["attempts"]) == 2
            assert metadata["usage"]["total_tokens"] == 68
            assert metadata["attempts"][0]["code"] == "invalid_output"
            assert "rejected-observation-sentinel" not in json.dumps(task.audit)
            entities = session.scalars(select(Entity).where(Entity.homework_id == flow.homework_id)).all()
            assert all("rejected-observation-sentinel" not in json.dumps(row.document) + json.dumps(row.annotations)
                       for row in entities)
            evidence = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
            assert len(evidence) == 1
            assert evidence[0].document["decision_id"] == valid_cases[0]["current_decision"]["id"]
            snapshot = build_snapshot(session, session.get(Student, "student_a"), date(2026, 10, 16),
                                      date(2026, 10, 17), "daily")
            assert snapshot["school_tasks"]["ai_verdicts"]["incorrect"] == 1
            assert len(snapshot["mistakes"]) == 1
        assert len(first_calls) == len(second_calls) == 1
    finally:
        pipeline.close()
