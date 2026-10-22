"""Synthetic bound-position arithmetic; not an image-accuracy benchmark."""
from copy import deepcopy
import json
import pytest
from test_correction_flow import flow  # noqa: F401
from test_reconciliation import identity_evidence, review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers import reconciliation
from xueban_backend.providers.position_arithmetic import check_position_arithmetic
from xueban_backend.providers.two_model import TwoModelWholePagePipeline

STEM = "计算：(－14)÷7=____(14÷7)=____。"
ANSWER = "第一空：－；第二空：2。"


def observed(stem=STEM, answer=ANSWER, **changes):
    return {"label": "1(3)", "stem_text": stem, "answer_text": answer,
            "answer_status": "present", "uncertainties": [], "options": [], **changes}


def checked(source):
    return check_position_arithmetic(source, source_id=reconciliation.source_id("visual", source))


def test_each_printed_position_is_evaluated_without_moving_the_first_minus():
    source = observed(); original = deepcopy(source); result = checked(source)
    assert result["verdict"] == "partially_correct"
    assert [(c["student_answer"], c["verdict"], c["correct_answer"]) for c in result["answer_checks"]] == [
        ("－", "correct", None), ("2", "incorrect", "-2")]
    assert source == original and result["audit"]["observation_rewritten"] is False
    for position in result["audit"]["positions"]:
        for name in ("printed_segment", "placeholder", "student_answer"):
            ref = position[name]
            assert source[ref["field"]][ref["start"]:ref["end"]] == ref["text"]
    assert result["audit"]["positions"][0]["student_answer"]["text"] != "-1"


def test_explicit_numbers_choose_positions_not_occurrence_order_and_spans_do_not_alias():
    result = checked(observed(answer="第二空：2；第一空：-。"))
    assert [c["student_answer"] for c in result["answer_checks"]] == ["-", "2"]
    assert result["audit"]["positions"][0]["student_answer"]["start"] > result["audit"]["positions"][1]["student_answer"]["start"]
    result = checked(observed("3=____=____", "第1空：2；第2空：2"))
    assert result["verdict"] == "incorrect"
    refs = [p["student_answer"] for p in result["audit"]["positions"]]
    assert refs[0]["text"] == refs[1]["text"] == "2" and refs[0]["end"] < refs[1]["start"]


@pytest.mark.parametrize("stem,answer,expected", [
    (STEM, "第一空：+；第二空：2", ["-", "-2"]),
    ("计算：0.1+0.2=____=____。", "第1空：.3；第2空：.4", [None, "3/10"]),
    ("求值：(-4)÷(-3/7)÷(4/3)=____=____", "第1空：7；第2空：8", [None, "7"]),
    ("２／３＝＿＿＿＿＝＿＿＿＿", "第２空：１／３；第１空：２／３", [None, "2/3"]),
    ("-7=____((3)+(4))=____", "第1空：-；第2空：7", [None, "-7"]),
])
def test_exact_bounded_constants_and_supported_typography(stem, answer, expected):
    assert [c["correct_answer"] for c in checked(observed(stem, answer))["answer_checks"]] == expected


@pytest.mark.parametrize("stem,answer", [
    (STEM, "第一空：－；第二空：-2"),
    ("5/2=____=____", "第1空：2 1/2；第2空：2.5"),
    ("12=____=____", "第1空：1 2；第2空：12"),
    ("2²=____=____", "第1空：22；第2空：4"),
    ("2=____=____", "第1空：²；第2空：4"),
    ("½=____=____", "第1空：1/2；第2空：2"),
    ("(-5)=____(2)+(3)=____", "第1空：-；第2空：5"),
    ("5/2=____=____", "第1空：2½；第2空：2.5"),
    ("计算：2x=____=____", "第1空：2；第2空：3"),
    ("2cm=____=____", "第1空：2；第2空：3"),
    ("取倒数：2=____=____", "第1空：2；第2空：3"),
    ("计算：2=____=____，保留一位小数", "第1空：2；第2空：3"),
    ("2**2=____=____", "第1空：4；第2空：3"),
    ("2(3)=____=____", "第1空：6；第2空：3"),
    ("4÷2/3=____=____", "第1空：2；第2空：3"),
    ("1/0=____=____", "第1空：2；第2空：3"),
    ("1.0000001=____=____", "第1空：2；第2空：3"),
    ("9999999999999=____=____", "第1空：2；第2空：3"),
    ("2=____+____=____", "第1空：2；第2空：3"),
    ("2=____ ____=____", "第1空：2；第2空：3"),
    ("2=1+____=____", "第1空：2；第2空：3"),
    ("2=____(3)=____", "第1空：-；第2空：3"),
    (STEM, "第一空：-；第一空：2"), (STEM, "第一空：-"), (STEM, "-；2"),
    (STEM, "第一空：-；第二空：2；所以正确"), ("2=____", "第一空：3"),
    ("(" * 70 + "2" + ")" * 70 + "=____=____", "第1空：2；第2空：3"),
])
def test_unsupported_and_wholly_correct_are_noops(stem, answer):
    source = observed(stem, answer); original = deepcopy(source)
    assert checked(source) is None and source == original


@pytest.mark.parametrize("change", [
    {"target_unit": "question"}, {"label": "1(3)#1"}, {"label": "1(3)＃１"},
    {"uncertainties": ["笔迹不明"]}, {"answer_status": "unclear"},
    {"options": [{"label": "A", "text": "2"}]},
])
def test_other_protocols_or_unresolved_readings_are_not_arithmetic_authority(change):
    assert checked(observed(**change)) is None


def synthetic_flow(*, answer=ANSWER, verdict="correct", missing_identity=False,
                   bad_source=False, visual_unclear=False, bad_quote=False):
    # The complete identity evidence here is synthetic, never historical backfill.
    locator = {"printed_label": "1(3)", "page_zone": "top_left"}
    first = correct("1(3)", stem_text="计算：(-14)÷7", answer_text="-(14÷7)=2", verdict="incorrect", visible_locator=locator)
    second = correct("1(3)", stem_text="计算：(3)(-14)÷7=-(14÷7)=____", answer_text="填2", visible_locator=locator)
    visual = observed(answer=answer, visible_locator=locator,
        **({"answer_status": "unclear", "uncertainties": ["末空不清"]} if visual_unclear else {}))
    def adjudicate(request):
        target = json.loads(request["text"])["task_input"]["items"][0]; selected = target["visual"]
        proposal = review(label="1(3)", source=selected, selected_source="visual", resolution="visual_resolution",
            verdict=verdict, correct_answer="旧复核标准" if verdict == "incorrect" else None,
            correction_method="旧复核方法" if verdict == "incorrect" else None)
        if not missing_identity: proposal["target_identity"] = identity_evidence(target["primary"], selected)
        if bad_source: proposal["source_id"] = "visual:" + "0" * 64
        if bad_quote: proposal["judgment_basis"]["condition_quotes"][0]["text"] = "8+9"
        return {"items": [proposal]}
    return TwoModelWholePagePipeline(FakeClient("first", {"items": [first]}),
        FakeClient("second", {"items": [second]}), visual_client=FakeClient("visual", {"items": [visual]}),
        reconciliation_client=FakeClient("review", adjudicate))


def application(result):
    return [a for call in result.data["multimodel_audit"]["calls"] for a in call.get("applications", [])][0]


def test_fully_bound_synthetic_visual_review_correct_is_repaired_without_new_calls():
    flow = synthetic_flow(); result = flow.recognize(images=(picture(),)); row = result.data["items"][0]
    assert row["recognition_status"] == "ready", application(result)
    assert row["assessment"]["verdict"] == "partially_correct"
    assert row["answer_text"] == ANSWER and row["stem_text"] == STEM
    assert row["assessment"]["answer_checks"][1]["correct_answer"] == "-2"
    assert len(result.metadata.attempts) == 4
    assert [len(c.calls) for c in (flow.client, flow.review_client, flow.visual_client, flow.reconciliation_client)] == [1] * 4
    review_call = result.data["multimodel_audit"]["calls"][-1]
    assert review_call["items"][0]["verdict"] == "correct" and review_call["items"][0]["answer_text"] == ANSWER
    audit = application(result)["position_arithmetic"]
    assert audit["previous_review_verdict"] == "correct" and audit["applied_verdict"] == "partially_correct"
    assert audit["source_id"] == review_call["items"][0]["source_id"]


@pytest.mark.parametrize("changes,reason", [
    ({"missing_identity": True}, "target_identity_unconfirmed"),
    ({"bad_source": True}, "source_id_mismatch"),
    ({"bad_quote": True}, "condition_quote_not_bound"),
    ({"visual_unclear": True}, "visual_reading_unclear"),
])
def test_original_gates_are_not_bypassed(changes, reason):
    result = synthetic_flow(**changes).recognize(images=(picture(),)); row = result.data["items"][0]
    assert row["assessment"]["verdict"] == "unable_to_judge"
    assert application(result)["rejection_code"] == reason
    assert "position_arithmetic" not in application(result)
    assert "answer_checks" not in row["assessment"]


def test_arithmetic_all_correct_does_not_upgrade_an_incorrect_review():
    result = synthetic_flow(answer="第一空：-；第二空：-2。", verdict="incorrect").recognize(images=(picture(),))
    assert result.data["items"][0]["assessment"]["verdict"] == "incorrect"
    assert result.data["items"][0]["assessment"]["correct_answer"] == "旧复核标准"
    assert "position_arithmetic" not in application(result)


def test_no_blind_visual_never_applies_arithmetic_or_requests_one():
    first = correct("1(3)", stem_text=STEM, answer_text=ANSWER); second = {**first, "verdict": "incorrect"}
    proposal = review(label="1(3)", source=first)
    flow = TwoModelWholePagePipeline(FakeClient("first", {"items": [first]}),
        FakeClient("second", {"items": [second]}), visual_client=FakeClient("visual", RuntimeError("must not call")),
        reconciliation_client=FakeClient("review", {"items": [proposal]}))
    result = flow.recognize(images=(picture(),))
    assert len(result.metadata.attempts) == 3 and not flow.visual_client.calls
    assert result.data["items"][0]["assessment"]["verdict"] == "correct"
    assert "position_arithmetic" not in application(result)


def test_real_worker_persists_position_checks_and_keeps_candidate_status(flow):
    from sqlalchemy.orm import Session
    from xueban_backend.correction_models import Entity, Task
    flow.worker.pipeline = synthetic_flow()
    _, job = flow.recognize()
    assert flow.worker.run_once()
    assert flow.client.get("/v1/jobs/" + job["id"]).json()["status"] == "succeeded"
    case = flow.client.get("/v1/cases/" + flow.homework()["case_ids"][0]).json()
    decision = flow.client.get("/v1/decisions/" + case["current_decision"]["id"]).json()["record"]
    attempt = flow.client.get("/v1/attempts/" + case["current_attempt_id"]).json()["record"]
    assert decision["verdict"] == "partially_correct" and decision["validity"] == "candidate"
    assert case["progress"] == "needs_correction" and attempt["answer_text"] == ANSWER
    assert [(c["student_answer"], c["correct_answer"]) for c in decision["answer_checks"]] == [("－", None), ("2", "-2")]
    assert decision["knowledge_points"] == []  # New printed conditions do not invent tags.
    with Session(flow.database.engine) as session:
        saved = session.get(Entity, decision["id"])
        assert saved.document == decision
        assert saved.annotations["assessment"]["verdict"] == "partially_correct"
        task = session.get(Task, job["id"])
        assert len(task.audit["executions"][-1]["metadata"]["attempts"]) == 4
    assert flow.client.get("/v1/decisions/" + decision["id"]).json()["record"] == decision
