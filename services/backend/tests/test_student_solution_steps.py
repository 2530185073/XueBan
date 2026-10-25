"""Grade-aware solution contracts through real adapters, with synthetic I/O only.

Provider HTTP uses MockTransport; persistence uses flow's disposable SQLite and
generated blank image. No process credentials, student files or live DBs are used.
These tests verify instructions and transport, not a model's mathematical ability.
"""

from copy import deepcopy
import json
from pathlib import Path
import re
import socket

import httpx
from jsonschema import Draft202012Validator
import pytest
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401 -- synthetic upload/API fixture
import test_user_original_pipeline as helpers
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Entity, Task
from xueban_backend.learning_models import StudentLearningProfile
from xueban_backend.models import Homework
from xueban_backend.providers.user_original import correction_pipeline, prompts
from xueban_backend.worker import CorrectionWorker


VERSION = "photo-correction.v20-grade-level-solutions"
GRADES = (5, 6, 7, 8, None)
# stem, original student writing, final answer, student-facing worked solution
EXAMPLES = {
    5: ("计算3.6×5－8。", "3.6×5＝18\n18－8＝12", "10",
        "3.6×5＝18\n18－8＝10"),
    6: ("计算3/4－1/6。", "3/4－1/6＝2/2", "7/12",
        "3/4＝9/12\n1/6＝2/12\n9/12－2/12＝7/12"),
    7: ("解方程3x＋5＝20。", "3x＝20＋5\nx＝25/3", "x＝5",
        "3x＝20－5\n3x＝15\nx＝5"),
    8: ("解方程组：x＋y＝9，x－y＝3。", "2x＝9－3\nx＝3，y＝6", "x＝6，y＝3",
        "(x＋y)＋(x－y)＝9＋3\n2x＝12\nx＝6\ny＝9－6＝3\n答：x＝6，y＝3。"),
    None: ("计算12×3－8。", "12×3＝36\n36－8＝30", "28",
           "12×3＝36\n36－8＝28"),
}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Solution regressions must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def solution_context(grade, role="check"):
    stem, written, _, _ = EXAMPLES[grade]
    ctx = helpers.context()
    ctx["question"].update(stem_text=stem, question_type="calculation")
    ctx["attempt"]["answer_text"] = written
    ctx["student_grade"] = grade
    if role == "recheck":
        ctx["images"] = (helpers.picture(), helpers.picture("asset_new"))
        ctx["attempt"].update(asset_ids=["asset_new"], answer_regions=[helpers.region("asset_new")],
                              prior_answer="OLD_ANSWER_MUST_NOT_BE_SENT")
        ctx["question"]["old_decision"] = "OLD_VERDICT_MUST_NOT_BE_SENT"
    return ctx


def solution_output(grade, **changes):
    _, _, answer, steps = EXAMPLES[grade]
    output = helpers.checked(correct_answer=answer, correction_method=steps,
                             basis_summary="按题目条件核算，学生原算式的结果有误。", **changes)
    # The second pass must not invent a competing transcription.
    output.pop("readback")
    output.pop("recognition_consistent")
    return output


def sent_instructions(request):
    body = json.loads(request.content)
    if "messages" in body:
        return body["messages"][0]["content"].split("\nJSON Schema:\n", 1)[0]
    return body["instructions"]


def sent_schema(request):
    body = json.loads(request.content)
    if "messages" in body:
        return json.loads(body["messages"][0]["content"].split("\nJSON Schema:\n", 1)[1])
    return body["text"]["format"]["schema"]


def assert_solution_request(request, grade, role):
    instruction = prompts.student_solution_instructions(grade)
    system = sent_instructions(request)
    assert system.count(instruction) == 1
    assert prompts.ROLE_PROMPTS[prompts.Role(role)] in system
    task = helpers.request_task(request)
    assert task["student_learning_context"]["grade"] == grade
    assert instruction not in json.dumps(task, ensure_ascii=False)
    field = sent_schema(request)["properties"]["correction_method"]
    assert "正确解题步骤" in field["description"]
    validator = Draft202012Validator(field)
    assert validator.is_valid(EXAMPLES[grade][3]) and validator.is_valid(None)
    assert not validator.is_valid(["一步算式"])


@pytest.mark.parametrize("grade", GRADES)
def test_solution_instruction_contract_uses_grade_and_concrete_lines(grade):
    instruction = prompts.student_solution_instructions(grade)
    for requirement in ("正确解题步骤", "correction_method", "每行", "\\n", "本题数值",
                        "答：", "correct_answer", "answer_text", "student_answer", "证据不足"):
        assert requirement in instruction
    assert "正确题的correction_method为null" in instruction
    assert "不为了凑步骤编造" in instruction
    if grade is None:
        assert "不推测学生年级" in instruction
        assert re.search(r"[1-9一二三四五六七八九]年级", instruction) is None
    elif grade <= 6:
        assert f"小学{grade}年级" in instruction
        assert "算术" in instruction and "分数" in instruction
        assert "不能为缩短答案引入初中方程技巧" in instruction
    else:
        assert f"初中{grade}年级" in instruction
        assert "此前已学" in instruction and "逐行" in instruction
        assert "不跳到更高年级" in instruction


def test_solution_keeps_existing_nullable_string_contract_and_prompt_version():
    assert correction_pipeline.PROMPT_VERSION == VERSION
    schema = correction_pipeline.CHECK_SCHEMA
    assert set(schema["properties"]) == {
        "verdict", "reason_code", "readback", "recognition_consistent", "basis_summary",
        "uncertainties", "knowledge_points", "correction_method", "correct_answer", "diagram_verified",
    }
    assert schema["additionalProperties"] is False
    root = Path(__file__).resolve().parents[3] / "contracts" / "phase1"
    public = json.loads((root / "data.schema.json").read_text())["$defs"]["Decision"]
    fields = (schema["properties"]["correction_method"], public["properties"]["correction_method"])
    for field in fields:
        validator = Draft202012Validator(field)
        for value in [None, *[example[3] for example in EXAMPLES.values()]]:
            validator.validate(value)
        for invalid in ([], ["算式"], {"steps": ["算式"]}, 42):
            assert not validator.is_valid(invalid)


@pytest.mark.parametrize("grade", GRADES)
@pytest.mark.parametrize("role", ["check", "recheck"])
@pytest.mark.parametrize("route", ["ds", "independent_review", "transport_fallback"])
def test_every_check_route_receives_identical_grade_solution_policy(monkeypatch, grade, role, route):
    ctx = solution_context(grade, role)
    output = solution_output(grade)
    if route == "independent_review":
        # Existing difficult-question policy, not formatting, triggers review.
        ctx["question"]["question_type"] = "application"
    before = deepcopy(ctx)
    first = httpx.Response(503) if route == "transport_fallback" else output
    fallback = [] if route == "ds" else [output]
    pipeline, requests = helpers.isolated_factory(monkeypatch, [first], fallback)
    with pipeline:
        result = getattr(pipeline, role)(**ctx)
    expected_hosts = ["ds.example.invalid"] + ([] if route == "ds" else ["astra.example.invalid"])
    assert [request.url.host for request in requests] == expected_hosts
    assert len(result.metadata.attempts) == len(requests)
    assert result.prompt_version == VERSION
    assert result.data["verdict"] == "incorrect" and result.data["validity"] == "candidate"
    assert result.data["correction_method"] == EXAMPLES[grade][3]
    assert result.data["correct_answer"] == EXAMPLES[grade][2]
    assert result.data["readback"]["answer_text"] == EXAMPLES[grade][1]
    assert ctx == before
    for request in requests:
        assert_solution_request(request, grade, role)
        task = helpers.request_task(request)
        assert task["provisional_transcription"]["answer_text"] == EXAMPLES[grade][1]
        assert task["attempt_asset_ids"] == (["asset_new"] if role == "recheck" else ["asset_original"])
        assert "OLD_" not in request.content.decode()
    if route == "independent_review":
        assert helpers.request_task(requests[-1])["primary_candidate_for_independent_review"] == output
    elif route == "transport_fallback":
        assert "primary_candidate_for_independent_review" not in helpers.request_task(requests[-1])
        assert sent_instructions(requests[0]) == sent_instructions(requests[1])
        assert [attempt.code for attempt in result.metadata.attempts] == ["upstream_unavailable", "ok"]


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_correct_final_answer_without_work_keeps_null_solution_and_normal_review(monkeypatch, role):
    ctx = solution_context(5, role)
    ctx["attempt"]["answer_text"] = "10"  # This problem does not require showing work.
    output = solution_output(5, verdict="correct")
    output.update(correct_answer=None, correction_method=None, basis_summary="终答为10，计算正确。")
    pipeline, requests = helpers.isolated_factory(monkeypatch, [output], [output])
    with pipeline:
        result = getattr(pipeline, role)(**ctx)
    assert len(requests) == len(result.metadata.attempts) == 2
    assert result.data["verdict"] == "correct" and result.data["validity"] == "candidate"
    assert result.data["correct_answer"] is result.data["correction_method"] is None
    assert result.data["readback"]["answer_text"] == "10"
    for request in requests:
        assert_solution_request(request, 5, role)


@pytest.mark.parametrize("role", ["check", "recheck"])
@pytest.mark.parametrize("reason", ["incomplete_question", "unclear_image", "insufficient_process"])
def test_missing_evidence_never_gets_fabricated_solution(monkeypatch, role, reason):
    ctx = solution_context(7, role)
    if reason == "incomplete_question":
        ctx["question"]["stem_text"] = None
    elif reason == "unclear_image":
        ctx["attempt"].update(answer_text=None, answer_status="unclear")
    else:
        ctx["question"].update(stem_text="证明两条直线平行，必须写出证明依据。", question_type="proof")
        ctx["attempt"]["answer_text"] = "两条直线平行。"
    output = solution_output(7, verdict="unable_to_judge", reason_code=reason,
                             uncertainties=["缺少判断所需的题目、笔迹或必需证明步骤。"])
    output.update(correct_answer=None, correction_method=None)
    before = deepcopy(ctx)
    pipeline, requests = helpers.isolated_factory(monkeypatch, [output], [output])
    with pipeline:
        result = getattr(pipeline, role)(**ctx)
    assert len(requests) == len(result.metadata.attempts) == 2
    assert result.data["verdict"] == "unable_to_judge" and result.data["validity"] == "needs_review"
    assert result.data["reason_code"] == reason
    assert result.data["correct_answer"] is result.data["correction_method"] is None
    assert result.data["evidence_verified"] is False
    assert ctx == before


@pytest.mark.parametrize("role", ["check", "recheck"])
@pytest.mark.parametrize("verdict", ["incorrect", "partially_correct", "unanswered"])
@pytest.mark.parametrize("method", ["3.6×5－8＝10", "先乘后减，结果是10。"])
def test_presentation_style_does_not_change_verdict_or_add_calls(monkeypatch, role, verdict, method):
    ctx = solution_context(5, role)
    if verdict == "unanswered":
        ctx["attempt"].update(answer_text=None, answer_status="blank")
    output = solution_output(5, verdict=verdict)
    output["correction_method"] = method
    pipeline, requests = helpers.isolated_factory(monkeypatch, [output], [])
    with pipeline:
        result = getattr(pipeline, role)(**ctx)
    assert len(requests) == len(result.metadata.attempts) == 1
    assert result.data["verdict"] == verdict and result.data["validity"] == "candidate"
    assert result.data["correction_method"] == method
    assert result.data["correct_answer"] == "10"


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_semantic_fallback_preserves_original_slot_answer_and_solution_instruction(monkeypatch, role):
    ctx = solution_context(6, role)
    ctx["attempt"]["answer_slots"] = [{"index": 1, "prompt": "3/4－1/6＝____",
        "answer_text": EXAMPLES[6][1], "answer_status": "present"}]
    good = solution_output(6)
    good["answer_checks"] = [{"index": 1, "prompt": "3/4－1/6＝____",
        "student_answer": EXAMPLES[6][1], "verdict": "incorrect", "correct_answer": "7/12",
        "explanation": "应先通分再相减。"}]
    bad = deepcopy(good)
    bad["answer_checks"][0]["student_answer"] = "7/12"  # A model must not repair the student's writing.
    before = deepcopy(ctx)
    pipeline, requests = helpers.isolated_factory(monkeypatch, [bad], [good])
    with pipeline:
        result = getattr(pipeline, role)(**ctx)
    assert len(requests) == len(result.metadata.attempts) == 2
    assert result.metadata.attempts[0].diagnostic.layer == "candidate_semantics"
    assert result.data["answer_checks"][0]["student_answer"] == EXAMPLES[6][1]
    assert result.data["readback"]["answer_text"] == EXAMPLES[6][1]
    assert result.data["correction_method"] == EXAMPLES[6][3]
    assert ctx == before
    for request in requests:
        assert_solution_request(request, 6, role)
        assert helpers.request_task(request)["answer_slots"] == before["attempt"]["answer_slots"]
    assert sent_instructions(requests[0]) == sent_instructions(requests[1])


@pytest.mark.parametrize("role", ["check", "recheck"])
@pytest.mark.parametrize("primary_failure", [False, True])
def test_review_outage_keeps_unresolved_nulls_without_retrying_for_steps(monkeypatch, role, primary_failure):
    output = solution_output(8, verdict="correct")
    output.update(correct_answer=None, correction_method=None)
    first = httpx.Response(503) if primary_failure else output
    pipeline, requests = helpers.isolated_factory(monkeypatch, [first], [httpx.Response(503)])
    with pipeline:
        result = getattr(pipeline, role)(**solution_context(8, role))
    assert len(requests) == len(result.metadata.attempts) == 2
    assert result.data["verdict"] == "unable_to_judge" and result.data["validity"] == "needs_review"
    assert result.data["correct_answer"] is result.data["correction_method"] is None
    assert result.data["evidence_verified"] is False
    for request in requests:
        assert_solution_request(request, 8, role)


@pytest.mark.parametrize("repair", ["closed_answer", "unit_whole"])
def test_reconciled_visual_solution_uses_corrected_fill_and_verified_equation_lines(monkeypatch, repair):
    ctx = solution_context(6)
    ctx["question"].update(stem_text="如图，绳长30米，取全长的3/5；用____法。",
                           has_diagram=True, question_type="fill_blank")
    slots = []
    checks = []
    abstract = "明确数量关系，根据题意选择运算并注意检查。"
    if repair == "unit_whole":
        slots.append({"index": 1, "prompt": "单位1是已知还是未知？",
                      "answer_text": "已知", "answer_status": "present"})
        checks.append({"index": 1, "prompt": slots[0]["prompt"], "student_answer": "已知",
                       "verdict": "correct", "correct_answer": "已知", "explanation": abstract})
    method_index = len(slots) + 1
    slots.append({"index": method_index, "prompt": "用____法",
                  "answer_text": "除", "answer_status": "present"})
    checks.append({"index": method_index, "prompt": "用____法", "student_answer": "除",
                   "verdict": "correct", "correct_answer": "乘" if repair == "closed_answer" else "除",
                   "explanation": abstract})
    ctx["question"]["blank_count"] = len(slots)
    ctx["attempt"].update(answer_text="；".join(slot["answer_text"] for slot in slots), answer_slots=slots)
    expected_verdict = "partially_correct" if repair == "unit_whole" else "incorrect"
    visual = {"brackets": [{"label": "30米", "position": "最下方", "covers": "整体"}],
              "equal_parts": "整体5份，所求为其中3份", "unit_whole": "绳长30米", "whole_is_known": True,
              "equation": "30×(3/5)=18（米）", "result": "18米"}
    output = solution_output(6, verdict=expected_verdict, diagram_verified=True,
                             visual_evidence=visual, answer_checks=checks)
    output.update(correction_method=abstract, correct_answer="；".join(check["correct_answer"] for check in checks))
    before = deepcopy(ctx)
    pipeline, requests = helpers.isolated_factory(monkeypatch, [deepcopy(output)], [deepcopy(output)])
    with pipeline:
        result = pipeline.check(**ctx)
    assert len(requests) == len(result.metadata.attempts) == 2
    assert result.data["verdict"] == expected_verdict
    assert result.data["correction_method"].splitlines() == [
        f"第{method_index}项：应填“乘”。", "列式：30×(3/5)=18（米）", "答：18米",
    ]
    assert abstract not in result.data["correction_method"]
    for check in result.data["answer_checks"]:
        assert check["explanation"] not in result.data["correction_method"]
    assert result.data["visual_evidence"] == visual
    assert result.data["answer_checks"][-1]["correct_answer"] == "乘"
    assert result.data["answer_checks"][-1]["verdict"] == "incorrect"
    assert [check["student_answer"] for check in result.data["answer_checks"]] == [slot["answer_text"] for slot in slots]
    assert ctx == before
    for request in requests:
        assert_solution_request(request, 6, "check")


def get_json(flow, path):
    response = flow.client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("database", ["sqlite"], indirect=True)
@pytest.mark.parametrize("grade", [5, 6, 7, 8])
def test_multiline_solution_survives_worker_storage_and_all_decision_reads(flow, monkeypatch, grade):
    stem, written, answer, steps = EXAMPLES[grade]
    flow.pipeline.stem = stem
    case = flow.ready()
    with Session(flow.database.engine) as session, session.begin():
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "subject": "数学"}
        attempt = session.get(Entity, case["current_attempt_id"])
        attempt.document = {**attempt.document, "answer_text": written}
        homework = session.get(Homework, flow.homework_id)
        # Avoid the fixture's unrelated default PEP/grade-7 BNUP scope conflict.
        homework.curriculum = {**homework.curriculum, "publisher_code": None}
        session.add(StudentLearningProfile(
            student_id="student_a", tenant_id="tenant_a", revision=1,
            document={"grade": grade, "term": None, "textbook_revision": None,
                      "current_knowledge_ids": [], "note": None},
            updated_by="actor_a", updated_at=now(),
        ))
    original_attempt = get_json(flow, f"/v1/attempts/{case['current_attempt_id']}")["record"]
    pipeline, requests = helpers.isolated_factory(monkeypatch, [solution_output(grade)], [])
    job = flow.start_check(case)
    with pipeline:
        worker = CorrectionWorker(flow.database.engine, flow.cfg, pipeline)
        assert worker.run_once()
        assert not worker.run_once()
    assert get_json(flow, f"/v1/jobs/{job['id']}")["status"] == "succeeded"
    assert len(requests) == 1
    assert_solution_request(requests[0], grade, "check")
    current = get_json(flow, f"/v1/cases/{case['id']}")["current_decision"]
    assert current["verdict"] == "incorrect" and current["validity"] == "candidate"
    decision_id = current["id"]
    with Session(flow.database.engine) as session:
        stored = deepcopy(session.get(Entity, decision_id).document)
        assert session.get(Entity, case["current_attempt_id"]).document == original_attempt
        task = session.get(Task, job["id"])
        check = session.get(Entity, task.inputs["check_id"])
        assert check.document["prompt_version"] == VERSION
        assert check.document["decision_ids"] == [decision_id]
    detail = get_json(flow, f"/v1/homeworks/{flow.homework_id}/detail")
    bundled = next(item["current_decision"] for item in detail["case_records"] if item["id"] == case["id"])
    for decision in (stored, current, bundled,
                     get_json(flow, f"/v1/decisions/{decision_id}")["record"],
                     get_json(flow, f"/v1/decisions/{decision_id}?revision=1")["record"]):
        worker.contracts.validate("Decision", decision)
        assert decision["correction_method"] == steps
        assert decision["correct_answer"] == answer
        assert isinstance(decision["correction_method"], str)
        assert decision["correction_method"].splitlines() == steps.splitlines()
        assert "\\n" not in decision["correction_method"]
    assert get_json(flow, f"/v1/attempts/{case['current_attempt_id']}")["record"] == original_attempt
    assert helpers.request_task(requests[0])["provisional_transcription"]["answer_text"] == written
