"""Mocked Responses protocol tests; never load credentials or make paid calls."""

import json
from dataclasses import asdict

import httpx

from xueban_backend.providers import ArkSettings
from xueban_backend.providers.prompts import Role
from xueban_backend.providers.responses import ResponsesClient


SECRET = "synthetic-responses-key-never-real"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
SCHEMA = {
    "type": "object",
    "properties": {"count": {"type": "integer"}, "note": {"type": "string"}},
    "required": ["count"], "additionalProperties": False,
}


def successful_response(text='{"count":6,"note":null}'):
    return {
        "id": "resp_synthetic",
        "model": "gpt-6-astra",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "private reasoning text"}]},
            {"type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100,
                   "input_tokens_details": {"cached_tokens": 4},
                   "output_tokens_details": {"reasoning_tokens": 10}},
    }


def client(reply, requests):
    def handle(request):
        requests.append((request.url.path, request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(200, json=reply)

    return ResponsesClient(ArkSettings(
        base_url="https://synthetic.example.invalid/v1", api_key=SECRET,
        model="gpt-6-astra", max_attempts=1,
    ), transport=httpx.MockTransport(handle), sleep=lambda _: None)


def test_responses_protocol_sends_multimodal_schema_and_drops_optional_nulls():
    requests = []
    with client(successful_response(), requests) as provider:
        result = provider.generate(
            role=Role.CHECK,
            text="Read the worksheet image.",
            output_schema=SCHEMA,
            images=(PNG,),
            thinking=True,
            max_tokens=512,
            instructions="Return a concise candidate.",
        )

    path, authorization, body = requests[0]
    assert path == "/v1/responses"
    assert authorization == "Bearer " + SECRET
    assert body["model"] == "gpt-6-astra"
    assert body["store"] is False
    assert body["reasoning"]["effort"] == "medium"
    assert body["max_output_tokens"] == 512
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"]["required"] == ["count", "note"]
    assert body["input"][0]["content"][1]["type"] == "input_image"
    assert body["input"][0]["content"][1]["image_url"] == PNG
    assert result.role == Role.CHECK
    assert result.data == {"count": 6}
    assert result.metadata.requested_model == result.metadata.model == "gpt-6-astra"
    assert result.metadata.usage.total_tokens == 100
    assert result.metadata.usage.reasoning_tokens == 10


def test_responses_adapter_does_not_return_reasoning_or_echo_credentials():
    requests = []
    reply = successful_response()
    with client(reply, requests) as provider:
        result = provider.generate(role="check", text="Check.", output_schema=SCHEMA)
    persisted = json.dumps({"data": result.data, "metadata": asdict(result.metadata)})
    assert "private reasoning text" not in persisted
    assert SECRET not in persisted
