"""Both production Ark clients keep practice calls bounded; all HTTP is mocked."""
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
import pytest

from test_correction_flow import flow  # noqa: F401
from test_student_variants import Client, candidate, queue, run, source, verification
from xueban_backend import practice_generation
from xueban_backend.providers.ark import ArkClient, ArkSettings
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.user_original.ark import ArkClient as UserOriginalArkClient


@pytest.fixture(params=[ArkClient, UserOriginalArkClient], ids=["shared", "user-original"])
def correction_client(request):
    def unexpected_request(_request):
        pytest.fail("Practice must not call the unbounded correction client")

    settings = ArkSettings(
        base_url="https://synthetic-learning.invalid/v1", api_key="synthetic-learning-key",
        timeout_seconds=180, total_timeout_seconds=200, max_attempts=3,
        max_concurrency=2, max_tokens=8192,
    )
    with request.param(settings, transport=httpx.MockTransport(unexpected_request)) as client:
        yield client


def test_both_real_clients_get_owned_35_second_wrapper(correction_client):
    original_settings = correction_client.settings
    client, owned = practice_generation.bounded_client(correction_client)
    try:
        assert owned is True
        assert type(client) is BoundedChatClient
        assert client.settings == replace(
            original_settings, timeout_seconds=35, total_timeout_seconds=35,
            max_attempts=1, max_tokens=3072, max_concurrency=1,
        )
    finally:
        if owned:
            client.close()
    assert correction_client.settings is original_settings
    assert correction_client._closed is False


@pytest.mark.parametrize("rate_limited", [False, True], ids=["success", "rate-limited"])
def test_variant_generation_uses_and_closes_only_bounded_client(
        flow, correction_client, monkeypatch, rate_limited):
    task = queue(flow, source(flow))
    generated = candidate()
    outputs = [generated, verification(generated)]
    requests, wrappers = [], []

    def serve(request):
        requests.append(request)
        assert str(request.url) == correction_client.settings.base_url + "/chat/completions"
        assert request.headers["authorization"] == "Bearer " + correction_client.settings.api_key
        assert json.loads(request.content)["max_tokens"] == 3072
        assert 0 < request.extensions["timeout"]["read"] <= 35
        if rate_limited:
            return httpx.Response(429)
        return httpx.Response(200, json={
            "id": "chat_learning", "model": correction_client.settings.model,
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(outputs.pop(0)),
            }}],
        })

    def create_bounded(settings):
        client = BoundedChatClient(settings, transport=httpx.MockTransport(serve))
        wrappers.append(client)
        return client

    monkeypatch.setattr(practice_generation, "BoundedChatClient", create_bounded)
    outcome = run(flow, task, correction_client)
    assert outcome["status"] == ("failed" if rate_limited else "ready")
    if rate_limited:
        assert outcome["failure_code"] == "provider_unavailable"
    assert len(requests) == (1 if rate_limited else 2)
    assert len(wrappers) == 1
    assert wrappers[0]._closed is True
    assert correction_client._closed is False
    assert correction_client.settings.total_timeout_seconds == 200


def test_fake_client_keeps_injected_identity_and_is_not_owned(monkeypatch):
    def unexpected_wrapper(_settings):
        pytest.fail("Unrecognized clients must not supply credentials to a real wrapper")

    monkeypatch.setattr(practice_generation, "BoundedChatClient", unexpected_wrapper)
    # Even carrying the real settings type does not make an object an Ark client.
    for injected in (Client([]), SimpleNamespace(settings=ArkSettings(
            base_url="https://synthetic-injected.invalid/v1", api_key="synthetic-injected-key"))):
        client, owned = practice_generation.bounded_client(injected)
        assert client is injected
        assert owned is False
