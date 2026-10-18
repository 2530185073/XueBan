"""Offline Responses SSE and actual deadline tests with synthetic data."""

import asyncio
import json
import time

import httpx
import pytest

from xueban_backend.providers.ark import ArkClient, ArkSettings, ProviderError
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.responses import ResponsesClient


SECRET = "synthetic-private-api-key"
SCHEMA = {"type": "object", "properties": {"observation": {"type": "string"}},
          "required": ["observation"], "additionalProperties": False}


def settings(**changes):
    return ArkSettings(base_url="https://synthetic.invalid/v1", api_key=SECRET,
                       model="gpt-6-astra", max_attempts=1, **changes)


def envelope(**changes):
    return {"id": "resp_synthetic", "model": "gpt-6-astra", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": '{"observation":"visible answer"}'}]}],
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
                      "input_tokens_details": {"cached_tokens": 2}, "output_tokens_details": {"reasoning_tokens": 3}}, **changes}


def sse(value, kind="response.completed"):
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, "response": value}) + "\n\n").encode()


def generate(client):
    return client.generate(role="check", text="untrusted synthetic homework", instructions="Check the visible answer.", output_schema=SCHEMA)


def test_streaming_request_requires_terminal_completed_and_extracts_usage():
    requests = []
    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=sse(envelope()), headers={"content-type": "text/event-stream"})
    with ResponsesClient(settings(), transport=httpx.MockTransport(serve)) as client:
        result = generate(client)
    assert result.data == {"observation": "visible answer"}
    assert result.metadata.usage.total_tokens == 30
    assert result.metadata.usage.cached_tokens == 2
    assert result.metadata.usage.reasoning_tokens == 3
    assert len(result.metadata.attempts) == 1
    assert requests[0]["store"] is False and requests[0]["stream"] is True
    assert requests[0]["text"]["format"]["strict"] is True


@pytest.mark.parametrize("body,code", [
    (b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n', "incomplete_output"),
    (b"data: [DONE]\n\n", "incomplete_output"),
    (sse(envelope(status="incomplete"), "response.incomplete"), "incomplete_output"),
    (sse(envelope(status="incomplete")), "incomplete_output"),
    (sse(envelope(output=[{"type": "message", "role": "assistant", "status": "completed", "content": [{"type":"refusal", "refusal":"no"}]}])), "invalid_output"),
    (sse(envelope(output=[{"type": "function_call", "name": "untrusted"}])), "invalid_output"),
    (sse(envelope(output=[{"type":"message","role":"assistant","status":"completed","content":[{"type":"output_text","text":'{"observation":"a","observation":"b"}'}]}])), "invalid_output"),
    (sse(envelope(id=SECRET)), "provider_credential_echo"),
])
def test_incomplete_refused_or_invalid_stream_never_produces_candidate(body, code):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body, headers={"content-type":"text/event-stream"}))
    with ResponsesClient(settings(), transport=transport) as client, pytest.raises(ProviderError, match="^" + code + "$") as failure:
        generate(client)
    assert len(failure.value.attempts) == 1
    assert SECRET not in repr(failure.value.attempts)


def test_terminal_json_response_is_also_accepted_and_unknown_counts_stay_unknown():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=envelope(usage=None)))
    with ResponsesClient(settings(), transport=transport) as client:
        result = generate(client)
    assert result.metadata.usage is None


@pytest.mark.parametrize("client_type", [ResponsesClient, BoundedChatClient])
def test_total_deadline_cancels_request_waiting_for_headers(client_type):
    async def slow(_):
        await asyncio.sleep(5)
        return httpx.Response(200, json=envelope())
    started = time.monotonic()
    with client_type(settings(timeout_seconds=1, total_timeout_seconds=.03), transport=httpx.MockTransport(slow)) as client:
        with pytest.raises(ProviderError, match="deadline_exceeded") as failure:
            generate(client)
    assert time.monotonic() - started < .5
    assert len(failure.value.attempts) == 1


@pytest.mark.parametrize("client_type", [ResponsesClient, BoundedChatClient])
def test_total_deadline_cancels_a_trickling_stream(client_type):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.01)
                yield b" "
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream()))
    started = time.monotonic()
    with client_type(settings(timeout_seconds=1, total_timeout_seconds=.04), transport=transport) as client:
        with pytest.raises(ProviderError, match="deadline_exceeded"):
            generate(client)
    assert time.monotonic() - started < .5


def test_bounded_chat_retains_paid_usage_on_truncation_and_omits_ark_extension_for_gateway():
    requests = []
    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"model":"kimi-k3", "id":"chat_test", "usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3},
            "choices":[{"finish_reason":"length", "message":{"content":"{}"}}]})
    with BoundedChatClient(settings(), transport=httpx.MockTransport(serve), provider_default_thinking=True) as client:
        with pytest.raises(ProviderError, match="incomplete_output") as failure:
            generate(client)
    assert "thinking" not in requests[0]
    assert failure.value.attempts[0].usage.total_tokens == 3


@pytest.mark.parametrize("phase,total,expected", [(118, 118, 15), (7, 3, 3), (4, 30, 4)])
def test_connection_timeout_is_fifteen_seconds_but_never_exceeds_other_budgets(phase, total, expected):
    timeouts = []
    def serve(request):
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json=envelope())
    with ResponsesClient(settings(timeout_seconds=phase, total_timeout_seconds=total),
                         transport=httpx.MockTransport(serve)) as client:
        generate(client)
    assert timeouts[0]["connect"] == expected


@pytest.mark.parametrize("client_type,field", [(BoundedChatClient, "max_tokens"), (ResponsesClient, "max_output_tokens")])
def test_large_whole_page_budget_is_explicit_and_preserved_in_request(client_type, field):
    requests = []
    def serve(request):
        requests.append(json.loads(request.content))
        data = envelope() if client_type is ResponsesClient else {"model":"synthetic", "id":"chat_synthetic",
            "choices":[{"finish_reason":"stop", "message":{"content":'{"observation":"complete"}'}}]}
        return httpx.Response(200, json=data)
    with client_type(settings(max_tokens=24576), transport=httpx.MockTransport(serve)) as client:
        generate(client)
        client.generate(role="check", text="synthetic", output_schema=SCHEMA, max_tokens=32768)
        with pytest.raises(ProviderError, match="invalid_input"):
            client.generate(role="check", text="synthetic", output_schema=SCHEMA, max_tokens=32769)
    assert [request[field] for request in requests] == [24576, 32768]


def test_legacy_ark_budget_is_not_relaxed_by_multimodel_adapters():
    with pytest.raises(ProviderError, match="invalid_configuration"):
        ArkClient(settings(max_tokens=8193))
    with ArkClient(settings(), transport=httpx.MockTransport(lambda _: pytest.fail("No request allowed"))) as client:
        with pytest.raises(ProviderError, match="invalid_input"):
            client.generate(role="check", text="synthetic", output_schema=SCHEMA, max_tokens=8193)
