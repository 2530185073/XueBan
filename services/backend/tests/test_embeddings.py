"""No live network/credentials: all examples are synthetic curriculum text."""

from dataclasses import replace
import json
import math

import httpx
import pytest

from xueban_backend.providers.embeddings import EmbeddingClient, EmbeddingError, EmbeddingSettings, MODEL_ID


BASE = "https://embedding.example.invalid/api/v3"
KEY = "synthetic-test-token"
TEXT = "合成教材片段：等式两边同时加上同一个数，等式仍然成立。"


def settings(**kwargs):
    return replace(EmbeddingSettings(base_url=BASE, api_key=KEY), **kwargs)


def envelope(dimension=2048):
    return {"id": "response-synthetic-1", "object": "list", "model": MODEL_ID, "created": 1, "data": {"object": "embedding", "embedding": [1.0, -2.0] + [0.0] * (dimension - 2)}, "usage": {"prompt_tokens": 18, "total_tokens": 18, "prompt_tokens_details": {"text_tokens": 18, "image_tokens": 0}}}


@pytest.mark.parametrize("dimension", [1024, 2048])
def test_official_payload_and_object_response_for_query_and_corpus(dimension):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url == BASE + "/embeddings/multimodal"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert request.extensions["timeout"]["read"] == 20.0
        return httpx.Response(200, json=envelope(dimension), headers={"x-request-id": "trace-synthetic"})

    with EmbeddingClient(settings(dimensions=dimension), transport=httpx.MockTransport(handler)) as client:
        corpus = client.embed_document(TEXT, index_space=client.space)
        query = client.embed_query("合成检索问题：等式的基本性质是什么？", index_space=corpus.space)
    bodies = [json.loads(request.content) for request in requests]
    assert bodies[0] == {"model": MODEL_ID, "input": [{"type": "text", "text": TEXT}], "encoding_format": "float", "dimensions": dimension, "instructions": "Instruction:Compress the text into one word.\nQuery:"}
    assert bodies[1]["instructions"].startswith("Target_modality: text.\nInstruction:")
    assert bodies[1]["instructions"].endswith("\nQuery:")
    assert query.space == corpus.space
    assert query.role == "query" and corpus.role == "corpus"
    assert query.model == MODEL_ID and query.dimension == dimension
    assert math.isclose(math.fsum(value * value for value in query.embedding), 1.0)
    assert query.metadata.request_id == "trace-synthetic"
    assert query.metadata.response_id == "response-synthetic-1"
    assert query.metadata.usage.text_tokens == 18
    assert query.metadata.usage.total_tokens == 18
    assert query.metadata.latency_ms >= 0
    assert query.metadata.cost is None and query.metadata.charge_status == "unknown"
    assert KEY not in repr(client.settings) and BASE not in repr(client.settings)
    assert TEXT not in repr(query) and "embedding=(" not in repr(query)


def test_missing_usage_and_header_are_explicitly_unknown_not_zero_cost():
    body = envelope()
    del body["usage"]
    with EmbeddingClient(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        result = client.embed_query(TEXT)
    assert result.metadata.usage is None
    assert result.metadata.request_id == body["id"]
    assert result.metadata.cost is None and result.metadata.charge_status == "unknown"


@pytest.mark.parametrize("status,code", [(301, "embedding_redirect_refused"), (307, "embedding_redirect_refused"), (400, "embedding_upstream_rejected"), (401, "embedding_unauthorized"), (403, "embedding_forbidden"), (404, "embedding_not_available"), (429, "embedding_rate_limited"), (503, "embedding_upstream_unavailable")])
def test_http_errors_do_not_follow_redirect_or_retry_or_leak(status, code, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://other.example.invalid/steal", "x-request-id": KEY}, text=KEY + TEXT)

    with EmbeddingClient(settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError) as caught:
            client.embed_query(TEXT)
    assert caught.value.code == code and len(calls) == 1
    assert caught.value.metadata.status_code == status
    assert caught.value.metadata.request_id is None
    assert caught.value.metadata.cost is None
    assert caught.value.metadata.charge_status == "unknown"
    assert KEY not in str(caught.value) + repr(caught.value.metadata) + caplog.text
    assert TEXT not in str(caught.value) + caplog.text


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "embedding_timeout"), (httpx.ConnectError, "embedding_transport_error")])
def test_network_failure_is_unknown_charge_and_not_retried(exception, code):
    calls = []

    def handler(request):
        calls.append(request)
        raise exception(KEY + TEXT, request=request)

    with EmbeddingClient(settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError) as caught:
            client.embed_query(TEXT)
    assert caught.value.code == code and len(calls) == 1
    assert caught.value.metadata.charge_status == "unknown"
    assert caught.value.metadata.cost is None
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("bad_vector,code", [([], "embedding_dimension_mismatch"), ([1.0] * 1024, "embedding_dimension_mismatch"), ([0.0] * 2048, "embedding_zero_vector"), ([True] + [0.0] * 2047, "invalid_embedding_response"), (["1"] + [0.0] * 2047, "invalid_embedding_response"), ([None] + [0.0] * 2047, "invalid_embedding_response")])
def test_bad_or_mismatched_vector_rejected(bad_vector, code):
    body = envelope()
    body["data"]["embedding"] = bad_vector
    with EmbeddingClient(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(EmbeddingError) as caught:
            client.embed_query(TEXT)
    assert caught.value.code == code
    assert caught.value.metadata.usage.total_tokens == 18


@pytest.mark.parametrize("invalid_number", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_non_finite_json_rejected(invalid_number):
    raw = json.dumps(envelope()).replace("1.0", invalid_number, 1)
    with EmbeddingClient(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=raw))) as client:
        with pytest.raises(EmbeddingError, match="^invalid_embedding_response$"):
            client.embed_query(TEXT)


@pytest.mark.parametrize("mutation,code", [
    (lambda body: body.update(model="different-model"), "embedding_model_mismatch"),
    (lambda body: body.update(data=[body["data"]]), "invalid_embedding_response"),
    (lambda body: body.update(id=KEY), "invalid_embedding_response"),
    (lambda body: body["usage"].update(total_tokens=-1), "invalid_embedding_response"),
    (lambda body: body["usage"].update(prompt_tokens=True), "invalid_embedding_response"),
])
def test_invalid_envelope_is_not_treated_as_embedding(mutation, code):
    body = envelope()
    mutation(body)
    with EmbeddingClient(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(EmbeddingError) as caught:
            client.embed_query(TEXT)
    assert caught.value.code == code


@pytest.mark.parametrize("raw", [b"not JSON", b'{"data":{},"data":{}}', b"\xff"])
def test_malformed_or_ambiguous_json_rejected(raw):
    with EmbeddingClient(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=raw))) as client:
        with pytest.raises(EmbeddingError, match="^invalid_embedding_response$"):
            client.embed_query(TEXT)


def test_bounded_response_size():
    with EmbeddingClient(settings(max_response_bytes=1024), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b" " * 1025))) as client:
        with pytest.raises(EmbeddingError, match="^embedding_response_too_large$"):
            client.embed_query(TEXT)


@pytest.mark.parametrize("override", [
    {"base_url": "http://example.invalid/api/v3"}, {"base_url": "https://user:pass@example.invalid/api/v3"},
    {"base_url": BASE + "?"}, {"base_url": BASE + "#"}, {"base_url": BASE + "?token=secret"},
    {"base_url": "https://example.invalid\\evil/api/v3"}, {"base_url": "https://example.invalid/api/v3\n"},
    {"base_url": "https://example.invalid:broken/api/v3"}, {"base_url": "https://example.invalid/incorrect"},
    {"api_key": "a\nb"}, {"api_key": "   "}, {"dimensions": 3072}, {"dimensions": True},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")}, {"timeout_seconds": 0},
    {"total_timeout_seconds": float("nan")}, {"total_timeout_seconds": float("inf")},
    {"total_timeout_seconds": 0}, {"total_timeout_seconds": True}, {"total_timeout_seconds": 121},
    {"model": "deepseek-v4-1-flash-260910"}, {"query_task": ""}, {"query_task": "line\nbreak"},
])
def test_bad_configuration_rejected_before_io(override):
    with pytest.raises(EmbeddingError, match="^invalid_embedding_configuration$"):
        EmbeddingClient(settings(**override), transport=httpx.MockTransport(lambda _: pytest.fail("network called")))


@pytest.mark.parametrize("override", [{"api_key": ""}, {"base_url": ""}])
def test_missing_configuration_disables_client(override):
    with EmbeddingClient(settings(**override), transport=httpx.MockTransport(lambda _: pytest.fail("network called"))) as client:
        with pytest.raises(EmbeddingError, match="^embedding_disabled$"):
            client.embed_query(TEXT)


@pytest.mark.parametrize("text", ["", "   ", None, [TEXT], "a" * 65_537, "\ud800"])
def test_invalid_text_rejected_before_io(text):
    with EmbeddingClient(settings(), transport=httpx.MockTransport(lambda _: pytest.fail("network called"))) as client:
        with pytest.raises(EmbeddingError, match="^invalid_embedding_input$"):
            client.embed_query(text)


@pytest.mark.parametrize("override", [{"dimensions": 1024}, {"query_task": "Retrieve related definitions."}, {"base_url": "https://other.example.invalid/api/v3"}])
def test_incompatible_index_is_rejected_before_request(override):
    transport = httpx.MockTransport(lambda _: pytest.fail("network called"))
    with EmbeddingClient(settings(), transport=transport) as original:
        index_space = original.space
    with EmbeddingClient(settings(**override), transport=transport) as client:
        with pytest.raises(EmbeddingError, match="^incompatible_embedding_space$"):
            client.embed_query(TEXT, index_space=index_space)
        with pytest.raises(EmbeddingError, match="^incompatible_embedding_space$"):
            client.embed_document(TEXT, index_space=index_space)


def test_different_credentials_do_not_change_embedding_space_and_closed_client_fails():
    transport = httpx.MockTransport(lambda _: pytest.fail("network called"))
    with EmbeddingClient(settings(), transport=transport) as first:
        original_space = first.space
    with EmbeddingClient(settings(api_key="another-synthetic-token", base_url=BASE + "/"), transport=transport) as second:
        original_space.require_compatible(second.space)
    with pytest.raises(EmbeddingError, match="^embedding_client_closed$"):
        second.embed_query(TEXT)


@pytest.mark.parametrize("value", [1e300, 1e-300])
def test_finite_extreme_values_normalize_without_overflow(value):
    body = envelope()
    body["data"]["embedding"] = [value] * 2048
    with EmbeddingClient(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        result = client.embed_query(TEXT)
    assert all(math.isfinite(value) for value in result.embedding)
    assert math.isclose(math.fsum(value * value for value in result.embedding), 1.0)


@pytest.mark.parametrize("text", ["中" * 33_334, "😀" * 25_001])
def test_utf8_byte_limit_rejects_multibyte_text_below_character_limit(text):
    assert len(text) < 65_536 and len(text.encode("utf-8")) > 100_000
    with EmbeddingClient(settings(), transport=httpx.MockTransport(lambda _: pytest.fail("network called"))) as client:
        with pytest.raises(EmbeddingError, match="^invalid_embedding_input$"):
            client.embed_document(text)


def test_exact_utf8_boundary_is_sent_but_provider_token_limit_still_applies():
    # Deliberately synthetic: a valid byte length does not make 25,000 emoji
    # fit the provider's 8,000-token cap. Only the provider knows its tokenizer.
    text = "😀" * 25_000
    calls = []

    def handler(request):
        calls.append(request)
        assert len(json.loads(request.content)["input"][0]["text"].encode("utf-8")) == 100_000
        return httpx.Response(400, text="synthetic token limit exceeded")

    with EmbeddingClient(settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="^embedding_upstream_rejected$"):
            client.embed_document(text)
    assert len(calls) == 1


def test_slow_response_chunks_respect_total_budget_and_close_stream(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("xueban_backend.providers.embeddings.time.monotonic", lambda: clock[0])

    class SlowStream(httpx.SyncByteStream):
        closed = False
        chunks_read = 0

        def __iter__(self):
            for _ in range(100):
                clock[0] += 0.75
                self.chunks_read += 1
                yield b" "

        def close(self):
            self.closed = True

    stream = SlowStream()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 2.0
        return httpx.Response(200, stream=stream, headers={"x-request-id": "slow-synthetic"})

    with EmbeddingClient(settings(total_timeout_seconds=2.0), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="^embedding_deadline_exceeded$") as caught:
            client.embed_query(TEXT)
    assert stream.chunks_read == 3 and stream.closed
    assert len(calls) == 1
    assert caught.value.metadata.latency_ms == 2250
    assert caught.value.metadata.request_id == "slow-synthetic"
    assert caught.value.metadata.charge_status == "unknown" and caught.value.metadata.cost is None


@pytest.mark.parametrize("raise_timeout", [False, True])
def test_response_headers_or_transport_timeout_after_deadline_cannot_succeed(monkeypatch, raise_timeout):
    clock = [0.0]
    monkeypatch.setattr("xueban_backend.providers.embeddings.time.monotonic", lambda: clock[0])

    def handler(request):
        clock[0] = 3.0
        if raise_timeout:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json=envelope())

    with EmbeddingClient(settings(total_timeout_seconds=2.0), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="^embedding_deadline_exceeded$"):
            client.embed_query(TEXT)


def test_output_parsing_over_budget_does_not_return_vector(monkeypatch):
    from xueban_backend.providers import embeddings

    clock = [0.0]
    monkeypatch.setattr(embeddings.time, "monotonic", lambda: clock[0])
    decode = embeddings._decode

    def delayed_decode(raw):
        clock[0] = 3.0
        return decode(raw)

    monkeypatch.setattr(embeddings, "_decode", delayed_decode)
    with EmbeddingClient(settings(total_timeout_seconds=2.0), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope()))) as client:
        with pytest.raises(EmbeddingError, match="^embedding_deadline_exceeded$") as caught:
            client.embed_query(TEXT)
    assert caught.value.metadata.usage.total_tokens == 18
