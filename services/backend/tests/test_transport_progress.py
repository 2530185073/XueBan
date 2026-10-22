"""Content-free transport diagnostics: local mocked streams, no model calls."""
import asyncio
from dataclasses import asdict
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_partial_output_persistence import make_client
from test_responses_provider import envelope, generate, settings, sse
from test_two_model_pipeline import correct
from xueban_backend.app import create_app
from xueban_backend.correction_models import Task
from xueban_backend.providers.ark import Attempt, ProviderError, TransportDiagnostic
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.bounded_http import read_response
from xueban_backend.providers.responses import ResponsesClient
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), *, hang=False):
        self.chunks = chunks; self.hang = hang; self.closed = False
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.hang:
            await asyncio.sleep(5)
    async def aclose(self): self.closed = True


def local_read(chunks=(), *, hang=False, ctype="text/event-stream", limit=2097152):
    stream = Stream(chunks, hang=hang); requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, stream=stream, headers={"content-type": ctype})
    result = read_response(settings(timeout_seconds=1, total_timeout_seconds=.025, max_response_bytes=limit),
                           "/responses", {}, responses=True, transport=httpx.MockTransport(serve))
    assert len(requests) == 1 and stream.closed
    return result


@pytest.mark.parametrize("client_type", [ResponsesClient, BoundedChatClient])
def test_waiting_for_headers_keeps_single_attempt_progress(client_type):
    calls = []
    async def serve(request):
        calls.append(request); await asyncio.sleep(5)
    with client_type(settings(timeout_seconds=1, total_timeout_seconds=.025), transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(ProviderError) as caught: generate(client)
    [attempt] = caught.value.attempts
    assert attempt.code == "deadline_exceeded" and attempt.status_code is None
    assert attempt.transport.phase == "waiting_headers"
    assert attempt.transport.headers_received_ms is None and attempt.transport.decoded_body_bytes == 0
    assert len(calls) == 1 and attempt.usage is None and attempt.charge_status == "unknown"


def test_headers_then_no_body_is_distinct_from_no_headers():
    result = local_read(hang=True); p = result.progress
    assert result.status == 200 and result.code == "deadline_exceeded" and result.body == b""
    assert p.headers_received_ms is not None and p.first_body_byte_ms is None
    assert p.phase == "reading_sse" and p.decoded_body_bytes == p.sse_events == 0


def test_comments_only_have_bytes_but_not_model_output():
    result = local_read([b": heartbeat\n\n", b": keepalive\r\n\r\n"], hang=True); p = result.progress
    assert result.code == "deadline_exceeded" and result.body == b""
    assert p.first_body_byte_ms is not None and p.last_body_byte_ms >= p.first_body_byte_ms
    assert p.sse_comment_lines == 2 and p.body_chunks_received == 2 and p.decoded_body_bytes > 0
    assert p.sse_events == p.output_text_events == 0 and p.first_sse_data_ms is None


def test_partial_text_or_reasoning_is_never_a_final_candidate_or_retained_text():
    private = "private-reasoning-and-answer-sentinel"
    events = [
        {"type": "response.reasoning_summary_text.delta", "delta": private},
        {"type": "response.output_text.delta", "delta": "visible partial"},
        {"type": private, "payload": private},
    ]
    chunks = [("data: " + json.dumps(x) + "\n\n").encode() for x in events]
    result = local_read(chunks, hang=True); p = result.progress
    assert result.code == "deadline_exceeded" and result.body == b""
    assert p.sse_events == 3 and p.reasoning_events == p.output_text_events == p.other_events == 1
    assert p.output_text_delta_chars == len("visible partial") and p.terminal_event is None
    assert p.first_output_text_ms is not None
    text = json.dumps(asdict(p)) + repr(result)
    assert private not in text and "visible partial" not in text


@pytest.mark.parametrize("kind", [[], {}, 42, None])
def test_unusual_event_type_cannot_become_a_diagnostic_string(kind):
    result = local_read([("data: " + json.dumps({"type": kind, "delta": "private-value"}) + "\n\n").encode()])
    assert result.code != "ok" and result.body == b""
    assert "private-value" not in json.dumps(asdict(result.progress))
    assert result.progress.terminal_event is None


@pytest.mark.parametrize("body,code,terminal,accepted", [
    (b"data: [DONE]\n\n", "incomplete_output", None, False),
    (b"data: not-json-private-sentinel\n\n", "invalid_output", None, False),
    (sse(envelope(status="incomplete")), "incomplete_output", "completed", False),
    (sse(envelope()), "ok", "completed", True),
    (sse(envelope(status="incomplete"), "response.incomplete"), "ok", "incomplete", True),
])
def test_terminal_diagnostics_do_not_relax_parser_rules(body, code, terminal, accepted):
    result = local_read([body]); p = result.progress
    assert result.code == code and p.terminal_event == terminal and p.terminal_accepted is accepted
    assert p.sse_events == 1 and "private-sentinel" not in repr(p)
    if not accepted: assert result.body == b""


def test_missing_event_separator_is_visible_as_pending_framing_not_final_output():
    body = sse(envelope()).rstrip(b"\n") + b"\n"
    result = local_read([body], hang=True)
    assert result.code == "deadline_exceeded" and result.body == b""
    assert result.progress.sse_data_lines == 1 and result.progress.pending_data_lines == 1
    assert result.progress.sse_events == 0 and result.progress.terminal_event is None


def test_oversize_and_non_sse_body_remain_distinguishable():
    result = local_read([b"x" * 1025], limit=1024)
    assert result.code == "response_too_large" and result.progress.decoded_body_bytes == 1025
    result = local_read([b'partial-json'], hang=True, ctype="application/json")
    assert result.code == "deadline_exceeded" and result.progress.content_mode == "body"
    assert result.progress.decoded_body_bytes == 12 and result.progress.sse_events == 0 and result.body == b""


@pytest.mark.parametrize("changes", [{"phase":"untrusted"}, {"terminal_event":"untrusted"},
    {"decoded_body_bytes": "secret"}, {"decoded_body_bytes":True}, {"first_body_byte_ms":-1}])
def test_transport_snapshot_allows_only_fixed_enums_and_numeric_values(changes):
    with pytest.raises(ValueError, match="invalid_transport_diagnostic"): TransportDiagnostic(**changes)


def test_old_attempt_archive_without_transport_still_loads_and_sanitized_error_keeps_counts():
    old = {"number":1,"status_code":None,"code":"upstream_timeout","latency_ms":10}
    assert Attempt(**old).transport is None
    assert asdict(Attempt(**old))["transport"] is None
    # Provider echoes are still rejected before event counters classify them.
    from test_responses_provider import SECRET
    result = local_read([('data: '+json.dumps({'type':'response.output_text.delta','delta':SECRET})+'\n\n').encode()])
    assert result.code == "provider_credential_echo" and result.body == b""
    assert SECRET not in json.dumps(asdict(result.progress)) and result.progress.output_text_events == 0


@pytest.mark.parametrize("client_type", [ResponsesClient, BoundedChatClient])
def test_completed_response_attempt_keeps_transport_without_changing_usage_or_candidate(client_type):
    response = (httpx.Response(200, content=sse(envelope()), headers={"content-type":"text/event-stream"})
                if client_type is ResponsesClient else httpx.Response(200, json={"model":"synthetic","usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30},
                    "choices":[{"finish_reason":"stop","message":{"content":'{"observation":"visible answer"}'}}]}))
    with client_type(settings(), transport=httpx.MockTransport(lambda request: response)) as client: result = generate(client)
    assert result.data == {"observation":"visible answer"} and result.metadata.usage.total_tokens == 30
    [attempt] = result.metadata.attempts
    assert attempt.transport.decoded_body_bytes > 0 and attempt.code == "ok"


def test_stream_timeout_progress_persists_in_worker_audit_but_not_public_api(flow):
    first, first_calls = make_client([correct()], "synthetic-primary")
    calls = []; private = "private-stream-sentinel"
    class TimedOutStream(Stream):
        async def __aiter__(self):
            async for chunk in super().__aiter__():
                yield chunk
            raise httpx.ReadTimeout("synthetic stream interruption")
    # Wall-clock cancellation is tested above. This worker/persistence case must
    # reach the stream before failing: a 25 ms total budget can expire during
    # PAGE_SCHEMA validation on a busy runner, before any HTTP attempt exists.
    def serve(request):
        calls.append(request)
        stream = TimedOutStream([('data: '+json.dumps({'type':'response.output_text.delta','delta':private})+'\n\n').encode()])
        return httpx.Response(200, stream=stream, headers={"content-type":"text/event-stream"})
    second = ResponsesClient(settings(timeout_seconds=1,total_timeout_seconds=5), transport=httpx.MockTransport(serve))
    p = TwoModelWholePagePipeline(first, second, host_call_journal_enabled=True);flow.worker.pipeline = p
    try:
        _,job=flow.recognize();assert flow.worker.run_once()
        with Session(flow.database.engine) as session:
            task=session.get(Task,job['id']);execution=task.audit['executions'][-1]
            attempts=execution['metadata']['attempts'];assert len(attempts)==2
            failed=next(a for a in attempts if a['code']=='upstream_timeout')
            assert failed['transport']['output_text_delta_chars']==len(private)
            assert failed['usage'] is None and failed['charge_status']=='unknown'
            call=next(c for c in execution['host_call_journal']['calls'] if c['stage']=='secondary')
            assert call['metadata']['attempts'][0]['transport']==failed['transport']
            assert private not in json.dumps(task.audit)
        app=create_app(flow.cfg)
        with TestClient(app) as client:
            client.headers.update({'Authorization':'Bearer '+flow.signing.token()})
            public=[client.get('/v1/jobs/'+job['id']).json(),client.get('/v1/homeworks/'+flow.homework_id).json()]
            public += [client.get('/v1/cases/'+case_id).json() for case_id in public[1]['case_ids']]
            assert 'decoded_body_bytes' not in json.dumps(public) and private not in json.dumps(public)
            assert all(c['current_decision']['verdict']=='unable_to_judge' for c in public[2:])
        app.state.engine.dispose()
        assert len(first_calls)==len(calls)==1
    finally:p.close()
