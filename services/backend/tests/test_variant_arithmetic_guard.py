"""Exact arithmetic vetoes model agreement; unsupported syntax is not a proof."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json
from types import SimpleNamespace

import pytest
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_student_variants import Client, candidate, get, queue, run, start, verification
from test_variant_fillblank_disagreement import SOURCE, assert_blind, signed_candidate
from test_variant_fillblank_repair import execution, source_with_stem
from xueban_backend.learning_models import LearningOutbox, PracticeItem, VariantGeneration
from xueban_backend import practice_generation as generation
from xueban_backend.practice_generation import _agrees, _valid_reference, _verified_item
from xueban_backend.providers import ProviderError
from xueban_backend.providers.ark import ArkSettings
from xueban_backend.providers.responses import ResponsesClient
from xueban_backend.providers.user_original.responses import ResponsesClient as UserOriginalResponsesClient
from xueban_backend.providers.variant_arithmetic import (
    _number, arithmetic_blank_issues, item_arithmetic_issues,
)


def blank_question(stem, values):
    question = {"question_kind": "fill_blank", "stem_text": stem,
                "answer_fields": [{"id": f"blank{i}", "label": f"第{i}空"}
                                  for i in range(1, len(values) + 1)]}
    answer = {"field_answers": [{"field_id": f"blank{i}", "answer_values": [value]}
                                for i, value in enumerate(values, 1)]}
    return question, answer


def q1_candidate(terminal="-3"):
    value = signed_candidate()
    value["stem_text"] = value["stem_text"].replace("(-35)÷7=____(35÷7)=____", "(-24)÷8=____(24÷8)=____")
    value["field_answers"][6]["answer_values"] = [terminal]
    value["solution_text"] = value["solution_text"].replace("(-35)÷7=-(35÷7)=-5", f"(-24)÷8=-(24÷8)={terminal}")
    return value


@pytest.mark.parametrize("expression,expected", [
    ("0", Fraction(0)),
    ("(-24)÷8", Fraction(-3)),
    ("−（24÷8）", Fraction(-3)),
    ("+(24/8)", Fraction(3)),
    ("2×(3-1)", Fraction(4)),
    ("1/3+1/6", Fraction(1, 2)),
    ("0.1+0.2", Fraction(3, 10)),
    ("-1.25÷.5", Fraction(-5, 2)),
    ("2÷(1/2)", Fraction(4)),
    ("1000000000000000", Fraction(10**15)),
    ("1/1000000000000000", Fraction(1, 10**15)),
])
def test_supported_arithmetic_uses_exact_rational_results(expression, expected):
    assert _number(expression) == expected


@pytest.mark.parametrize("expression", [
    "", "True", "x+1", "[1,2]", "__import__('math').sqrt(4)",
    "2**1000000", "9//2", "9%2", "1e3", "1/0", "0/(1-1)",
    "1000000000000001", "1/1000000000000001", "9" * 201,
    "+" * 22 + "1", "+".join(["1"] * 30),
    # AST floats must not round these into an apparently in-bound integer.
    "0.99999999999999999", "999999999999999.99",
])
def test_unsupported_or_over_budget_expression_is_unknown_not_evaluated(expression):
    assert _number(expression) is None


@pytest.mark.parametrize("stem,values", [
    ("(-24)÷8=____(24÷8)=____", ["-", "-3"]),
    ("24÷8=____(24÷8)=____", ["+", "3"]),
    ("（3）（−24）÷8＝____（24÷8）＝____", ["−", "-3.0"]),
    ("0.1+0.2=____", ["0.3"]),
    ("1/3+1/6=____", ["1/2"]),
    ("(-2)÷7=____", ["-2/7"]),
    (r"2+3=\underline{\quad}", ["5"]),
    # Each submitted value is one operand, including a fractional divisor.
    ("2÷____=4", ["1/2"]),
    ("2÷(____)=4", ["1/2"]),
    ("2÷（____）=4", ["1/2"]),
    ("2÷____=-4", ["-1/2"]),
    ("1-____=2/3", ["1/3"]),
    ("2÷____=____", ["1/2", "4"]),
    ("____+3=5", ["2"]),
    ("(1)____+3=5", ["2"]),
])
def test_correct_chains_and_fraction_operands_are_not_vetoed(stem, values):
    question, answer = blank_question(stem, values)
    before = deepcopy((question, answer))
    assert arithmetic_blank_issues(question, answer) == []
    assert (question, answer) == before


@pytest.mark.parametrize("wrong", ["3", "+3", "3.0"])
def test_terminal_sign_error_identifies_correct_negative_result(wrong):
    question, answer = blank_question("计算：(3)(-24)÷8=____(24÷8)=____。", ["-", wrong])
    answer["field_answers"].reverse()
    before = deepcopy((question, answer))
    issues = arithmetic_blank_issues(question, answer)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["code"] == "arithmetic_substitution_conflict"
    assert issue["field_ids"] == ["blank1", "blank2"] and issue["field_id"] == "blank2"
    assert issue["expected"] == "-3" and issue["expected_source"] == "exact_arithmetic_substitution"
    assert "(-24)÷8" in issue["equation_with_answers"]
    assert (question, answer) == before


@pytest.mark.parametrize("stem,values,expected", [
    ("1/3+1/6=____", ["2/3"], "1/2"),
    ("2÷____=____", ["1/2", "1"], "4"),
    ("0.1+0.2=____", ["0.4"], "3/10"),
])
def test_repair_value_is_exact_and_derived_only_from_agreeing_preceding_segments(stem, values, expected):
    issues = arithmetic_blank_issues(*blank_question(stem, values))
    assert len(issues) == 1 and issues[0]["expected"] == expected
    assert issues[0]["field_id"] == f"blank{len(values)}"


@pytest.mark.parametrize("stem,values", [
    ("(-24)÷8=____(24÷8)=____", ["+", "3"]),
    ("(-24)÷8=____(24÷8)=____", ["+", "-3"]),
    ("2+2=____+1", ["2"]),
])
def test_conflicting_intermediate_step_or_nonterminal_blank_does_not_invent_a_repair(stem, values):
    issues = arithmetic_blank_issues(*blank_question(stem, values))
    assert len(issues) == 1
    assert not {"expected", "expected_source", "field_id"} & set(issues[0])


@pytest.mark.parametrize("separator", ["\n", "；", ";", "。"])
def test_unsupported_lines_still_advance_field_mapping(separator):
    stem = separator.join(("写一个数____", "x+1=____", "(-24)÷8=____(24÷8)=____"))
    question, answer = blank_question(stem, ["7", "x+1", "-", "3"])
    answer["field_answers"].reverse()
    issues = arithmetic_blank_issues(question, answer)
    assert len(issues) == 1 and issues[0]["line"] == 3
    assert issues[0]["field_ids"] == ["blank3", "blank4"]
    assert issues[0]["field_id"] == "blank4" and issues[0]["expected"] == "-3"


def test_all_supported_false_lines_are_reported_without_fixing_the_answers():
    question, answer = blank_question("(-24)÷8=____；2÷7=____", ["3", "-2/7"])
    before = deepcopy(answer)
    issues = arithmetic_blank_issues(question, answer)
    assert [(issue["field_id"], issue["expected"]) for issue in issues] == [("blank1", "-3"), ("blank2", "2/7")]
    assert answer == before


@pytest.mark.parametrize("stem,value", [
    (r"\frac{-24}{8}=____", "3"),
    ("√9=____", "4"),
    ("2^3=____", "9"),
    ("2**3=____", "9"),
    ("x+1=____", "3"),
    ("3米+2米=____米", "8"),
    ("1/0=____", "0"),
    ("1e3=____", "4"),
    ("2:1=____", "3"),
    ("计算：2:1=____", "3"),
    ("2+2=____", "__import__('math').sqrt(4)"),
])
def test_unsupported_notation_returns_no_arithmetic_verdict(stem, value):
    assert arithmetic_blank_issues(*blank_question(stem, [value])) == []


@pytest.mark.parametrize("change", ["not_fill_blank", "missing_field", "missing_answer", "long_answer", "long_stem"])
def test_structural_and_size_limits_are_not_guessed_by_arithmetic(change):
    question, answer = blank_question("2+2=____", ["5"])
    if change == "not_fill_blank":
        question["question_kind"] = "solution"
    elif change == "missing_field":
        question["answer_fields"] = []
    elif change == "missing_answer":
        answer["field_answers"] = []
    elif change == "long_answer":
        answer["field_answers"][0]["answer_values"] = ["9" * 201]
    else:
        question["stem_text"] = " " * 16001 + question["stem_text"]
    assert arithmetic_blank_issues(question, answer) == []


def test_persisted_item_adapter_uses_same_arithmetic_guard_and_rejects_absent_arrays():
    value = q1_candidate("3")
    item = SimpleNamespace(**value)
    assert item_arithmetic_issues(item) == arithmetic_blank_issues(value, verification(value))
    item.answer_fields = item.field_answers = None
    assert {issue["code"] for issue in item_arithmetic_issues(item)} >= {"missing_answer_fields", "stem_blank_count_mismatch"}


@pytest.mark.parametrize("regenerations", ["0", "1"])
def test_same_model_agreement_on_wrong_signed_result_cannot_publish(flow, monkeypatch, regenerations):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", regenerations)
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    bad = q1_candidate("3")
    solved = verification(bad)
    assert _agrees(bad, solved) is True  # The prior model-agreement gate alone passed this case.
    assert _valid_reference(bad, solved) is False
    client = Client([bad, solved] * (1 + int(regenerations)))
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 2 * (1 + int(regenerations))
    audit = execution(flow, task)
    assert {call["metadata"]["model"] for call in audit["calls"]} == {"test-model"}
    assert [call["stage"] for call in audit["calls"]] == ["generate", "blind_solve"] * (1 + int(regenerations))
    for call in audit["calls"]:
        if call["stage"] == "blind_solve":
            assert call["validation_errors"][0]["expected"] == "-3"
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


def test_bounded_second_generation_gets_exact_negative_repair_then_requires_new_blind_solve(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    bad, good = q1_candidate("3"), q1_candidate()
    client = Client([bad, verification(bad), good, verification(good)])
    assert run(flow, task, client)["status"] == "ready"
    assert len(client.calls) == 4
    feedback = json.loads(client.calls[2]["text"])["repair_feedback"]
    assert feedback["stage"] == "blind_solve"
    assert len(feedback["errors"]) == 1
    error = feedback["errors"][0]
    assert error["code"] == "arithmetic_substitution_conflict"
    assert error["field_ids"] == ["blank6", "blank7"] and error["field_id"] == "blank7"
    assert error["expected"] == "-3" and error["expected_source"] == "exact_arithmetic_substitution"
    assert feedback["previous_question"]["stem_text"] == bad["stem_text"]
    assert_blind(client.calls[1])
    assert_blind(client.calls[3])
    with Session(flow.database.engine) as session:
        item = session.scalar(select(PracticeItem))
        assert item.field_answers == good["field_answers"]
        assert item.field_answers[5]["answer_values"] == ["-"]
        assert item.field_answers[6]["answer_values"] == ["-3"]
        assert not item_arithmetic_issues(item)


def test_unsupported_notation_does_not_bypass_failed_independent_verification(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    task = queue(flow, source_with_stem(flow, "√4=____", 1))
    generated = candidate()
    generated.update(stem_text="√9=____", solution_text="9的算术平方根是3。")
    generated["field_answers"][0]["answer_values"] = ["3"]
    solved = verification(generated)
    solved["same_type"] = False
    assert arithmetic_blank_issues(generated, solved) == []
    client = Client([generated, solved])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 2
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


def test_previously_published_bad_item_is_hidden_and_cannot_be_started(flow):
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    good = q1_candidate()
    assert run(flow, task, Client([good, verification(good)]))["status"] == "ready"
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(VariantGeneration, task["job_id"])
        item = session.get(PracticeItem, row.item_id)
        # Model a historical item saved before the arithmetic veto existed.
        item.field_answers = q1_candidate("3")["field_answers"]
    with Session(flow.database.engine) as session:
        row = session.get(VariantGeneration, task["job_id"])
        assert _verified_item(session, row) is None
    status = get(flow, task)
    assert status["status"] == "superseded" and status["failure_code"] == "verification_failed"
    assert status["item_id"] is None
    assert start(flow, task).status_code == 409
    with Session(flow.database.engine) as session:
        # Reads enforce the veto without rewriting the historical evidence.
        assert session.get(VariantGeneration, task["job_id"]).status == "ready"
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 1


@pytest.mark.parametrize("stem", ["____+3=3", "(1)____+3=3", "计算：(1)____+3=3"])
def test_substituted_leading_number_is_never_removed_as_a_question_label(stem):
    issues = arithmetic_blank_issues(*blank_question(stem, ["1"]))
    assert len(issues) == 1 and issues[0]["code"] == "arithmetic_substitution_conflict"
    assert issues[0]["field_ids"] == ["blank1"]


@pytest.mark.parametrize("alternatives", [["3", "3.0"], ["3", "6/2"], ["4", "5"]])
def test_all_numeric_alternatives_are_checked_and_wrong_values_cannot_hide(alternatives):
    question, answer = blank_question("2+2=____", ["3"])
    answer["field_answers"][0]["answer_values"] = alternatives
    before = deepcopy(answer)
    issues = arithmetic_blank_issues(question, answer)
    assert issues and all(issue["code"] == "arithmetic_substitution_conflict" for issue in issues)
    assert any(issue.get("expected") == "4" for issue in issues)
    assert answer == before


def test_equivalent_correct_alternatives_are_not_rejected():
    question, answer = blank_question("2+2=____", ["4"])
    answer["field_answers"][0]["answer_values"] = ["4", "+4", "4.0", "8/2"]
    assert arithmetic_blank_issues(question, answer) == []


@pytest.mark.parametrize("width,over_budget", [(8, False), (9, True)])
def test_distinct_alternative_combinations_are_checked_with_a_64_combination_cap(width, over_budget):
    # Every in-budget combination is valid; no early contradiction can hide
    # either an unnecessary rejection at 64 or an unbounded traversal above it.
    question, answer = blank_question("0×____+0×____=0", ["1", "1"])
    answer["field_answers"][0]["answer_values"] = [str(i) for i in range(1, width + 1)]
    answer["field_answers"][1]["answer_values"] = [str(i) for i in range(1, 9)]
    issues = arithmetic_blank_issues(question, answer)
    if over_budget:
        assert len(issues) == 1 and issues[0]["code"] == "arithmetic_alternatives_exceeded"
        assert "expected" not in issues[0]
    else:
        assert issues == []


def test_model_agreement_on_equivalent_wrong_alternatives_cannot_publish(flow, monkeypatch):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0")
    task = queue(flow, source_with_stem(flow, "2+1=____", 1))
    bad = candidate()
    bad.update(stem_text="2+2=____", solution_text="2+2=3。")
    bad["field_answers"][0]["answer_values"] = ["3", "3.0"]
    client = Client([bad, verification(bad)])
    assert run(flow, task, client)["failure_code"] == "verification_failed"
    assert len(client.calls) == 2
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


class NamedClient(Client):
    def __init__(self, outputs, model):
        super().__init__(outputs)
        self.model, self.closes = model, 0

    def generate(self, **kwargs):
        result = super().generate(**kwargs)
        result.metadata = replace(result.metadata, model=self.model)
        return result

    def close(self):
        self.closes += 1


def run_with_fallback(flow, task, primary, fallback):
    with Session(flow.database.engine) as session, session.begin():
        row = session.get(VariantGeneration, task["job_id"])
        job = session.get(LearningOutbox, row.outbox_id)
        result = generation.generate_student_variant(session, job, SimpleNamespace(client=primary, fallback_client=fallback))
        job.status = "succeeded"
        return result


@pytest.mark.parametrize("needs_equivalence", [False, True])
def test_only_second_blind_solve_and_equivalence_use_fallback_within_existing_budget(flow, monkeypatch, needs_equivalence):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    bad, good = q1_candidate("3"), q1_candidate()
    solved = verification(good)
    outputs = [solved]
    if needs_equivalence:
        solved["field_answers"][6]["answer_kind"] = "text"
        outputs.append({"equivalent": True, "reason": "两份完整答案数值及符号一致。"})
    primary = NamedClient([bad, verification(bad), good], "primary-model")
    fallback = NamedClient(outputs, "fallback-model")
    assert run_with_fallback(flow, task, primary, fallback)["status"] == "ready"
    assert len(primary.calls) == 3 and len(fallback.calls) == 1 + int(needs_equivalence)
    assert "repair_feedback" in json.loads(primary.calls[2]["text"])
    assert_blind(primary.calls[1])
    assert_blind(fallback.calls[0])
    if needs_equivalence:
        review = json.loads(fallback.calls[1]["text"])
        assert set(review) == {"question", "answer_a", "answer_b"}
        assert review["answer_b"] == {key: solved[key] for key in generation.ANSWER_SCHEMA}
    audit = execution(flow, task)
    assert [call["stage"] for call in audit["calls"]] == [
        "generate", "blind_solve", "generate", "blind_solve", *(["answer_equivalence"] if needs_equivalence else [])]
    assert [call["metadata"]["model"] for call in audit["calls"]] == [
        "primary-model", "primary-model", "primary-model", "fallback-model", *(["fallback-model"] if needs_equivalence else [])]
    assert primary.closes == fallback.closes == 0  # Injected/worker-owned clients stay open.
    with Session(flow.database.engine) as session:
        item = session.scalar(select(PracticeItem))
        assert item.verification["heterogeneous_models"] is True
        assert item.verification["generator_model"] == "primary-model"
        assert item.verification["verifier_model"] == "fallback-model"
        assert item.field_answers == solved["field_answers"]


@pytest.mark.parametrize("case", ["first_success", "retry_disabled", "disabled_fallback", "provider_failure"])
def test_fallback_is_not_called_when_unneeded_disabled_or_retry_forbidden(flow, monkeypatch, case):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "0" if case == "retry_disabled" else "1")
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    bad, good = q1_candidate("3"), q1_candidate()
    outputs = [good, verification(good)] if case == "first_success" else [bad, verification(bad)]
    if case == "disabled_fallback":
        outputs.extend([good, verification(good)])
    if case == "provider_failure":
        outputs = [ProviderError("upstream_unavailable")]
    primary = NamedClient(outputs, "primary-model")
    fallback = NamedClient([], "fallback-model")
    fallback.settings.enabled = case != "disabled_fallback"
    result = run_with_fallback(flow, task, primary, fallback)
    assert result["status"] == ("ready" if case in ("first_success", "disabled_fallback") else "failed")
    assert fallback.calls == [] and primary.outputs == []


@pytest.mark.parametrize("outcome", ["success", "wrong_again", "provider_error", "secret"])
def test_owned_clients_close_once_on_fallback_success_or_failure_without_extra_retries(flow, monkeypatch, outcome):
    monkeypatch.setenv("VARIANT_MAX_REGENERATIONS", "1")
    task = queue(flow, source_with_stem(flow, SOURCE, 8))
    bad, good = q1_candidate("3"), q1_candidate()
    solved = verification(good if outcome != "wrong_again" else bad)
    if outcome == "provider_error":
        solved = ProviderError("upstream_unavailable")
    elif outcome == "secret":
        solved["field_answers"][6].update(answer_kind="text", answer_values=["fallback-private-token"])
    primary = NamedClient([bad, verification(bad), good], "primary-model")
    fallback = NamedClient([solved], "fallback-model")
    fallback.settings.api_key = "fallback-private-token"
    allocated = []

    def owned(client):
        allocated.append(client)
        return client, True

    monkeypatch.setattr(generation, "bounded_client", owned)
    result = run_with_fallback(flow, task, primary, fallback)
    assert result["status"] == ("ready" if outcome == "success" else "failed")
    assert allocated == [primary, fallback]
    assert primary.closes == fallback.closes == 1
    assert len(primary.calls) == 3 and len(fallback.calls) == 1
    assert "fallback-private-token" not in json.dumps(execution(flow, task))
    if outcome != "success":
        with Session(flow.database.engine) as session:
            assert session.scalar(select(func.count()).select_from(PracticeItem)) == 0


@pytest.mark.parametrize("client_type,stream", [(ResponsesClient, True), (ResponsesClient, False), (UserOriginalResponsesClient, None)])
def test_bounded_responses_client_preserves_protocol_stream_and_configuration_without_closing_source(client_type, stream):
    settings = ArkSettings(base_url="https://provider.example.invalid/v1", api_key="synthetic-only-key",
                           model="fallback-model", timeout_seconds=180, total_timeout_seconds=200,
                           max_attempts=3, max_tokens=8192, max_concurrency=3)
    kwargs = {"stream": stream} if stream is not None else {}
    transport = httpx.MockTransport(lambda request: pytest.fail("This constructor-only test must not make requests"))
    original = client_type(settings, transport=transport, **kwargs)
    bounded = None
    try:
        bounded, owned = generation.bounded_client(original)
        assert owned and type(bounded) is client_type and bounded is not original
        assert bounded.settings == replace(settings, timeout_seconds=generation.CALL_SECONDS,
                                           total_timeout_seconds=generation.CALL_SECONDS,
                                           max_attempts=1, max_tokens=3072, max_concurrency=1)
        if stream is not None:
            assert bounded.stream is stream
        bounded.close()
        assert bounded._closed and not original._closed
    finally:
        if bounded is not None and not bounded._closed:
            bounded.close()
        original.close()
