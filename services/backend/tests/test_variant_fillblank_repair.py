"""Legacy counts and bounded structural repair, with isolated DBs and no model I/O."""

from copy import deepcopy
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import (Client, candidate, get, queue, run, run_assessment,
                                   solution_agreement, source, start, verification)
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import PracticeItem, VariantGeneration
from xueban_backend.practice_generation import _valid_candidate, snapshot_current
from xueban_backend.providers.variant_structure import (
    normalize_blank_answers, source_blank_contract, visible_blank_count,
)


SOURCE = "填空：(1) (-12)÷3=(____)____；(2) 21÷(-3)=(____)____；(3) (-24)÷(-4)=(____)____；(4) 30÷5=(____)____。"
VARIANT = "填空：(1) (-18)÷3=(____)____；(2) 32÷(-4)=(____)____；(3) (-35)÷(-5)=(____)____；(4) 48÷6=(____)____。"


def eight_blanks():
    result = candidate()
    result.update(stem_text=VARIANT,
                  answer_fields=[{"id": f"blank{i}", "label": f"第{(i+1)//2}小题第{(i-1)%2+1}空"}
                                 for i in range(1, 9)],
                  field_answers=[{"field_id": f"blank{i}", "answer_kind": "text" if i % 2 else "number",
                                  "answer_values": [value]}
                                 for i, value in enumerate(("-", "6", "-", "8", "+", "7", "+", "8"), 1)],
                  solution_text="异号相除得负，同号相除得正。18÷3=6，32÷4=8，35÷5=7，48÷6=8，所以结果依次为-6、-8、+7、+8。",
                  hint_text="先确定商的符号，再计算绝对值。")
    return result


def source_with_stem(flow, stem=SOURCE, count=7):
    case = source(flow, blanks=count)
    with Session(flow.database.engine) as session, session.begin():
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "stem_text": stem, "subject": "数学"}
    return case


def execution(flow, task):
    with Session(flow.database.engine) as session:
        return deepcopy(session.get(VariantGeneration, task["job_id"]).audit["executions"][-1])


@pytest.mark.parametrize("stem,count", [
    (SOURCE, 8),
    ("(1) (-3)÷(+1)=(  )____；(2) 4÷2=（＿）＿＿", 4),
    (r"(1) x_1=\underline{\quad}；(2) x_2=\boxed{}；(3) 3+2=(____)", 3),
    ("(1) (-3)÷(+1)；(2) x_1+2=5", 0),
])
def test_count_only_visible_empty_slots(stem, count):
    assert visible_blank_count(stem) == count


def test_missing_legacy_count_is_inferred_only_from_visible_markers():
    source_question = {"stem_text": SOURCE, "blank_count": None}
    before = deepcopy(source_question)
    contract = source_blank_contract(source_question)
    assert contract["expected_blank_count"] == 8 and not contract["errors"]
    assert contract["normalizations"] == [{"code": "source_blank_count_from_visible_markers", "count": 8}]
    assert source_question == before


@pytest.mark.parametrize("stem,count,code", [
    (SOURCE, 9, "source_blank_count_mismatch"),
    (SOURCE, True, "source_blank_count_invalid"),
    (SOURCE, "7", "source_blank_count_invalid"),
    (SOURCE, 0, "source_blank_count_invalid"),
    (SOURCE, 13, "source_blank_count_invalid"),
    ("按要求填空。", None, "source_blank_count_unknown"),
    ("例：3+4=____。" + SOURCE, 7, "source_blank_structure_ambiguous"),
    ("仿照示例，填写下列空格。" + SOURCE, 7, "source_blank_structure_ambiguous"),
])
def test_ambiguous_or_invalid_sources_are_not_silently_recounted(stem, count, code):
    contract = source_blank_contract({"stem_text": stem, "blank_count": count})
    assert contract["expected_blank_count"] is None
    assert contract["errors"][0]["code"] == code
    assert not contract["normalizations"]


def test_legacy_7_becomes_8_only_in_generation_contract_and_preserves_source_and_fingerprint(flow):
    case = source_with_stem(flow)
    task = queue(flow, case)
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        old_snapshot, old_fingerprint = deepcopy(row.snapshot), row.source_fingerprint
        old_document = deepcopy(session.get(Entity, case["question_id"]).document)
    generated = eight_blanks()
    solved = verification(generated)
    # Field answers can arrive in a different order; ids remain authoritative.
    generated["answer_values"] = [field["answer_values"][0] for field in generated["field_answers"]]
    solved["answer_values"] = list(generated["answer_values"])
    solved["field_answers"].reverse()
    client = Client([generated, solved])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 2
    request, blind = (json.loads(call["text"]) for call in client.calls)
    assert request["source_question"]["blank_count"] == 8
    assert request["fill_blank_contract"]["declared_blank_count"] == 7
    assert len(blind["answer_fields"]) == 8
    for private in ("answer_values", "field_answers", "solution_text", "hint_text", "source_question",
                    "fill_blank_contract", "repair_feedback"):
        assert private not in blind
    audit = execution(flow, task)
    assert audit["source_structure"]["normalizations"] == [
        {"code": "source_blank_count_underreported", "declared": 7, "visible": 8}]
    assert all(call["normalizations"] == [{"code": "redundant_top_level_answers_removed"}] for call in audit["calls"])
    assert all(call["output"]["answer_values"] == generated["answer_values"] for call in audit["calls"])
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        assert row.snapshot == old_snapshot and row.source_fingerprint == old_fingerprint
        assert session.get(Entity, case["question_id"]).document == old_document
        assert snapshot_current(session, row)
        item = session.get(PracticeItem, row.item_id)
        assert len(item.answer_fields) == len(item.field_answers) == 8
        assert item.answer_values == [] and item.field_answers == solved["field_answers"]
    assert "原题仍需核对" in get(flow, task)["notice"]
    activity = start(flow, task).json()
    response = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        "item_id": activity["items"][0]["id"],
        "answers": [{"field_id": field["field_id"], "answer_text": field["answer_values"][0]}
                    for field in solved["field_answers"]]})
    # Sign fields are text, so the existing submission flow requests its
    # separate answer assessment instead of silently treating text as numbers.
    assert response.status_code == 202 and response.json()["result"] == "pending"
    assessment_id = response.json()["assessment_id"]
    assert run_assessment(flow, assessment_id, Client([{"result": "correct", "feedback": "符号和数值均正确。"}]))["status"] == "completed"
    assert flow.client.get(f"/v1/practice-assessments/{assessment_id}").json()["result"] == "correct"


@pytest.mark.parametrize("change,code", [
    ("ninth_field", "blank_count_mismatch"),
    ("extra_stem_blank", "stem_blank_count_mismatch"),
    ("missing_answer", "field_answer_mapping_mismatch"),
    ("conflicting_top_level", "fill_blank_top_level_answers"),
    ("duplicate_field", "duplicate_field_ids"),
    ("duplicate_answer", "duplicate_field_answers"),
    ("invalid_number", "invalid_field_answer"),
])
def test_structural_retry_has_actionable_feedback_and_repaired_candidate_is_blindly_solved(flow, monkeypatch, change, code):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    case = source_with_stem(flow)
    task = queue(flow, case)
    good = eight_blanks()
    bad = deepcopy(good)
    if change == "ninth_field":
        bad["answer_fields"].append({"id": "blank9", "label": "第9空"})
        bad["field_answers"].append({"field_id": "blank9", "answer_kind": "number", "answer_values": ["1"]})
    elif change == "extra_stem_blank":
        bad["stem_text"] += "再计算1+1=____。"
    elif change == "missing_answer":
        bad["field_answers"].pop()
    elif change == "conflicting_top_level":
        bad["answer_values"] = ["错误的整体答案"]
    elif change == "duplicate_field":
        bad["answer_fields"][1]["id"] = "blank1"
    elif change == "duplicate_answer":
        bad["field_answers"][1]["field_id"] = "blank1"
    else:
        bad["field_answers"][1]["answer_values"] = ["1/0"]
    client = Client([bad, good, verification(good)])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 3
    feedback = json.loads(client.calls[1]["text"])["repair_feedback"]
    assert feedback["stage"] == "generate"
    assert code in {error["code"] for error in feedback["errors"]}
    assert feedback["previous_question"]["stem_text"] == bad["stem_text"]
    assert "repair_feedback" not in json.loads(client.calls[2]["text"])
    assert "answer_values" not in client.calls[2]["text"]
    assert [call["stage"] for call in execution(flow, task)["calls"]] == ["generate", "generate", "blind_solve"]


@pytest.mark.parametrize("regenerations", ["0", "1"])
def test_repeated_bad_structure_stops_at_configured_limit_with_specific_failure(flow, monkeypatch, regenerations):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", regenerations)
    case = source_with_stem(flow)
    task = queue(flow, case)
    bad = eight_blanks()
    bad["answer_values"] = ["conflict"]
    limit = 1 + int(regenerations)
    client = Client([bad] * limit)
    assert run(flow, task, client)["failure_code"] == "candidate_structure_invalid"
    for _ in range(2):
        assert get(flow, task)["failure_code"] == "candidate_structure_invalid"
    assert "填空位置或逐空答案" in get(flow, task)["notice"]
    assert len(client.calls) == limit
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


@pytest.mark.parametrize("stem,count", [(SOURCE, 9), ("例：3+4=____。" + SOURCE, 7)])
def test_source_ambiguity_fails_before_any_paid_call_with_recheck_notice(flow, stem, count):
    case = source_with_stem(flow, stem, count)
    task = queue(flow, case)
    client = Client([])
    assert run(flow, task, client)["failure_code"] == "source_structure_invalid"
    assert client.calls == [] and "先核对原题" in get(flow, task)["notice"]
    assert execution(flow, task)["source_structure"]["errors"]


@pytest.mark.parametrize("change", ["conflict", "alternatives", "duplicate_id", "missing_field", "numeric_equivalence"])
def test_normalization_never_selects_drops_or_equates_conflicting_answers(change):
    value = eight_blanks()
    value["answer_values"] = [field["answer_values"][0] for field in value["field_answers"]]
    if change == "conflict":
        value["answer_values"][0] = "+"
    elif change == "alternatives":
        value["field_answers"][1]["answer_values"].append("6.0")
    elif change == "duplicate_id":
        value["field_answers"][1]["field_id"] = "blank1"
    elif change == "missing_field":
        value["field_answers"].pop()
    else:
        value["answer_values"][1] = "6.0"
    before = deepcopy(value)
    normalized, audit = normalize_blank_answers(value, value["answer_fields"])
    assert normalized == value == before and audit == []


def test_duplicate_answers_normalization_does_not_override_wrong_numeric_reference(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    case = source_with_stem(flow)
    task = queue(flow, case)
    generated = eight_blanks()
    generated["answer_values"] = [field["answer_values"][0] for field in generated["field_answers"]]
    wrong = verification(eight_blanks())
    wrong["field_answers"][1]["answer_values"] = ["99"]
    client = Client([generated, wrong])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 2  # Numeric disagreement cannot go to an equivalence override.
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


def test_invalid_blind_field_mapping_gets_specific_failure(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    task = queue(flow, source_with_stem(flow))
    generated = eight_blanks()
    solved = verification(generated)
    solved["field_answers"].pop()
    client = Client([generated, solved])
    assert run(flow, task, client)["failure_code"] == "reference_structure_invalid"
    assert len(client.calls) == 2
    assert execution(flow, task)["calls"][-1]["validation_errors"][0]["code"] == "field_answer_mapping_mismatch"


def test_invalid_blind_mapping_uses_same_bounded_retry_and_never_leaks_old_answers(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    task = queue(flow, source_with_stem(flow))
    generated = eight_blanks()
    solved = verification(generated)
    bad = deepcopy(solved)
    bad["answer_values"] = ["conflicting summary"]
    client = Client([generated, bad, generated, solved])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 4
    feedback = json.loads(client.calls[2]["text"])["repair_feedback"]
    assert feedback["stage"] == "blind_solve"
    assert feedback["errors"][0]["code"] == "fill_blank_top_level_answers"
    for call in (client.calls[1], client.calls[3]):
        blind = json.loads(call["text"])
        assert not {"answer_values", "solution_text", "field_answers", "repair_feedback"} & set(blind)


def test_calculation_subparts_with_zero_blanks_do_not_use_fill_blank_contract(flow):
    case = source(flow, "calculation")
    with Session(flow.database.engine) as session, session.begin():
        question = session.get(Entity, case["question_id"])
        question.document = {**question.document, "blank_count": 0}
    task = queue(flow, case)
    generated = candidate("calculation")
    client = Client([generated, verification(generated), solution_agreement()])
    assert run(flow, task, client)["status"] == "ready"
    assert "fill_blank_contract" not in json.loads(client.calls[0]["text"])
    assert "source_structure" not in execution(flow, task)


def test_exact_source_copy_still_fails_after_legacy_count_reconciliation():
    value = eight_blanks()
    value["stem_text"] = SOURCE
    snapshot = {"question_type": "fill_blank", "question_kind": "fill_blank", "source_curriculum": {"subject": "数学"},
                "source_question": {"stem_text": SOURCE, "blank_count": 7}}
    assert not _valid_candidate(value, snapshot)
