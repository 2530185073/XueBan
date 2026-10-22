"""Explicit second-reader routing and request bodies, without external calls."""
import json
from types import SimpleNamespace

import httpx
import pytest

from test_whole_page_pipeline import picture
from xueban_backend.providers import two_model
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.responses import ResponsesClient


@pytest.mark.parametrize("api,thinking,expected", [
    ("chat", "enabled", {"type": "enabled"}),
    ("chat", "disabled", {"type": "disabled"}),
    ("responses", "enabled", {"effort": "medium"}),
    ("responses", "disabled", {"effort": "low"}),
])
def test_explicit_secondary_config_controls_protocol_and_request_thinking(monkeypatch, api, thinking, expected):
    requests = []

    def reply(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/v1/responses":
            return httpx.Response(200, json={
                "id": "resp_synthetic", "model": body["model"], "status": "completed",
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": '{"items":[]}'}]}],
            })
        return httpx.Response(200, json={
            "id": "chat_synthetic", "model": body["model"],
            "choices": [{"finish_reason": "stop", "message": {"content": '{"items":[]}'}}],
        })

    monkeypatch.setattr(two_model, "BoundedChatClient",
                        lambda settings: BoundedChatClient(settings, transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(two_model, "ResponsesClient",
                        lambda settings: ResponsesClient(settings, transport=httpx.MockTransport(reply)))
    env = {"ARK_BASE_URL": "https://synthetic.example.invalid/v1", "ARK_API_KEY": "synthetic-primary-key",
           "ZYAI_BASE_URL": "https://gateway.example.invalid/v1", "ZYAI_API_KEY": "synthetic-secondary-key",
           "REVIEW_MODEL": "glm-5.3" if api == "chat" else "gpt-6.1-sol",
           "REVIEW_API": api, "REVIEW_THINKING": thinking, "RECONCILIATION_PROVIDER": "secondary"}
    with two_model.TwoModelWholePagePipeline.from_env(env) as pipeline:
        assert isinstance(pipeline.review_client, BoundedChatClient if api == "chat" else ResponsesClient)
        assert pipeline.reconciliation_client is pipeline.review_client
        assert pipeline.secondary_thinking is (thinking == "enabled")
        assert pipeline.review_client.settings.thinking is (thinking == "enabled")
        assert pipeline._read_and_judge(pipeline.client, picture(), "primary")["code"] == "ok"
        assert pipeline._read_and_judge(pipeline.review_client, picture(), "secondary")["code"] == "ok"
        assert isinstance(pipeline.visual_client, ResponsesClient)
        assert pipeline.visual_client.settings.base_url == env["ZYAI_BASE_URL"]
        assert pipeline.visual_client.settings.api_key == env["ZYAI_API_KEY"]
        assert pipeline._read_visual_targets(picture(), ["5#1"])["code"] == "ok"
    first_path, first = requests[0]
    second_path, second = requests[1]
    assert first_path == "/v1/chat/completions" and first["thinking"] == {"type": "disabled"}
    assert second_path == ("/v1/chat/completions" if api == "chat" else "/v1/responses")
    assert second["thinking" if api == "chat" else "reasoning"] == expected
    if api == "responses":
        assert "thinking" not in second
    else:
        assert "reasoning" not in second
    visual_path, visual = requests[2]
    assert visual_path == "/v1/responses"
    assert visual["model"] == "gpt-6-astra"
    assert visual["reasoning"] == {"effort": "medium"}
    assert visual["max_output_tokens"] == 8192
    assert json.loads(visual["input"][0]["content"][0]["text"])["task_input"] == {"labels": ["5#1"]}
    assert len(requests) == 3


def test_omitted_review_settings_keep_responses_and_enabled_thinking():
    with two_model.TwoModelWholePagePipeline.from_env({}) as pipeline:
        assert isinstance(pipeline.review_client, ResponsesClient)
        assert pipeline.review_client.settings.model == "gpt-6-astra"
        assert pipeline.secondary_thinking is True
        assert pipeline.reconciliation_client is pipeline.client
        assert isinstance(pipeline.visual_client, ResponsesClient)
        assert pipeline.visual_client.settings.model == "gpt-6-astra"
        assert pipeline.visual_client.settings.thinking is True
        assert pipeline.visual_client.settings.max_tokens == 8192
        assert pipeline.visual_client.settings.timeout_seconds == 118
        assert pipeline.visual_client.settings.total_timeout_seconds == 118
        assert pipeline.visual_client.settings.max_attempts == 1
        assert pipeline.visual_client is not pipeline.review_client
        assert pipeline.structure_navigation_enabled is False


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_navigation_experiment_requires_explicit_opt_in(enabled):
    with two_model.TwoModelWholePagePipeline.from_env({"STRUCTURE_NAVIGATION_ENABLED": enabled}) as pipeline:
        assert pipeline.structure_navigation_enabled is (enabled == "1")


def test_visual_model_can_be_explicit_and_uses_primary_gateway_fallback():
    env = {"ARK_BASE_URL": "https://synthetic.example.invalid/v1", "ARK_API_KEY": "synthetic-primary-key",
           "VISUAL_REVIEW_MODEL": "gpt-6.1-sol"}
    with two_model.TwoModelWholePagePipeline.from_env(env) as pipeline:
        assert pipeline.visual_client.settings.model == "gpt-6.1-sol"
        assert pipeline.visual_client.settings.base_url == env["ARK_BASE_URL"]
        assert pipeline.visual_client.settings.api_key == env["ARK_API_KEY"]


@pytest.mark.parametrize("values", [
    {"REVIEW_API": ""}, {"REVIEW_API": "automatic"}, {"REVIEW_API": None},
    {"REVIEW_THINKING": ""}, {"REVIEW_THINKING": "false"}, {"REVIEW_THINKING": False},
    {"RECONCILIATION_PROVIDER": ""}, {"RECONCILIATION_PROVIDER": "third"}, {"RECONCILIATION_PROVIDER": None},
    {"VISUAL_REVIEW_ENABLED": ""}, {"VISUAL_REVIEW_ENABLED": "enabled"},
    {"VISUAL_REVIEW_ENABLED": None}, {"VISUAL_REVIEW_ENABLED": True}, {"VISUAL_REVIEW_ENABLED": 1},
    {"VISUAL_REVIEW_MODEL": ""}, {"VISUAL_REVIEW_MODEL": None},
    {"VISUAL_REVIEW_MODEL": "gpt-6-astra\n"}, {"VISUAL_REVIEW_MODEL": 6},
    {"VISUAL_REVIEW_MODEL": "m" * 201},
    {"STRUCTURE_NAVIGATION_ENABLED": ""}, {"STRUCTURE_NAVIGATION_ENABLED": "true"},
    {"STRUCTURE_NAVIGATION_ENABLED": None}, {"STRUCTURE_NAVIGATION_ENABLED": True},
    {"STRUCTURE_NAVIGATION_ENABLED": 1},
])
def test_invalid_review_settings_fail_before_client_creation(monkeypatch, values):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid provider configuration must not construct a client")
    monkeypatch.setattr(two_model, "BoundedChatClient", forbidden)
    monkeypatch.setattr(two_model, "ResponsesClient", forbidden)
    with pytest.raises(ProviderError, match="invalid_configuration"):
        two_model.TwoModelWholePagePipeline.from_env(values)


def test_failed_secondary_setup_closes_primary(monkeypatch):
    closed = []
    primary = SimpleNamespace(close=lambda: closed.append("primary"))
    monkeypatch.setattr(two_model, "BoundedChatClient", lambda settings: primary)
    def fail(settings):
        raise ProviderError("invalid_configuration")
    monkeypatch.setattr(two_model, "ResponsesClient", fail)
    with pytest.raises(ProviderError, match="invalid_configuration"):
        two_model.TwoModelWholePagePipeline.from_env({})
    assert closed == ["primary"]


def test_failed_visual_setup_closes_both_existing_readers(monkeypatch):
    created, closed = [], []

    def client(settings):
        index = len(created)
        value = SimpleNamespace(close=lambda: closed.append(index))
        created.append(value)
        return value

    def fail(settings):
        raise ProviderError("invalid_configuration")

    monkeypatch.setattr(two_model, "BoundedChatClient", client)
    monkeypatch.setattr(two_model, "ResponsesClient", fail)
    with pytest.raises(ProviderError, match="invalid_configuration"):
        two_model.TwoModelWholePagePipeline.from_env({"REVIEW_API": "chat"})
    assert len(created) == 2
    assert closed == [0, 1]


@pytest.mark.parametrize("provider", ["primary", "secondary"])
@pytest.mark.parametrize("visual_enabled", ["0", "1"])
def test_factory_reuses_selected_reconciliation_provider_and_closes_each_once(monkeypatch, provider, visual_enabled):
    created, closed = [], []

    def client(settings):
        index = len(created)
        value = SimpleNamespace(settings=settings, close=lambda: closed.append(index))
        created.append(value)
        return value

    monkeypatch.setattr(two_model, "BoundedChatClient", client)
    monkeypatch.setattr(two_model, "ResponsesClient", client)
    with two_model.TwoModelWholePagePipeline.from_env({
        "RECONCILIATION_PROVIDER": provider, "VISUAL_REVIEW_ENABLED": visual_enabled,
    }) as pipeline:
        assert len(created) == (3 if visual_enabled == "1" else 2)
        assert pipeline.reconciliation_client is (pipeline.client if provider == "primary" else pipeline.review_client)
        assert pipeline.visual_client is (created[2] if visual_enabled == "1" else None)
    assert closed == list(range(len(created)))


def test_failed_pipeline_setup_closes_all_constructed_clients(monkeypatch):
    created, closed = [], []

    def client(settings):
        index = len(created)
        value = SimpleNamespace(close=lambda: closed.append(index))
        created.append(value)
        return value

    def fail(self, *args, **kwargs):
        raise ProviderError("invalid_configuration")

    monkeypatch.setattr(two_model, "BoundedChatClient", client)
    monkeypatch.setattr(two_model, "ResponsesClient", client)
    monkeypatch.setattr(two_model.TwoModelWholePagePipeline, "__init__", fail)
    with pytest.raises(ProviderError, match="invalid_configuration"):
        two_model.TwoModelWholePagePipeline.from_env({})
    assert len(created) == 3
    assert closed == [0, 1, 2]


def test_direct_constructor_keeps_reconciliation_opt_in():
    pipeline = two_model.TwoModelWholePagePipeline(SimpleNamespace(), SimpleNamespace())
    assert pipeline.reconciliation_client is None
    assert pipeline.visual_client is None


def test_reconciliation_reuses_secondary_and_close_runs_once_per_client():
    closed = []
    primary = SimpleNamespace(close=lambda: closed.append("primary"))
    secondary = SimpleNamespace(close=lambda: closed.append("secondary"))
    pipeline = two_model.TwoModelWholePagePipeline(primary, secondary, reconciliation_client=secondary,
                                                  secondary_thinking=False, visual_client=secondary)
    pipeline.close()
    assert closed == ["primary", "secondary"]
