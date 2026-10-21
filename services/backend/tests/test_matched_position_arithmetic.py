"""Matched two-reader downgrades; synthetic outputs and isolated DB only."""
from copy import deepcopy
import pytest
from sqlalchemy.orm import Session
from test_correction_flow import flow  # noqa: F401
from test_partial_output_persistence import make_client
from test_position_arithmetic import STEM, ANSWER
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.correction_models import Task
from xueban_backend.providers.matched_position_arithmetic import check_matched_positions
from xueban_backend.providers.position_arithmetic import check_position_arithmetic
from xueban_backend.providers.reconciliation import source_id
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def source(**changes):
    return correct(changes.pop("label", "1(3)"), **{"stem_text": STEM, "answer_text": ANSWER,
        "knowledge_points": [], "correct_answer": None, "correction_method": None, **changes})


def pipeline(a=None, b=None):
    return TwoModelWholePagePipeline(FakeClient("first", {"items": [a or source()]}),
        FakeClient("second", {"items": [b or source()]}),
        visual_client=FakeClient("visual", RuntimeError("no extra visual")),
        reconciliation_client=FakeClient("review", RuntimeError("no extra review")))


def test_two_matched_correct_judges_downgrade_only_proved_wrong_positions():
    a, b = source(), source(); before = deepcopy((a, b)); p = pipeline(a, b)
    result = p.recognize(images=(picture(),)); row = result.data["items"][0]
    assert row["recognition_status"] == "ready" and row["assessment"]["verification"]["status"] == "matched"
    assert row["assessment"]["verdict"] == "partially_correct"
    assert row["answer_text"] == ANSWER and row["stem_text"] == STEM
    assert [(c["student_answer"], c["verdict"], c["correct_answer"]) for c in row["assessment"]["answer_checks"]] == [
        ("－", "correct", None), ("2", "incorrect", "-2")]
    assert len(result.metadata.attempts) == 2 and not p.visual_client.calls and not p.reconciliation_client.calls
    audit = result.data["multimodel_audit"]["matched_position_arithmetic"][0]
    assert audit["previous_verdict"] == "correct" and audit["applied_verdict"] == "partially_correct"
    assert [s["source_id"] for s in audit["sources"]] == [source_id("primary", a), source_id("secondary", b)]
    assert all(s["calculation"]["authority"] == "bounded_arithmetic_after_matched_dual_source_gates"
               for s in audit["sources"])
    for s in audit["sources"]:
        assert s["observation"]["answer_text"] == ANSWER
        for pos in s["calculation"]["positions"]:
            for field in ("printed_segment", "placeholder", "student_answer"):
                ref = pos[field]
                assert s["observation"][ref["field"]][ref["start"]:ref["end"]] == ref["text"]
    assert [call["items"][0]["verdict"] for call in result.data["multimodel_audit"]["calls"]] == ["correct", "correct"]
    audit["sources"][0]["observation"]["answer_text"] = "changed result audit"
    assert (a, b) == before and row["answer_text"] == ANSWER
    assert result.data["multimodel_audit"]["calls"][0]["items"][0]["answer_text"] == ANSWER


def test_all_slots_wrong_can_downgrade_to_incorrect():
    a = source(answer_text="第一空：+；第二空：2。")
    result = pipeline(a, deepcopy(a)).recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "incorrect"
    assert [c["correct_answer"] for c in result.data["items"][0]["assessment"]["answer_checks"]] == ["-", "-2"]


def test_one_supported_source_cannot_downgrade_matched_pair():
    a = source(answer_text="第一空：－；第二空：+2。")
    b = {**a, "answer_text": "第一空：－；第二空：+" + " " * 80 + "2。"}
    assert check_position_arithmetic(a, source_id=source_id("primary", a)) is not None
    assert check_position_arithmetic(b, source_id=source_id("secondary", b)) is None
    assert check_matched_positions(a, b) is None
    result = pipeline(a, b).recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "correct"
    assert "matched_position_arithmetic" not in result.data["multimodel_audit"]
    assert len(result.metadata.attempts) == 2


@pytest.mark.parametrize("b", [
    source(answer_text="第一空：+；第二空：2。"),
    source(answer_text="第一空：－；第二空：3。"),
    source(answer_text="第一空：-；第二空：2。"),
    source(stem_text="计算：(-14)÷7=____(14÷7)=____。"),
    source(label="other"),
    source(target_unit="question"),
    source(uncertainties=["末空不清"]),
    source(answer_status="unclear"),
])
def test_different_raw_positions_structures_or_unresolved_sources_are_noop(b):
    a = source(); before = deepcopy((a, b))
    assert check_matched_positions(a, b) is None and (a, b) == before


@pytest.mark.parametrize("verdict", ["correct", "incorrect", "partially_correct"])
def test_all_correct_does_not_promote_or_overwrite_old_judgment(verdict):
    a = source(answer_text="第一空：－；第二空：-2。", verdict=verdict,
        correct_answer="old correction", correction_method="old method")
    result = pipeline(a, deepcopy(a)).recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == verdict
    assert "matched_position_arithmetic" not in result.data["multimodel_audit"]


def test_two_fully_supported_but_different_conclusions_keep_existing_conflict():
    a = source(); b = source(answer_text="第一空：+；第二空：2。")
    assert check_position_arithmetic(a, source_id=source_id("primary", a))["verdict"] == "partially_correct"
    assert check_position_arithmetic(b, source_id=source_id("secondary", b))["verdict"] == "incorrect"
    assert check_matched_positions(a, b) is None
    p = TwoModelWholePagePipeline(FakeClient("first", {"items": [a]}), FakeClient("second", {"items": [b]}))
    result = p.recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "unable_to_judge"
    assert "matched_position_arithmetic" not in result.data["multimodel_audit"] and len(result.metadata.attempts) == 2


def test_two_pages_have_separate_source_bindings_and_no_shared_mutation():
    p = pipeline(); result = p.recognize(images=(picture("asset_one"), picture("asset_two")))
    audits = result.data["multimodel_audit"]["matched_position_arithmetic"]
    assert [a["asset_id"] for a in audits] == ["asset_one", "asset_two"] and len(result.metadata.attempts) == 4
    audits[0]["sources"][0]["calculation"]["positions"][0]["student_answer"]["text"] = "edited"
    assert audits[1]["sources"][0]["calculation"]["positions"][0]["student_answer"]["text"] == "－"
    assert result.data["items"][1]["assessment"]["answer_checks"][0]["student_answer"] == "－"


def test_partial_page_retains_existing_matched_valid_row_without_new_arithmetic():
    valid = source(); malformed = {**valid, "label": "bad", "answer_text": {"bad": "shape"}}
    a, acalls = make_client([valid, malformed], "first"); b, bcalls = make_client([valid, malformed], "second")
    p = TwoModelWholePagePipeline(a, b, partial_output_enabled=True)
    try:
        result = p.recognize(images=(picture(),))
        assert result.data["items"][0]["assessment"]["verdict"] == "correct"
        assert "matched_position_arithmetic" not in result.data["multimodel_audit"]
        assert len(acalls) == len(bcalls) == 1
    finally: p.close()


def test_real_mock_http_worker_persists_downgrade_and_only_two_attempts(flow):
    a, acalls = make_client([source()], "first"); b, bcalls = make_client([source()], "second")
    p = TwoModelWholePagePipeline(a, b); flow.worker.pipeline = p
    try:
        _, job = flow.recognize(); assert flow.worker.run_once()
        assert flow.client.get("/v1/jobs/" + job["id"]).json()["status"] == "succeeded"
        case = flow.client.get("/v1/cases/" + flow.homework()["case_ids"][0]).json()
        decision = flow.client.get("/v1/decisions/" + case["current_decision"]["id"]).json()["record"]
        attempt = flow.client.get("/v1/attempts/" + case["current_attempt_id"]).json()["record"]
        assert decision["verdict"] == "partially_correct" and decision["validity"] == "candidate"
        assert case["progress"] == "needs_correction" and attempt["answer_text"] == ANSWER
        assert decision["answer_checks"][0]["correct_answer"] is None
        assert decision["answer_checks"][1]["correct_answer"] == "-2"
        assert decision["knowledge_points"] == [] and len(acalls) == len(bcalls) == 1
        with Session(flow.database.engine) as session:
            execution = session.get(Task, job["id"]).audit["executions"][-1]
            assert len(execution["metadata"]["attempts"]) == 2
            audit = execution["candidate"]["multimodel_audit"]["matched_position_arithmetic"][0]
            assert len(audit["sources"]) == 2 and audit["additional_model_calls"] == 0
        assert flow.client.get("/v1/decisions/" + decision["id"]).json()["record"] == decision
    finally: p.close()
