"""Fast-route contract: real recognition provenance, synthetic model transports.

No database fixtures, real credentials, or network calls are needed. Provenance
must be issued by recognize(); a test-supplied truth flag is never sufficient.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
import socket
from threading import Barrier, Lock

import httpx
from PIL import Image
import pytest

from test_user_original_pipeline import (
    PNG, CorrectionPipeline, checked, context, envelope, isolated_env,
    isolated_factory, picture, recognition, request_task, responses_envelope, source_reading,
)
from xueban_backend.providers.user_original import ProviderError
from xueban_backend.providers.user_original.source_provenance import source_evidence_matches


DS = "ds.example.invalid"
ASTRA = "astra.example.invalid"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("routing regressions must use synthetic transports only")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def _page(answer="12", *, stem="计算7+5。", question_type="calculation"):
    page = recognition()
    page["items"][0].update(
        stem_text=stem, answer_text=answer, question_type=question_type,
        options=[], blank_count=0, has_diagram=False,
    )
    return page


def _answer_text(item):
    slots = item.get("answer_slots", [])
    if not slots or len(slots) == 1 and slots[0]["prompt"] == "完整作答":
        return item["answer_text"]
    return "\n".join(f'{slot["index"]}. {slot["answer_text"]}' for slot in slots)


def _reply(page, *, verdict="correct", correct_answer=None, **changes):
    item = page["items"][0]
    return checked(
        verdict=verdict, correct_answer=correct_answer,
        correction_method=None if verdict == "correct" else "7+5=12。",
        basis_summary="依据题干独立核算并核对学生原作答。",
        readback={"stem_text": item["stem_text"], "answer_text": _answer_text(item),
                  "answer_status": item["answer_status"]},
        **changes,
    )


def _context_from_item(item, *, with_source):
    ctx = context()
    for key in ("label", "stem_text", "question_type", "options", "blank_count", "has_diagram"):
        ctx["question"][key] = deepcopy(item[key])
    ctx["question"]["regions"] = deepcopy(item.get("regions", ctx["question"]["regions"]))
    ctx["question"]["recognition_uncertainties"] = deepcopy(item["uncertainties"])
    ctx["attempt"].update(
        answer_text=_answer_text(item), answer_status=item["answer_status"],
        answer_slots=deepcopy(item.get("answer_slots", [])),
        answer_regions=deepcopy(item.get("answer_regions", [])),
        recognition_status=item.get("recognition_status", "ready"),
        recognition_uncertainties=deepcopy(item["uncertainties"]),
    )
    if with_source:
        assert item.get("source_evidence"), (
            "recognize() must issue per-item source_evidence after the independent "
            "Astra read; source_reading_audit alone does not reach check()"
        )
        ctx["attempt"]["_source_evidence"] = deepcopy(item["source_evidence"])
    return ctx


@contextmanager
def _recognized_case(monkeypatch, page, primary, *, fallback=None):
    fallback = deepcopy(primary) if fallback is None else fallback
    flow, requests = isolated_factory(
        monkeypatch, [page, primary], [source_reading(page), fallback],
    )
    with flow:
        recognized = flow.recognize(images=(picture(),))
        assert [request.url.host for request in requests] == [DS, ASTRA]
        ctx = _context_from_item(recognized.data["items"][0], with_source=True)
        assert source_evidence_matches(
            {image.asset_id: image for image in ctx["images"]}, ctx["question"], ctx["attempt"],
        ), "the fixture must copy the complete recognized question and answer"
        yield flow, requests, ctx


def _check_hosts(requests):
    # The first two requests belong to whole-page recognition and source reading.
    return [request.url.host for request in requests[2:]]


@pytest.mark.parametrize("answer,verdict", [("12", "correct"), ("11", "incorrect")])
def test_source_verified_pure_arithmetic_uses_one_non_thinking_ds_check(monkeypatch, answer, verdict):
    page = _page(answer)
    primary = _reply(page, verdict=verdict, correct_answer="12" if verdict == "incorrect" else None)
    with _recognized_case(monkeypatch, page, primary) as (flow, requests, ctx):
        original = deepcopy(ctx)
        result = flow.check(**ctx)
    assert _check_hosts(requests) == [DS]
    assert json.loads(requests[2].content)["thinking"] == {"type": "disabled"}
    assert result.data["verdict"] == verdict
    assert result.data["validity"] == "candidate"
    assert result.data["evidence_verified"] is True
    assert len(result.metadata.attempts) == 1
    assert result.metadata.model == flow.client.settings.model
    assert ctx == original
    if verdict == "incorrect":
        assert result.data["correct_answer"] == "12"
        assert result.data["correction_method"]


def test_correct_without_source_provenance_still_requires_gpt(monkeypatch):
    page = _page()
    primary = _reply(page)
    ctx = _context_from_item(page["items"][0], with_source=False)
    assert "_source_evidence" not in ctx["attempt"]
    flow, requests = isolated_factory(monkeypatch, [primary], [primary])
    with flow:
        result = flow.check(**ctx)
    assert [request.url.host for request in requests] == [DS, ASTRA]
    assert len(result.metadata.attempts) == 2
    assert result.metadata.model == flow.fallback_client.settings.model
    assert result.data["verdict"] == "correct"
    assert request_task(requests[1])["primary_candidate_for_independent_review"]["verdict"] == "correct"


def test_simple_first_pass_disables_thinking_even_when_gpt_review_is_needed(monkeypatch):
    page = _page("11")
    primary = _reply(page)
    fallback = _reply(page, verdict="incorrect", correct_answer="12")
    with _recognized_case(monkeypatch, page, primary, fallback=fallback) as (flow, requests, ctx):
        flow.check(**ctx)
    assert json.loads(requests[2].content)["thinking"] == {"type": "disabled"}
    assert _check_hosts(requests) == [DS, ASTRA]


@pytest.mark.parametrize("answer,ds_verdict,ds_answer", [
    ("11", "correct", None),
    ("11", "incorrect", "13"),
    ("12", "incorrect", "13"),
])
def test_ds_arithmetic_disagreement_still_requires_gpt(monkeypatch, answer, ds_verdict, ds_answer):
    page = _page(answer)
    primary = _reply(page, verdict=ds_verdict, correct_answer=ds_answer)
    fallback = _reply(page, verdict="correct" if answer == "12" else "incorrect",
                      correct_answer=None if answer == "12" else "12")
    with _recognized_case(monkeypatch, page, primary, fallback=fallback) as (flow, requests, ctx):
        result = flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]
    assert len(result.metadata.attempts) == 2
    assert result.data["correct_answer"] != "13"
    if answer == "11":
        assert result.data["verdict"] != "correct"
    else:
        assert result.data["verdict"] in {"correct", "unable_to_judge"}


@pytest.mark.parametrize("first_answer,verdict", [("12", "correct"), ("11", "partially_correct")])
def test_independently_checkable_numeric_slots_do_not_escalate_just_for_slot_count(
    monkeypatch, first_answer, verdict,
):
    page = _page(stem="7+5=____；9-4=____。", question_type="fill_blank")
    item = page["items"][0]
    item.update(blank_count=2, answer_slots=[
        {"index": 1, "prompt": "7+5=____", "answer_text": first_answer, "answer_status": "present"},
        {"index": 2, "prompt": "9-4=____", "answer_text": "5", "answer_status": "present"},
    ])
    item["answer_text"] = _answer_text(item)
    checks = [
        {"index": slot["index"], "prompt": slot["prompt"], "student_answer": slot["answer_text"],
         "verdict": "correct" if slot["answer_text"] == expected else "incorrect",
         "correct_answer": expected, "explanation": explanation}
        for slot, expected, explanation in zip(item["answer_slots"], ("12", "5"), ("7+5=12。", "9-4=5。"))
    ]
    primary = _reply(page, verdict=verdict, correct_answer=None if verdict == "correct" else "1. 12；2. 5",
                     answer_checks=checks)
    with _recognized_case(monkeypatch, page, primary) as (flow, requests, ctx):
        result = flow.check(**ctx)
    assert _check_hosts(requests) == [DS]
    assert json.loads(requests[2].content)["thinking"] == {"type": "disabled"}
    assert result.data["verdict"] == verdict
    assert [check["student_answer"] for check in result.data["answer_checks"]] == [first_answer, "5"]
    assert [check["correct_answer"] for check in result.data["answer_checks"]] == ["12", "5"]


@pytest.mark.parametrize("question_type,stem", [
    ("proof", "证明7+5=12。"),
    ("application", "小明有7支笔，又买了5支，一共有多少支笔？"),
])
def test_proof_and_application_keep_gpt_even_with_source_evidence(monkeypatch, question_type, stem):
    page = _page("11", stem=stem, question_type=question_type)
    primary = _reply(page, verdict="incorrect", correct_answer="12")
    with _recognized_case(monkeypatch, page, primary) as (flow, requests, ctx):
        flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]


def test_diagram_keeps_gpt_even_when_ds_claims_it_verified_the_figure(monkeypatch):
    page = _page("11")
    page["items"][0].update(has_diagram=True, answer_slots=[
        {"index": 1, "prompt": "完整作答", "answer_text": "11", "answer_status": "present"},
    ])
    primary = _reply(page, verdict="incorrect", correct_answer="12", diagram_verified=True,
        visual_evidence={"brackets": [], "equal_parts": None, "unit_whole": "整段",
                         "whole_is_known": True, "equation": None, "result": None},
        answer_checks=[{"index": 1, "prompt": "完整作答", "student_answer": "11",
                        "verdict": "incorrect", "correct_answer": "12", "explanation": "7+5=12。"}])
    with _recognized_case(monkeypatch, page, primary) as (flow, requests, ctx):
        result = flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]
    assert len(result.metadata.attempts) == 2
    assert request_task(requests[-1])["primary_candidate_for_independent_review"]["diagram_verified"] is True


@pytest.mark.parametrize("stem,answer,correct_answer", [
    ("计算sin(30°)。", "0", "1/2"),
    ("计算6÷2(1+2)。", "8", "9"),
])
def test_unsupported_or_ambiguous_arithmetic_keeps_gpt(monkeypatch, stem, answer, correct_answer):
    page = _page(answer, stem=stem)
    primary = _reply(page, verdict="incorrect", correct_answer=correct_answer)
    primary["correction_method"] = "需进一步核实题目中的运算约定。"
    fallback = _reply(page, verdict="unable_to_judge", reason_code="verification_conflict",
                      uncertainties=["运算含义需要进一步确认。"])
    with _recognized_case(monkeypatch, page, primary, fallback=fallback) as (flow, requests, ctx):
        result = flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]
    assert result.data["verdict"] == "unable_to_judge"


def test_source_readback_conflict_escalates_and_cannot_be_approved(monkeypatch):
    page = _page("11")
    primary = _reply(page, verdict="incorrect", correct_answer="12", recognition_consistent=False)
    primary["readback"].update(answer_text=None, answer_status="blank")
    fallback = deepcopy(primary)
    fallback.update(verdict="unable_to_judge", reason_code="recognition_conflict")
    with _recognized_case(monkeypatch, page, primary, fallback=fallback) as (flow, requests, ctx):
        original = deepcopy(ctx)
        result = flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]
    assert result.data["verdict"] == "unable_to_judge"
    assert result.data["evidence_verified"] is False
    assert result.data["correct_answer"] is None
    assert ctx == original


@pytest.mark.parametrize("changed_field", ["stem_text", "answer_text", "options", "blank_count", "answer_slots"])
def test_source_evidence_must_match_current_question_and_answer(monkeypatch, changed_field):
    page = _page()
    primary = _reply(page)
    if changed_field == "stem_text":
        primary["readback"]["stem_text"] = "计算6+6。"
    elif changed_field == "answer_text":
        primary["readback"]["answer_text"] = "12.0"
    elif changed_field == "answer_slots":
        primary["answer_checks"] = [{"index": 1, "prompt": "完整作答", "student_answer": "12",
                                     "verdict": "correct", "correct_answer": "12", "explanation": "7+5=12。"}]
    with _recognized_case(monkeypatch, page, primary) as (flow, requests, ctx):
        if changed_field == "stem_text":
            ctx["question"]["stem_text"] = "计算6+6。"
        elif changed_field == "answer_text":
            ctx["attempt"]["answer_text"] = "12.0"
        elif changed_field == "options":
            ctx["question"]["options"] = [{"label": "A", "text": "12"}]
        elif changed_field == "blank_count":
            ctx["question"]["blank_count"] = 1
        else:
            ctx["attempt"]["answer_slots"] = [{"index": 1, "prompt": "完整作答",
                                                "answer_text": "12", "answer_status": "present"}]
        flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]


def test_source_evidence_cannot_be_reused_for_changed_image_bytes(monkeypatch):
    page = _page()
    primary = _reply(page)
    with _recognized_case(monkeypatch, page, primary) as (flow, requests, ctx):
        with BytesIO() as output:
            Image.new("RGB", (1, 1), color="red").save(output, format="PNG")
            replacement = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        assert replacement != PNG
        # Keep asset ID and dimensions; provenance must bind the actual page bytes.
        ctx["images"] = (replace(ctx["images"][0], data_url=replacement),)
        flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]


@pytest.mark.parametrize("with_source", [False, True])
def test_gpt_outage_cannot_release_unverified_ds_correct(monkeypatch, with_source):
    page = _page("11" if with_source else "12")
    primary = _reply(page)
    if with_source:
        # The source is trustworthy, but DS's claimed 7+5=11 is not.
        with _recognized_case(monkeypatch, page, primary, fallback=httpx.Response(503)) as (flow, requests, ctx):
            result = flow.check(**ctx)
        assert _check_hosts(requests) == [DS, ASTRA]
    else:
        flow, requests = isolated_factory(monkeypatch, [primary], [httpx.Response(503)])
        with flow:
            result = flow.check(**_context_from_item(page["items"][0], with_source=False))
        assert [request.url.host for request in requests] == [DS, ASTRA]
    assert result.data["verdict"] == "unable_to_judge"
    assert result.data["validity"] == "needs_review"
    assert result.data["evidence_verified"] is False
    assert result.data["correct_answer"] is None
    assert result.data["correction_method"] is None
    assert not result.data.get("answer_checks")
    assert [attempt.code for attempt in result.metadata.attempts] == ["ok", "upstream_unavailable"]


def test_whole_page_astra_handwriting_read_is_retained_and_answer_blind(monkeypatch):
    page = _page("12")
    second = deepcopy(page["items"][0])
    second.update(label="2", stem_text="计算(-12)÷5。", answer_text="-12/5")
    page["items"].append(second)
    review = source_reading(page)
    # Astra must preserve the actually written missing minus, even though it is wrong.
    review["readings"][1]["answer_text"] = "12/5"
    flow, requests = isolated_factory(monkeypatch, [page], [review])
    with flow:
        result = flow.recognize(images=(picture(),))
    assert [request.url.host for request in requests] == [DS, ASTRA]
    assert len(result.metadata.attempts) == 2
    assert result.data["items"][1]["answer_text"] == "12/5"
    assert all(item.get("source_evidence") for item in result.data["items"])
    assert result.data["source_reading_audit"][1]["before"]["answer_text"] == "-12/5"
    targets = request_task(requests[1])["targets"]
    assert len(targets) == 2
    assert all("answer_text" not in target and "correct_answer" not in target for target in targets)
    assert "primary_candidate_for_independent_review" not in request_task(requests[1])
    assert json.loads(requests[1].content)["input"][0]["content"][1]["image_url"] == PNG


def test_failed_independent_source_read_cannot_issue_fast_route_evidence(monkeypatch):
    flow, requests = isolated_factory(monkeypatch, [_page()], [httpx.Response(503)])
    with flow, pytest.raises(ProviderError) as failure:
        flow.recognize(images=(picture(),))
    assert [request.url.host for request in requests] == [DS, ASTRA]
    assert [attempt.code for attempt in failure.value.attempts] == ["ok", "upstream_unavailable"]


@pytest.mark.parametrize("same_homework", [True, False], ids=["same-homework-batches", "different-homework-stays-separate"])
def test_concurrent_source_bound_checks_keep_results_and_usage_bound_to_each_question(monkeypatch, same_homework):
    page = _page("12")
    second = _page("6", stem="计算9-4。")["items"][0]
    second["label"] = "2"
    page["items"].append(second)
    replies = {}
    for item, expected in zip(page["items"], ("12", "5")):
        item["answer_slots"] = [{"index": 1, "prompt": "完整作答",
                                 "answer_text": item["answer_text"], "answer_status": "present"}]
        verdict = "correct" if item["answer_text"] == expected else "incorrect"
        reply = _reply({"items": [item]}, verdict=verdict,
                       correct_answer=expected if verdict == "incorrect" else None,
                       answer_checks=[{"index": 1, "prompt": "完整作答", "student_answer": item["answer_text"],
                                       "verdict": verdict, "correct_answer": expected,
                                       "explanation": "7+5=12。" if item["label"] == "1" else "9-4=5。"}])
        if verdict == "incorrect":
            reply["correction_method"] = "9-4=5。"
        replies[item["stem_text"]] = reply

    requests, request_lock = [], Lock()

    def answer(task):
        observation = task["provisional_transcription"]
        reply = replies[observation["stem_text"]]
        assert observation == reply["readback"]
        assert task["answer_slots"][0]["answer_text"] == reply["answer_checks"][0]["student_answer"]
        return deepcopy(reply)

    def handle(request):
        with request_lock:
            requests.append(request)
            sequence = len(requests)
        body = json.loads(request.content)
        if request.url.host == ASTRA:
            task = request_task(request)
            assert "targets" in task, "only the whole-page Astra source read is allowed"
            response = responses_envelope(source_reading(page))
        else:
            assert request.url.host == DS
            wire = json.loads(body["messages"][1]["content"][0]["text"])
            if "items" in wire:
                items = wire["items"]
                assert len(items) == len({item["id"] for item in items}) == 2
                # Reverse insertion order so positional demultiplexing cannot pass.
                response = envelope({"items": {
                    item["id"]: answer(json.loads(item["text"])["task_input"])
                    for item in reversed(items)
                }})
            elif "provisional_transcription" in wire["task_input"]:
                response = envelope(answer(wire["task_input"]))
            else:
                assert wire["task_input"] == {}
                response = envelope(page)
        response["id"] = f"response_batch_{sequence}"
        return httpx.Response(200, json=response, headers={"x-request-id": f"request_batch_{sequence}"})

    client_type = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_type(
        **{**kwargs, "transport": httpx.MockTransport(handle)}))
    with CorrectionPipeline.from_env(isolated_env()) as flow:
        assert requests == []
        recognized = flow.recognize(images=(picture(),))
        assert [request.url.host for request in requests] == [DS, ASTRA]
        contexts = []
        for index, item in enumerate(recognized.data["items"]):
            ctx = _context_from_item(item, with_source=True)
            question_id, attempt_id = f"question_batch_{index}", f"attempt_batch_{index}"
            ctx["question"].update(question_id=question_id,
                                   homework_id="homework_one" if same_homework or index == 0 else "homework_two")
            ctx["attempt"].update(id=attempt_id, question_id=question_id)
            ctx["input_versions"].update(question_id=question_id, attempt_id=attempt_id,
                                         fingerprint=f"{index + 1:064x}")
            assert source_evidence_matches(
                {image.asset_id: image for image in ctx["images"]}, ctx["question"], ctx["attempt"],
            )
            contexts.append(ctx)
        original = deepcopy(contexts)
        # Allow scheduling slack; assertions concern batching and binding, not a 60ms timer.
        monkeypatch.setattr(flow._check_batcher, "wait_seconds", 0.2)
        barrier = Barrier(2)

        def run(ctx):
            barrier.wait(timeout=3)
            return flow.check(**ctx)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, ctx) for ctx in contexts]
            results = [future.result(timeout=5) for future in futures]

    expected_calls = 1 if same_homework else 2
    assert _check_hosts(requests) == [DS] * expected_calls
    assert contexts == original
    for request in requests[2:]:
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["messages"][1]["content"][1]["image_url"]["url"] == PNG
        wire = json.loads(body["messages"][1]["content"][0]["text"])
        if same_homework:
            assert len(wire["items"]) == 2
            assert {json.loads(item["text"])["task_input"]["provisional_transcription"]["stem_text"]
                    for item in wire["items"]} == {"计算7+5。", "计算9-4。"}
            assert {item["id"] for item in wire["items"]}.isdisjoint(
                ctx["question"]["question_id"] for ctx in contexts)
        else:
            assert "items" not in wire

    assert [result.data["verdict"] for result in results] == ["correct", "incorrect"]
    assert [result.data["readback"]["answer_text"] for result in results] == ["12", "6"]
    assert [result.data["answer_checks"][0]["student_answer"] for result in results] == ["12", "6"]
    assert [result.data["answer_checks"][0]["correct_answer"] for result in results] == ["12", "5"]
    assert results[1].data["correct_answer"] == "5"
    for result, ctx in zip(results, contexts):
        assert result.data["input_versions"] == ctx["input_versions"]
        assert result.data["fast_check_audit"]["source_bound"] is True
        assert result.data["fast_check_audit"]["gpt_review"] is False
        assert result.data["evidence_verified"] is True
        assert len(result.metadata.attempts) == 1
        assert result.metadata.model == flow.client.settings.model

    usages = [result.metadata.usage for result in results if result.metadata.usage is not None]
    attempts = [attempt for result in results for attempt in result.metadata.attempts]
    attempt_usages = [attempt.usage for attempt in attempts if attempt.usage is not None]
    assert len(usages) == len(attempt_usages) == expected_calls
    for recorded in (usages, attempt_usages):
        assert sum(usage.prompt_tokens for usage in recorded) == 100 * expected_calls
        assert sum(usage.completion_tokens for usage in recorded) == 30 * expected_calls
        assert sum(usage.total_tokens for usage in recorded) == 130 * expected_calls
    assert len({result.metadata.request_id for result in results}) == expected_calls
    assert len({attempt.request_id for attempt in attempts}) == expected_calls


@pytest.mark.parametrize("case", ["source_uncertainty", "source_not_ready", "application"])
def test_receipt_bound_uncertain_and_application_checks_disable_ds_thinking_but_keep_gpt(monkeypatch, case):
    page = _page("11")
    item = page["items"][0]
    if case == "source_uncertainty":
        item["uncertainties"] = ["笔迹局部模糊，需要再次核对。"]
    elif case == "source_not_ready":
        item["answer_status"] = "unclear"
    else:
        item.update(question_type="application", stem_text="小明有7支笔，又买了5支，一共有多少支笔？")
    primary = _reply(page, verdict="incorrect", correct_answer="12")
    flow, requests = isolated_factory(monkeypatch, [page, primary], [source_reading(page), primary])
    with flow:
        recognized = flow.recognize(images=(picture(),))
        ctx = _context_from_item(recognized.data["items"][0], with_source=True)
        images = {image.asset_id: image for image in ctx["images"]}
        assert source_evidence_matches(images, ctx["question"], ctx["attempt"], require_ready=False)
        assert source_evidence_matches(images, ctx["question"], ctx["attempt"]) is (case == "application")
        original = deepcopy(ctx)
        result = flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]
    assert json.loads(requests[2].content)["thinking"] == {"type": "disabled"}
    assert len(result.metadata.attempts) == 2
    assert result.data["fast_check_audit"]["primary_mode"] == "quick"
    assert result.data["fast_check_audit"]["source_bound"] is (case == "application")
    assert result.data["fast_check_audit"]["gpt_review"] is True
    assert result.data["verdict"] == "incorrect"
    assert ctx == original


@pytest.mark.parametrize("question_type,answer,verdict,reviewed", [
    ("calculation", "12", "correct", True),
    ("calculation", "11", "incorrect", False),
    ("application", "11", "incorrect", True),
])
def test_legacy_checks_without_receipt_keep_original_thinking_and_review_policy(
    monkeypatch, question_type, answer, verdict, reviewed,
):
    page = _page(answer, question_type=question_type)
    if question_type == "application":
        page["items"][0]["stem_text"] = "小明有7支笔，又买了5支，一共有多少支笔？"
    primary = _reply(page, verdict=verdict, correct_answer="12" if verdict == "incorrect" else None)
    flow, requests = isolated_factory(monkeypatch, [primary], [primary])
    ctx = _context_from_item(page["items"][0], with_source=False)
    assert "_source_evidence" not in ctx["attempt"]
    with flow:
        result = flow.check(**ctx)
    assert json.loads(requests[0].content)["thinking"] == {"type": "enabled"}
    assert [request.url.host for request in requests] == ([DS, ASTRA] if reviewed else [DS])
    assert result.data["verdict"] == verdict
    assert result.data["fast_check_audit"]["primary_mode"] == "standard"
    assert result.data["fast_check_audit"]["source_bound"] is False
    assert result.data["fast_check_audit"]["gpt_review"] is reviewed


@pytest.mark.parametrize("ds_outcome", ["ok", "incomplete_output", "upstream_unavailable"])
def test_astra_returned_model_alias_does_not_repeat_review_and_is_audited(monkeypatch, ds_outcome):
    page = _page("11", question_type="application",
                 stem="小明有7支笔，又买了5支，一共有多少支笔？")
    candidate = _reply(page, verdict="incorrect", correct_answer="12")
    primary = candidate
    if ds_outcome == "incomplete_output":
        primary = httpx.Response(200, json=envelope(candidate, finish="length"))
    elif ds_outcome == "upstream_unavailable":
        primary = httpx.Response(503)
    alias_reply = responses_envelope(candidate)
    alias_reply["model"] = "igpt-6-astra"
    # A spare valid reply makes duplicate escalation fail the call-count assertion,
    # rather than hiding the regression behind an exhausted mock iterator.
    flow, requests = isolated_factory(monkeypatch, [page, primary], [
        source_reading(page), httpx.Response(200, json=alias_reply), httpx.Response(200, json=alias_reply),
    ])
    with flow:
        recognized = flow.recognize(images=(picture(),))
        ctx = _context_from_item(recognized.data["items"][0], with_source=True)
        result = flow.check(**ctx)
    assert _check_hosts(requests) == [DS, ASTRA]
    assert json.loads(requests[2].content)["thinking"] == {"type": "disabled"}
    assert json.loads(requests[3].content)["model"] == "gpt-6-astra"
    assert result.metadata.model == "igpt-6-astra"
    assert result.metadata.attempts[-1].model == "igpt-6-astra"
    assert [attempt.code for attempt in result.metadata.attempts] == [ds_outcome, "ok"]
    assert result.data["fast_check_audit"]["gpt_review"] is True
    assert result.data["verdict"] == "incorrect"
    assert result.data["correct_answer"] == "12"
