"""Offline gateway protocol tests; no user photo, private config or API access."""

from copy import deepcopy
import json
import logging

import httpx
import pytest

from scripts import vision_benchmark as benchmark


BASE = "https://gateway.example.invalid/v1"
SECRET = "synthetic-key-not-a-real-credential"
MODELS = ("gpt-6-astra", "gpt-6.1-sol")
JPEG_BYTES = b"\xff\xd8\xffsynthetic-jpeg-magic-for-protocol-tests-only"


def candidate():
    return {"page_note": "Synthetic test", "items": [{
        "label": "1", "question": "2 + 3 = ?", "student_answer": "5", "correct_answer": "5",
        "verdict": "correct", "reason": "2 + 3 = 5", "uncertainties": [],
    }], "excluded_regions": []}


def responses(content=None, **overrides):
    return {
        "id": "resp_synthetic", "model": MODELS[0], "status": "completed", "error": None,
        "incomplete_details": None,
        "output": [
            {"type": "reasoning", "id": "reasoning_synthetic", "summary": [{"type": "summary_text", "text": "DO_NOT_RETAIN_INTERNAL_REASONING"}]},
            {"type": "message", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": json.dumps(candidate()) if content is None else content},
            ]},
        ],
        "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20, "output_tokens_details": {"reasoning_tokens": 3}},
        **overrides,
    }


def chat(content=None, *, finish_reason="stop", **overrides):
    return {"id": "chatcmpl_synthetic", "model": "synthetic-chat-model",
        "choices": [{"finish_reason": finish_reason, "message": {"role": "assistant", "content": json.dumps(candidate()) if content is None else content}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}, **overrides}


@pytest.fixture
def synthetic_image(tmp_path):
    path = tmp_path / "synthetic.jpg"
    path.write_bytes(JPEG_BYTES)
    return path


def probe(synthetic_image, envelope, protocol="responses", *, http_status=200, headers=None, checkpoint=None):
    snapshots = []
    requests = []

    def save(report):
        snapshots.append(deepcopy(report))
        if checkpoint:
            checkpoint(report)

    def reply(request):
        requests.append(request)
        assert snapshots and snapshots[0]["status"] == "started"
        return httpx.Response(http_status, json=envelope, headers=headers)

    report = benchmark.run_probe(BASE, SECRET, MODELS[0], protocol, synthetic_image, save, transport=httpx.MockTransport(reply))
    return report, snapshots, requests


@pytest.mark.parametrize("model", MODELS)
def test_responses_request_uses_official_image_and_strict_output_shape(model):
    url = "data:image/jpeg;base64,/9j/c3ludGhldGlj"
    body = benchmark.request_body(model, "responses", url, max_tokens=4096)
    assert body["model"] == model
    assert body["input"][0]["role"] == "user"
    content = body["input"][0]["content"]
    assert content[0]["type"] == "input_text"
    assert content[1] == {"type": "input_image", "image_url": url, "detail": "high"}
    assert body["text"]["format"] == {"type": "json_schema", "name": "homework_observation", "strict": True, "schema": benchmark.SCHEMA}
    assert body["reasoning"] == {"effort": "medium"}
    assert body["max_output_tokens"] == 4096
    assert body["store"] is False and body["stream"] is False
    assert not ({"messages", "response_format", "max_tokens", "thinking"} & set(body))
    assert benchmark.SCHEMA["additionalProperties"] is False
    assert set(benchmark.SCHEMA["required"]) == set(benchmark.SCHEMA["properties"])


def test_chat_request_keeps_chat_image_shape_separate():
    url = "data:image/jpeg;base64,/9j/c3ludGhldGlj"
    body = benchmark.request_body("synthetic-chat-model", "chat", url, max_tokens=4096)
    assert body["messages"][1]["content"][1] == {"type": "image_url", "image_url": {"url": url}}
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 4096
    assert not ({"input", "text", "max_output_tokens", "reasoning"} & set(body))


@pytest.mark.parametrize("protocol", ["responses", "chat"])
def test_probe_checkpoints_before_request_and_keeps_only_candidate_evidence(synthetic_image, protocol, caplog):
    caplog.set_level(logging.INFO)
    body = responses() if protocol == "responses" else chat()
    report, snapshots, requests = probe(synthetic_image, body, protocol, headers={"x-request-id": "request_synthetic"})
    assert len(requests) == 1
    expected_path = "/v1/responses" if protocol == "responses" else "/v1/chat/completions"
    assert requests[0].url.path == expected_path
    assert requests[0].headers["Authorization"] == "Bearer " + SECRET
    assert snapshots[0]["status"] == "started"
    assert snapshots[-1]["status"] == report["status"] == "candidate_received"
    assert report["candidate"] == candidate()
    assert report["model_provenance"] == "gateway_claim_only"
    assert report["release_approval"] == "not_assessed"
    assert report["cost"]["amount"] is None
    assert report["request_parameters"]["automatic_retries"] == 0
    assert report["request_id"] == "request_synthetic"
    assert report["usage"] == body["usage"]
    assert "DO_NOT_RETAIN_INTERNAL_REASONING" not in json.dumps(report)
    assert SECRET not in caplog.text and SECRET not in json.dumps(report)


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress", "queued", None])
def test_responses_noncompleted_status_never_returns_success(synthetic_image, status):
    report, snapshots, _ = probe(synthetic_image, responses(status=status, incomplete_details={"reason": "max_output_tokens"}))
    assert report["status"] == "failed"
    assert report["failure_code"] == "response_not_completed"
    assert "candidate" not in report
    assert snapshots[-1]["incomplete_details"] == {"reason": "max_output_tokens"}
    assert report["usage"]["total_tokens"] == 20


def test_contradictory_completed_with_incomplete_details_is_rejected(synthetic_image):
    report, _, _ = probe(synthetic_image, responses(incomplete_details={"reason": "max_output_tokens"}))
    assert report["status"] == "failed"
    assert "candidate" not in report


def test_refusal_is_rejected_even_when_top_level_is_completed(synthetic_image):
    body = responses()
    body["output"][1]["content"] = [{"type": "refusal", "refusal": "Synthetic refusal"}]
    report, _, _ = probe(synthetic_image, body)
    assert report["status"] == "failed"
    assert report["failure_code"] == "model_refusal"
    assert "candidate" not in report


def test_unrequested_tool_output_is_rejected_even_with_valid_text(synthetic_image):
    body = responses()
    body["output"].insert(1, {"type": "function_call", "name": "unexpected", "arguments": "{}"})
    report, _, _ = probe(synthetic_image, body)
    assert report["status"] == "failed"
    assert report["failure_code"] == "unexpected_nontext_output"
    assert "candidate" not in report


@pytest.mark.parametrize("status", [200, 400, 401, 429, 503])
def test_http_or_provider_error_is_recorded_without_retry(synthetic_image, status):
    report, _, requests = probe(synthetic_image, {"error": {"code": "synthetic_failure", "type": "test", "message": "No result"}}, http_status=status)
    assert report["status"] == "failed"
    assert report["failure_code"] == "http_or_provider_error"
    assert report["http_status"] == status
    assert report["provider_error"]["code"] == "synthetic_failure"
    assert len(requests) == 1


@pytest.mark.parametrize("kind", ["missing", "reasoning_only", "empty_text", "message_incomplete", "wrong_role"])
def test_missing_or_nonfinal_response_text_does_not_succeed(synthetic_image, kind):
    body = responses()
    if kind == "missing": del body["output"]
    if kind == "reasoning_only": body["output"] = body["output"][:1]
    if kind == "empty_text": body["output"][1]["content"] = []
    if kind == "message_incomplete": body["output"][1]["status"] = "incomplete"
    if kind == "wrong_role": body["output"][1]["role"] = "user"
    report, _, _ = probe(synthetic_image, body)
    assert report["status"] == "failed"
    assert "candidate" not in report


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", None])
def test_chat_truncation_and_nonstop_finish_are_rejected(synthetic_image, finish_reason):
    report, _, _ = probe(synthetic_image, chat(finish_reason=finish_reason), "chat")
    assert report["status"] == "failed"
    assert report["failure_code"] == "chat_not_completed"
    assert "candidate" not in report


@pytest.mark.parametrize("field,value", [("refusal", "synthetic refusal"), ("tool_calls", [{"id": "call_synthetic"}]), ("function_call", {"name": "fake"})])
def test_chat_nontext_outputs_are_rejected(synthetic_image, field, value):
    body = chat()
    body["choices"][0]["message"][field] = value
    report, _, _ = probe(synthetic_image, body, "chat")
    assert report["status"] == "failed"
    assert report["failure_code"] == "unexpected_nontext_output"


@pytest.mark.parametrize("content", [
    '{}', '[]', '{"page_note":"x","items":[],"excluded_regions":[]}',
    json.dumps({**candidate(), "confirmed": True}),
    json.dumps({**candidate(), "items": [{**candidate()["items"][0], "verdict": "confirmed"}]}),
])
def test_json_that_fails_schema_is_not_a_success(synthetic_image, content):
    report, _, _ = probe(synthetic_image, responses(content))
    assert report["status"] == "failed"
    assert report["failure_code"] == "schema_validation_failed"
    assert report["schema_error_count"] > 0
    assert "candidate" not in report


@pytest.mark.parametrize("content", ['```json\n{}\n```', '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', ''])
def test_invalid_json_is_not_a_success(synthetic_image, content):
    report, _, _ = probe(synthetic_image, responses(content))
    assert report["status"] == "failed"
    assert report["failure_code"] == "invalid_response"
    assert "candidate" not in report


def test_checkpoint_failure_before_request_prevents_spending(synthetic_image):
    def bad_disk(_):
        raise OSError("Synthetic disk failure")

    with pytest.raises(OSError, match="Synthetic disk failure"):
        benchmark.run_probe(BASE, SECRET, MODELS[0], "responses", synthetic_image, bad_disk,
            transport=httpx.MockTransport(lambda _: pytest.fail("Must checkpoint before request")))


def test_timeout_error_message_never_leaks_credentials(synthetic_image, caplog):
    snapshots = []

    def fail(request):
        raise httpx.ReadTimeout("provider accidentally echoed " + SECRET, request=request)

    report = benchmark.run_probe(BASE, SECRET, MODELS[0], "responses", synthetic_image,
        lambda value: snapshots.append(deepcopy(value)), transport=httpx.MockTransport(fail))
    assert report["status"] == "failed" and report["failure_code"] == "ReadTimeout"
    assert SECRET not in json.dumps(report) and SECRET not in caplog.text
    assert snapshots[-1]["status"] == "failed"


def test_json_escaped_credential_echo_never_reaches_report_or_stdout(synthetic_image, tmp_path, monkeypatch, capsys):
    body = responses(model=SECRET)
    raw = json.dumps(body).replace(SECRET, "".join("\\u%04x" % ord(char) for char in SECRET))
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=raw.encode()))
    real_client = httpx.Client

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(benchmark.httpx, "Client", mock_client)
    monkeypatch.setattr(benchmark, "read_config", lambda _: (BASE, SECRET))
    report_path = tmp_path / "synthetic-report.json"
    exit_code = benchmark.main(["--live", "--env-file", str(tmp_path / "never-read.env"),
        "--image", str(synthetic_image), "--output", str(report_path), "--model", MODELS[0], "--protocol", "responses"])
    assert exit_code == 1
    assert SECRET not in capsys.readouterr().out
    assert SECRET not in report_path.read_text()
    assert json.loads(report_path.read_text())["failure_code"] == "provider_credential_echo"


def test_double_json_encoded_credential_is_removed_before_final_checkpoint(synthetic_image):
    inner = json.dumps({**candidate(), "page_note": SECRET})
    inner = inner.replace(SECRET, "".join("\\u%04x" % ord(char) for char in SECRET))
    report, snapshots, _ = probe(synthetic_image, responses(inner))
    assert report["status"] == "failed"
    assert report["failure_code"] == "provider_credential_echo"
    assert "final_text" not in report and "candidate" not in report
    assert SECRET not in json.dumps(snapshots)


def test_excessively_nested_provider_envelope_is_a_recorded_failure(synthetic_image):
    # JSON decoding can succeed while a recursive traversal exceeds Python's
    # stack limit. An untrusted envelope must still produce a terminal report.
    nested = "value"
    for _ in range(600):
        nested = [nested]
    body = responses(untrusted_extension=nested)
    report, snapshots, _ = probe(synthetic_image, body)
    assert report["status"] == "failed"
    assert report["failure_code"] == "invalid_response"
    assert snapshots[-1]["status"] == "failed"


def sse_event(event, *, crlf=False):
    newline = "\r\n" if crlf else "\n"
    return ("event: " + event["type"] + newline + "data: " + json.dumps(event, ensure_ascii=False) + newline * 2).encode("utf-8")


def sse_probe(synthetic_image, chunks):
    snapshots = []
    requests = []
    closed = []

    class ChunkStream(httpx.SyncByteStream):
        def __iter__(self):
            yield from chunks

        def close(self):
            closed.append(True)

    def reply(request):
        requests.append(request)
        assert snapshots[0]["status"] == "started"
        return httpx.Response(200, stream=ChunkStream(), headers={"content-type": "text/event-stream; charset=utf-8", "x-request-id": "request_sse_synthetic"})

    report = benchmark.run_probe(BASE, SECRET, MODELS[0], "responses", synthetic_image,
        lambda value: snapshots.append(deepcopy(value)), transport=httpx.MockTransport(reply), stream=True)
    return report, snapshots, requests, closed


def test_sse_crosses_utf8_and_crlf_chunk_boundaries_and_omits_deltas(synthetic_image):
    final_candidate = {**candidate(), "page_note": "合成中文分块测试"}
    final_response = responses(json.dumps(final_candidate, ensure_ascii=False))
    wire = b": synthetic keepalive\r\n\r\n" + b"".join([
        sse_event({"type": "response.reasoning_summary_text.delta", "delta": "UNSAVED_REASONING_片段"}, crlf=True),
        sse_event({"type": "response.output_text.delta", "delta": "UNSAVED_PARTIAL_DELTA"}, crlf=True),
        sse_event({"type": "response.completed", "response": final_response}, crlf=True),
        b"data: [DONE]\r\n\r\n",
    ])
    # One byte per chunk forces both UTF-8 and CR/LF pairs to span chunks.
    report, snapshots, requests, closed = sse_probe(synthetic_image, (wire[i:i + 1] for i in range(len(wire))))
    assert report["status"] == "candidate_received"
    assert report["candidate"] == final_candidate
    assert report["received_sse"] is True
    assert report["request_parameters"]["stream"] is True
    assert json.loads(requests[0].content)["stream"] is True
    assert len(requests) == 1 and closed
    serialized = json.dumps(snapshots, ensure_ascii=False)
    assert "UNSAVED_REASONING" not in serialized
    assert "UNSAVED_PARTIAL_DELTA" not in serialized
    assert "DO_NOT_RETAIN_INTERNAL_REASONING" not in serialized
    assert SECRET not in serialized


def test_sse_completed_returns_without_waiting_for_connection_close(synthetic_image):
    def no_more_reads():
        yield sse_event({"type": "response.completed", "response": responses()})
        pytest.fail("Completed event must end parsing without waiting for another chunk or DONE")

    report, _, _, closed = sse_probe(synthetic_image, no_more_reads())
    assert report["status"] == "candidate_received"
    assert closed


@pytest.mark.parametrize("status", ["incomplete", "failed"])
def test_sse_noncompleted_terminal_event_preserves_usage_but_never_succeeds(synthetic_image, status):
    body = responses(status=status, incomplete_details={"reason": "max_output_tokens"} if status == "incomplete" else None)
    report, snapshots, _, closed = sse_probe(synthetic_image, [sse_event({"type": "response." + status, "response": body})])
    assert report["status"] == snapshots[-1]["status"] == "failed"
    assert report["response_status"] == status
    assert report["usage"]["total_tokens"] == 20
    assert "candidate" not in report and closed


@pytest.mark.parametrize("event_type", ["response.incomplete", "response.failed"])
def test_sse_terminal_event_cannot_disguise_failure_with_completed_response(synthetic_image, event_type):
    report, _, _, _ = sse_probe(synthetic_image, [sse_event({"type": event_type, "response": responses()})])
    assert report["status"] == "failed"
    assert "candidate" not in report


@pytest.mark.parametrize("wire", [
    b"data: [DONE]\n\n",
    sse_event({"type": "response.output_text.delta", "delta": '{"partial":true}'}),
    sse_event({"type": "response.completed", "response": responses()})[:-2],
])
def test_sse_done_or_clean_disconnect_without_terminal_event_is_failure(synthetic_image, wire):
    report, snapshots, _, closed = sse_probe(synthetic_image, [wire])
    assert report["status"] == "failed"
    assert report["failure_code"] == "sse_missing_terminal_response"
    assert snapshots[-1]["status"] == "failed"
    assert "candidate" not in report and closed


def test_sse_transport_disconnect_is_recorded_without_retaining_partial_text(synthetic_image, caplog):
    def disconnected():
        yield sse_event({"type": "response.output_text.delta", "delta": "UNSAVED_BEFORE_DISCONNECT"})
        raise httpx.ReadError("Synthetic disconnected transport " + SECRET)

    report, snapshots, requests, closed = sse_probe(synthetic_image, disconnected())
    assert report["status"] == "failed" and report["failure_code"] == "ReadError"
    assert len(requests) == 1 and closed
    assert "UNSAVED_BEFORE_DISCONNECT" not in json.dumps(snapshots)
    assert SECRET not in json.dumps(report) and SECRET not in caplog.text


def test_sse_error_event_records_provider_failure(synthetic_image):
    wire = sse_event({"type": "error", "code": "synthetic_overload", "message": "Synthetic failure"})
    report, _, _, closed = sse_probe(synthetic_image, [wire])
    assert report["status"] == "failed" and report["failure_code"] == "http_or_provider_error"
    assert report["provider_error"]["code"] == "synthetic_overload"
    assert "candidate" not in report and closed


def test_sse_size_limit_counts_discarded_delta_bytes(synthetic_image):
    wire = sse_event({"type": "response.output_text.delta", "delta": "x" * 2_000_001})
    report, _, _, closed = sse_probe(synthetic_image, [wire])
    assert report["status"] == "failed" and report["failure_code"] == "response_too_large"
    assert "final_text" not in report and closed


def test_sse_elapsed_guard_still_stops_before_accepting_a_late_completed_event(synthetic_image, monkeypatch):
    clock = {"value": 100.0}
    monkeypatch.setattr(benchmark.time, "monotonic", lambda: clock["value"])

    def late_result():
        clock["value"] += 241.0
        yield sse_event({"type": "response.completed", "response": responses()})

    report, _, _, closed = sse_probe(synthetic_image, late_result())
    assert report["status"] == "failed" and report["failure_code"] == "probe_deadline_exceeded"
    assert report["elapsed_ms"] == 241000
    assert "candidate" not in report and closed
