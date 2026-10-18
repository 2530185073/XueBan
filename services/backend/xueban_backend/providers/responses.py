"""Bounded Responses SSE client. No partial output is a completed candidate."""

import json
import time

from jsonschema.exceptions import ValidationError
from referencing.exceptions import Unresolvable

from .ark import (ArkClient, Attempt, CandidateResult, Metadata, OutputDiagnostic,
                  ProviderError, _elapsed, _images, _schema_diagnostic,
                  _strict_json, _token, _usage, _validator)
from .correction_pipeline import _contains_credential
from .prompts import COMMON_PROMPT, ROLE_PROMPTS, Role


def _response_usage(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError()
    return _usage({"prompt_tokens": value.get("input_tokens"),
                   "completion_tokens": value.get("output_tokens"),
                   "total_tokens": value.get("total_tokens"),
                   "prompt_tokens_details": value.get("input_tokens_details"),
                   "completion_tokens_details": value.get("output_tokens_details")})


def _final_text(envelope):
    if (not isinstance(envelope, dict) or envelope.get("error")
            or envelope.get("status") != "completed" or envelope.get("incomplete_details")):
        raise ProviderError("incomplete_output")
    texts = []
    for item in envelope.get("output", []):
        if not isinstance(item, dict):
            raise ProviderError("invalid_output")
        if item.get("type") == "reasoning":
            continue  # Never persist chain of thought or reasoning event history.
        if (item.get("type") != "message" or item.get("role") != "assistant"
                or item.get("status") != "completed"):
            raise ProviderError("invalid_output")
        for content in item.get("content", []):
            if not isinstance(content, dict) or content.get("type") != "output_text" or not isinstance(content.get("text"), str):
                raise ProviderError("invalid_output")
            texts.append(content["text"])
    if not texts:
        raise ProviderError("invalid_output")
    return "".join(texts)


class ResponsesClient(ArkClient):
    """Reuse verified transport/configuration limits; exactly one paid attempt."""

    MAX_OUTPUT_TOKENS = 32768

    def __init__(self, settings, *, transport=None):
        super().__init__(settings, transport=transport)
        self._async_transport = transport

    def generate(self, *, role, text, output_schema, images=(), thinking=True,
                 max_tokens=None, instructions=None):
        started = time.monotonic()
        cfg = self.settings
        deadline = started + cfg.total_timeout_seconds
        if not cfg.enabled:
            raise ProviderError("provider_disabled")
        if self._closed:
            raise ProviderError("client_closed")
        try:
            role = Role(role)
        except (TypeError, ValueError):
            raise ProviderError("invalid_role") from None
        max_tokens = cfg.max_tokens if max_tokens is None else max_tokens
        if (not isinstance(text, str) or not text.strip() or len(text) > 65_536
                or not isinstance(images, (tuple, list)) or type(thinking) is not bool
                or type(max_tokens) is not int or not 1 <= max_tokens <= self.MAX_OUTPUT_TOKENS
                or (instructions is not None and (not isinstance(instructions, str)
                    or not instructions.strip() or len(instructions) > 16_000))):
            raise ProviderError("invalid_input")
        validator = _validator(output_schema)
        image_parts = _images(images)
        body = {"model": cfg.model, "store": False, "stream": True,
                "max_output_tokens": max_tokens,
                "reasoning": {"effort": "medium" if thinking else "low"},
                "instructions": COMMON_PROMPT + "\n" + ROLE_PROMPTS[role] + "\n" + (instructions or ""),
                "input": [{"role": "user", "content": [{"type": "input_text", "text": text},
                    *[{"type": "input_image", "image_url": part["image_url"]["url"], "detail": "high"} for part in image_parts]]}],
                "text": {"format": {"type": "json_schema", "name": "correction_review",
                                      "strict": True, "schema": output_schema}}}
        try:
            json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (UnicodeError, TypeError, ValueError):
            raise ProviderError("invalid_input") from None
        if not self._slots.acquire(timeout=min(cfg.queue_timeout_seconds, cfg.total_timeout_seconds)):
            raise ProviderError("concurrency_limit", latency_ms=_elapsed(started))
        try:
            return self._request(body, role, validator, started, deadline)
        finally:
            self._slots.release()

    def _request(self, body, role, validator, started, deadline):
        from .bounded_http import read_response
        cfg = self.settings
        response = read_response(cfg, "/responses", body, transport=self._async_transport, responses=True)
        code, model, response_id, usage, diagnostic = response.code, None, None, None, None
        if code == "ok":
            try:
                envelope = _strict_json(response.body)
                if _contains_credential(envelope, cfg.api_key):
                    raise ProviderError("provider_credential_echo")
                if isinstance(envelope, dict):
                    model = _token(envelope.get("model"), cfg.api_key)
                    response_id = _token(envelope.get("id"), cfg.api_key)
                    usage = _response_usage(envelope.get("usage"))
                data = _strict_json(_final_text(envelope))
                validator.validate(data)
            except ProviderError as error:
                code = error.code
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError, ValidationError, Unresolvable) as error:
                code = "invalid_output"
                diagnostic = _schema_diagnostic(error) if isinstance(error, ValidationError) else OutputDiagnostic("responses", "invalid_structure")
        attempt = Attempt(1, response.status, code, _elapsed(started), response.request_id, model, usage, diagnostic=diagnostic)
        if code != "ok":
            raise ProviderError(code, attempts=(attempt,), latency_ms=_elapsed(started)) from None
        return CandidateResult(role, data, Metadata(cfg.model, model, response.request_id, response_id, usage,
                                                   _elapsed(started), (attempt,)))
