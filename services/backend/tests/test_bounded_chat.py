"""Offline Chat response compatibility, diagnostics and fail-closed boundaries."""

import asyncio
from dataclasses import asdict
import json
import time
from types import SimpleNamespace

import httpx
from jsonschema import Draft202012Validator
import pytest

from xueban_backend.providers import bounded_chat
from xueban_backend.providers.ark import ArkSettings, ProviderError
from xueban_backend.providers.bounded_chat import BoundedChatClient


SECRET = "synthetic-bounded-secret"
PRIVATE = "synthetic-private-student-answer"
SCHEMA = {
    "type": "object",
    "properties": {
        "answer_text": {"type": "string"},
        "verdict": {"enum": ["correct", "incorrect", "unable_to_judge"]},
    },
    "required": ["answer_text", "verdict"],
    "additionalProperties": False,
}
DATA = {"answer_text": "-4\n1 2\nx²", "verdict": "incorrect"}


def envelope(content=None, **changes):
    return {
        "id": "chat_synthetic", "model": "glm-5.3",
        "usage": {"prompt_tokens": 11, "completion_tokens": 23, "total_tokens": 34},
        "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(DATA, ensure_ascii=False) if content is None else content,
        }}],
        **changes,
    }


def run(response, *, schema=SCHEMA, settings_changes=None):
    requests = []

    def serve(request):
        requests.append(request)
        return response

    cfg = ArkSettings(base_url="https://synthetic.invalid/v1", api_key=SECRET,
                      model="glm-5.3", max_attempts=3, **(settings_changes or {}))
    with BoundedChatClient(cfg, transport=httpx.MockTransport(serve)) as client:
        try:
            result = client.generate(role="check", text="synthetic untrusted input", output_schema=schema)
        except ProviderError as error:
            result = error
    assert len(requests) == 1  # Even invalid output/429 must not trigger another paid request.
    return result


def failure(response, expected, *, schema=SCHEMA):
    result = run(response, schema=schema)
    assert isinstance(result, ProviderError)
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    diagnostic = attempt.diagnostic
    assert (result.code, diagnostic.layer, diagnostic.rule, diagnostic.path) == expected
    assert attempt.charge_status == "unknown"
    return result


@pytest.mark.parametrize("opening,closing", [
    ("", ""), (" \n", "\n "),
    ("```json\n", "\n```"), ("```JSON\r\n", "\r\n```"),
    (" \n```\n", "\n```\t\n"),
])
def test_complete_json_wrapper_keeps_student_content_and_verdict_exactly(opening, closing):
    result = run(httpx.Response(200, json=envelope(opening + json.dumps(DATA, ensure_ascii=False) + closing)))
    assert result.data == DATA
    assert result.status == "candidate"
    assert result.metadata.usage.total_tokens == 34
    diagnostic = result.metadata.attempts[0].diagnostic
    if "```" in opening:
        assert (diagnostic.layer, diagnostic.rule) == ("candidate_json", "json_fence_removed")
    else:
        assert diagnostic is None


def test_fence_like_student_text_is_not_removed_from_json_string():
    data = {"answer_text": '  ```json\n{"verdict":"correct"}\n```  ', "verdict": "incorrect"}
    result = run(httpx.Response(200, json=envelope("```json\n" + json.dumps(data) + "\n```")))
    assert result.data == data


@pytest.mark.parametrize("content,rule", [
    ('Answer:\n```json\n{"answer_text":"-4","verdict":"incorrect"}\n```', "invalid_json"),
    ('```json\n{"answer_text":"-4","verdict":"incorrect"}\n```\nExplanation', "unsupported_json_fence"),
    ('```python\n{"answer_text":"-4","verdict":"incorrect"}\n```', "unsupported_json_fence"),
    ('```json\n{"answer_text":"-4","verdict":"incorrect"}', "unsupported_json_fence"),
    ('````json\n{"answer_text":"-4","verdict":"incorrect"}\n````', "unsupported_json_fence"),
    ('```json {"answer_text":"-4","verdict":"incorrect"}```', "unsupported_json_fence"),
    ('```json\n{}\n```\n```json\n{}\n```', "invalid_json"),
    ('{"answer_text":"-4","verdict":"incorrect"} {}', "invalid_json"),
    ('<think>private reasoning</think>{"answer_text":"-4","verdict":"incorrect"}', "invalid_json"),
    ('```json\n{"answer_text":"-4","verdict":"incorrect",}\n```', "invalid_json"),
    ('```json\n{"answer_text":"-4","verdict":"incorrect","verdict":"correct"}\n```', "conflicting_duplicate_keys"),
    ('```json\n{"answer_text":NaN,"verdict":"correct"}\n```', "invalid_json"),
    ('```json\n{"answer_text":1e999,"verdict":"correct"}\n```', "invalid_json"),
])
def test_wrapper_compatibility_never_extracts_repairs_or_combines_json(content, rule):
    result = failure(httpx.Response(200, json=envelope(content)),
                     ("invalid_output", "candidate_json", rule, ("choices", 0, "message", "content")))
    assert result.attempts[0].usage.total_tokens == 34


def test_identical_repeated_fields_are_audited_without_changing_any_answer():
    content = '{"answer_text":"-4\\n1 2\\nx²","verdict":"incorrect","verdict":"incorrect"}'
    result = run(httpx.Response(200, json=envelope(content)))
    assert result.data == DATA
    assert result.metadata.attempts[0].diagnostic.rule == "identical_duplicate_keys_removed"


@pytest.mark.parametrize("first,second", [
    ('true', '1'), ('1', '1.0'), ('"-2"', '"2"'), ('"qiáo"', '"qiào"'),
    ('{"x":true}', '{"x":1}'), ('[1,2]', '[2,1]'),
])
def test_conflicting_nested_types_numbers_tones_and_order_are_never_deduplicated(first, second):
    failure(httpx.Response(200, json=envelope('{"value":' + first + ',"value":' + second + '}')),
            ("invalid_output", "candidate_json", "conflicting_duplicate_keys", ("choices", 0, "message", "content")),
            schema={"type": "object"})


def test_identical_duplicate_field_does_not_bypass_schema_or_credentials():
    failure(httpx.Response(200, json=envelope('{"answer_text":"-4","verdict":"unknown","verdict":"unknown"}')),
            ("invalid_output", "candidate_schema", "enum", ("verdict",)))
    failure(httpx.Response(200, json=envelope('{"answer_text":"' + SECRET +
        '","answer_text":"' + SECRET + '","verdict":"correct"}')),
        ("provider_credential_echo", "content_security", "credential_echo", ()))


@pytest.mark.parametrize("candidate,rule,path", [
    ({"answer_text": "-4"}, "required", ()),
    ({"answer_text": "-4", "verdict": "right"}, "enum", ("verdict",)),
    ({"answer_text": None, "verdict": "correct"}, "type", ("answer_text",)),
    ({"answer_text": "-4", "verdict": "correct", "confirmed": True}, "additionalProperties", ()),
    ({"data": DATA}, "required", ()),
    ([DATA], "type", ()),
    (json.dumps(DATA), "type", ()),
])
def test_fenced_candidate_still_requires_every_original_schema_constraint(candidate, rule, path):
    content = "```json\n" + json.dumps(candidate) + "\n```"
    failure(httpx.Response(200, json=envelope(content)), ("invalid_output", "candidate_schema", rule, path))


@pytest.mark.parametrize("raw,rule", [
    (b'{"choices":', "invalid_json"),
    (b'{"choices":[],"choices":[]}', "invalid_json"),
    (b'{"choices":NaN}', "invalid_json"),
    (b'\xff', "invalid_encoding"),
])
def test_envelope_json_is_distinct_from_candidate_json(raw, rule):
    result = failure(httpx.Response(200, content=raw), ("invalid_output", "envelope_json", rule, ()))
    assert result.attempts[0].usage is None


@pytest.mark.parametrize("choices,rule,path", [
    (None, "choices_not_array", ("choices",)),
    ({}, "choices_not_array", ("choices",)),
    ([], "empty_choices", ("choices",)),
    ([{}, {}], "multiple_choices", ("choices",)),
    ([None], "choice_not_object", ("choices", 0)),
])
def test_envelope_shape_failures_keep_usage_and_exact_safe_field_location(choices, rule, path):
    result = failure(httpx.Response(200, json=envelope(choices=choices)),
                     ("invalid_output", "envelope_shape", rule, path))
    assert result.attempts[0].usage.total_tokens == 34


def test_nonobject_envelope_does_not_become_candidate():
    failure(httpx.Response(200, json=[]), ("invalid_output", "envelope_shape", "envelope_not_object", ()))


@pytest.mark.parametrize("message,rule,path", [
    (None, "message_not_object", ("choices", 0, "message")),
    ({}, "missing_content", ("choices", 0, "message", "content")),
    ({"reasoning_content": '{"answer_text":"-4","verdict":"correct"}'}, "missing_content", ("choices", 0, "message", "content")),
    ({"content": None}, "null_content", ("choices", 0, "message", "content")),
    ({"content": [{"type": "text", "text": json.dumps(DATA)}]}, "content_array", ("choices", 0, "message", "content")),
    ({"content": DATA}, "content_not_string", ("choices", 0, "message", "content")),
    ({"content": json.dumps(DATA), "refusal": PRIVATE}, "refused_message", ("choices", 0, "message", "refusal")),
    ({"content": json.dumps(DATA), "tool_calls": [{"name": PRIVATE}]}, "tool_call_message", ("choices", 0, "message", "tool_calls")),
    ({"content": json.dumps(DATA), "function_call": {"name": PRIVATE}}, "function_call_message", ("choices", 0, "message", "function_call")),
])
def test_message_shapes_are_diagnosed_without_using_reasoning_or_tool_results(message, rule, path, caplog):
    response = httpx.Response(200, json=envelope(choices=[{"finish_reason": "stop", "message": message}]))
    result = failure(response, ("invalid_output", "message_shape", rule, path))
    assert PRIVATE not in json.dumps([asdict(a) for a in result.attempts]) + caplog.text


def test_empty_final_content_does_not_fall_back_to_private_reasoning():
    response = envelope(choices=[{"finish_reason": "stop", "message": {
        "content": " \n", "reasoning_content": json.dumps(DATA),
    }}])
    failure(httpx.Response(200, json=response),
            ("invalid_output", "candidate_json", "empty_content", ("choices", 0, "message", "content")))


@pytest.mark.parametrize("finish,rule", [
    ("length", "token_limit"), ("content_filter", "content_filtered"),
    ("tool_calls", "tool_call_finish"), ("function_call", "function_call_finish"),
    (None, "missing_finish_reason"), ({}, "invalid_finish_reason"),
    (PRIVATE, "unsupported_finish_reason"),
])
def test_nonterminal_valid_json_remains_incomplete_with_usage_not_repaired(finish, rule, caplog):
    response = envelope(choices=[{"finish_reason": finish, "message": {"content": json.dumps(DATA)}}])
    result = failure(httpx.Response(200, json=response),
                     ("incomplete_output", "completion", rule, ("choices", 0, "finish_reason")))
    assert result.attempts[0].usage.total_tokens == 34
    assert PRIVATE not in json.dumps([asdict(a) for a in result.attempts]) + caplog.text


def test_malformed_usage_is_not_silently_changed_to_zero():
    result = failure(httpx.Response(200, json=envelope(usage={"total_tokens": PRIVATE})),
                     ("invalid_output", "usage_shape", "invalid_structure", ("usage",)))
    assert result.attempts[0].usage is None


def test_schema_failure_redacts_unknown_field_names_and_values(caplog):
    schema = {"type": "object", "properties": {PRIVATE: {"type": "integer", "description": PRIVATE}}}
    result = failure(httpx.Response(200, json=envelope(json.dumps({PRIVATE: "private value"}))),
                     ("invalid_output", "candidate_schema", "type", ("*",)), schema=schema)
    persisted = str(result) + repr(result.attempts) + caplog.text
    assert PRIVATE not in persisted and "private value" not in persisted and SECRET not in persisted


@pytest.mark.parametrize("escaped", [False, True])
def test_credential_echo_never_enters_candidate_or_diagnostic(escaped, caplog):
    secret = "".join("\\u%04x" % ord(c) for c in SECRET) if escaped else SECRET
    content = '```json\n{"answer_text":"' + secret + '","verdict":"correct"}\n```'
    result = failure(httpx.Response(200, json=envelope(content)),
                     ("provider_credential_echo", "content_security", "credential_echo", ()))
    assert SECRET not in str(result) + repr(result.attempts) + caplog.text


@pytest.mark.parametrize("status,code,rule", [
    (429, "upstream_rate_limited", "rate_limited"),
    (503, "upstream_unavailable", "upstream_unavailable"),
    (401, "upstream_rejected", "http_rejected"),
])
def test_http_failure_stays_transport_failure_without_reading_error_body(status, code, rule):
    result = failure(httpx.Response(status, content=(SECRET + PRIVATE).encode()),
                     (code, "transport", rule, ()))
    assert SECRET not in repr(result.attempts) and PRIVATE not in repr(result.attempts)


def test_response_size_limit_is_distinct_from_truncation_or_invalid_json():
    result = run(httpx.Response(200, content=b" " * 2048), settings_changes={"max_response_bytes": 1024})
    assert isinstance(result, ProviderError)
    assert result.code == "response_too_large"
    attempt = result.attempts[0]
    assert (attempt.diagnostic.layer, attempt.diagnostic.rule) == ("transport", "response_size_limit")
    assert attempt.usage is None


def test_valid_candidate_completed_after_deadline_is_not_released(monkeypatch):
    started = time.monotonic()
    clock = [started]
    monkeypatch.setattr(bounded_chat, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class TimedValidator:
        def validate(self, data):
            Draft202012Validator(SCHEMA).validate(data)
            clock[0] = started + 3

    cfg = ArkSettings(base_url="https://synthetic.invalid/v1", api_key=SECRET, model="glm-5.3")
    with BoundedChatClient(cfg, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=envelope()))) as client:
        with pytest.raises(ProviderError) as error:
            client._generate({}, "check", TimedValidator(), started, started + 2)
    attempt = error.value.attempts[0]
    assert (error.value.code, attempt.diagnostic.layer, attempt.diagnostic.rule) == (
        "deadline_exceeded", "validation", "total_timeout",
    )
    assert attempt.usage.total_tokens == 34


@pytest.mark.parametrize("scenario,code,rule", [
    ("total", "deadline_exceeded", "total_timeout"),
    ("phase", "upstream_timeout", "phase_timeout"),
    ("connection", "upstream_transport_error", "transport_error"),
])
def test_timeout_and_transport_failure_do_not_become_json_errors(scenario, code, rule):
    calls = 0

    async def serve(request):
        nonlocal calls
        calls += 1
        if scenario == "phase":
            raise httpx.ReadTimeout(PRIVATE, request=request)
        if scenario == "connection":
            raise httpx.ConnectError(PRIVATE, request=request)
        await asyncio.sleep(1)
        return httpx.Response(200, json=envelope())

    cfg = ArkSettings(base_url="https://synthetic.invalid/v1", api_key=SECRET,
                      model="glm-5.3", timeout_seconds=1, total_timeout_seconds=.03)
    with BoundedChatClient(cfg, transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(ProviderError) as failure:
            client.generate(role="check", text="synthetic", output_schema=SCHEMA)
    assert calls == 1
    attempt = failure.value.attempts[0]
    assert (failure.value.code, attempt.diagnostic.layer, attempt.diagnostic.rule) == (code, "transport", rule)
    assert PRIVATE not in repr(attempt)
