"""Offline cross-provider credential fences for every two-model call stage.

Credentials are synthetic. MockTransport exercises the actual bounded Chat
adapter; successful alternate-provider paths use the existing test clients.
No environment configuration, database or external provider is accessed.
"""
from dataclasses import asdict
import json

import httpx
from jsonschema import Draft202012Validator
import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, item, picture
from xueban_backend.providers import reconciliation, visual_readback
from xueban_backend.providers.ark import ArkSettings
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def assert_credential_rejected(call, secret):
    assert call["code"] == "provider_credential_echo"
    assert call["items"] == {}
    assert secret not in repr(call) + json.dumps(asdict(call["metadata"]))
    assert len(call["metadata"].attempts) == 1
    attempt = call["metadata"].attempts[0]
    assert attempt.code == "provider_credential_echo"
    assert attempt.request_id is None and attempt.model is None
    assert attempt.charge_status == "unknown"
    return attempt


@pytest.mark.parametrize("stage", ["page_read", "reconciliation", "visual_readback", "semantic_alignment"])
@pytest.mark.parametrize("metadata_field", ["request_id", "model"])
def test_failed_stage_checks_other_provider_credentials_before_archiving(stage, metadata_field):
    """Failure metadata is inspected even when no candidate was returned."""
    primary = FakeClient("primary", {"items": [correct()]})
    secondary = FakeClient("secondary", {"items": [correct()]})
    other_secret = secondary.settings.api_key
    if metadata_field == "request_id":
        response = httpx.Response(500, headers={"x-request-id": other_secret})
    else:
        response = httpx.Response(200, headers={"x-request-id": "safe-request-id"}, json={
            "id": "safe-response-id", "model": other_secret,
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            "choices": [{"finish_reason": "stop", "message": {"content": '{"items":'}}],
        })
    requests = []

    def serve(request):
        requests.append(request)
        return response

    settings = ArkSettings(base_url="https://synthetic-provider.invalid/v1",
                           api_key="synthetic-actual-provider-secret", model="actual-provider",
                           max_attempts=3)
    with BoundedChatClient(settings, transport=httpx.MockTransport(serve)) as client:
        assert client.settings.api_key != other_secret
        pipeline = TwoModelWholePagePipeline(
            client if stage == "page_read" else primary, secondary,
            reconciliation_client=client, visual_client=client,
        )
        if stage == "page_read":
            call = pipeline._read_and_judge(client, picture(), "primary")
        elif stage == "reconciliation":
            call = pipeline._review_conflicts(picture(), [])
        elif stage == "visual_readback":
            call = pipeline._read_visual_once(picture(), ["1"])
        else:
            call = pipeline._align_page(picture(), {"1": correct("1")}, {"2": correct("2")})

    attempt = assert_credential_rejected(call, other_secret)
    assert len(requests) == 1  # The security failure must not trigger a paid retry.
    assert primary.calls == secondary.calls == []
    assert attempt.status_code == (500 if metadata_field == "request_id" else 200)
    if metadata_field == "model":
        assert attempt.usage is not None and attempt.usage.total_tokens == 8
    else:
        assert attempt.usage is None


@pytest.mark.parametrize("stage", ["critical_readback", "visual_adjudication"])
def test_successful_alternate_provider_checks_every_configured_credential(stage):
    """An alternate call must still check the provider it did not select."""
    primary = FakeClient("primary", {"items": []})
    secondary = FakeClient("secondary", {"items": []})
    reconciler = FakeClient("reconciler", {"items": []})
    visual = FakeClient("visual", {"items": []})
    judge = FakeClient("judge", {"items": []})
    if stage == "critical_readback":
        # A corroborating read uses the secondary model, but must also reject
        # the distinct credential belonging to the ordinary visual reader.
        secret = visual.settings.api_key
        actual = secondary
        actual.output = {"items": [{**item(answer_text=secret), "options": []}]}
        schema = visual_readback.SCHEMA
    else:
        # Strong visual adjudication selects the judgment model; the unused
        # reconciliation model's credential remains part of the security fence.
        secret = reconciler.settings.api_key
        actual = judge
        actual.output = {"items": [review(visual_evidence=secret)]}
        schema = reconciliation.SCHEMA
    assert secret != actual.settings.api_key
    Draft202012Validator(schema).validate(actual.output)
    assert secret in json.dumps(actual.output)
    pipeline = TwoModelWholePagePipeline(primary, secondary, reconciliation_client=reconciler,
                                        visual_client=visual, judgment_client=judge)
    if stage == "critical_readback":
        call = pipeline._read_visual_once(picture(), ["1"], client=secondary, stage=stage)
    else:
        call = pipeline._review_conflicts(picture(), [{"label": "1"}], stage=stage)

    attempt = assert_credential_rejected(call, secret)
    assert attempt.usage is not None and attempt.usage.total_tokens == 30
    assert sum(len(client.calls) for client in (primary, secondary, reconciler, visual, judge)) == 1
    assert len(actual.calls) == 1
