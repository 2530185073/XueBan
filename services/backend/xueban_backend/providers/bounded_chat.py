"""Chat adapter with a cancellable total deadline and no automatic paid retry."""

from dataclasses import replace
import json
import math
import re
import time

from jsonschema.exceptions import ValidationError
from referencing.exceptions import Unresolvable

from .ark import (ArkClient, Attempt, CandidateResult, Metadata, OutputDiagnostic,
                  ProviderError, _elapsed, _schema_diagnostic, _strict_json,
                  _token, _usage)
from .bounded_http import read_response
from .correction_pipeline import _contains_credential
from .partial_output import PageSchemaFailure, capture_enabled


_CONTENT_PATH = ("choices", 0, "message", "content")
_JSON_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*\Z", re.IGNORECASE)
_TRANSPORT_RULES = {
    "deadline_exceeded": "total_timeout",
    "upstream_timeout": "phase_timeout",
    "upstream_transport_error": "transport_error",
    "upstream_rate_limited": "rate_limited",
    "upstream_unavailable": "upstream_unavailable",
    "upstream_rejected": "http_rejected",
    "response_too_large": "response_size_limit",
    "invalid_output": "invalid_response",
}


class _ChatOutputError(ProviderError):
    """Only server-owned diagnostic codes/paths, never response fragments."""

    def __init__(self, layer, rule, path=(), *, code="invalid_output"):
        super().__init__(code)
        self.diagnostic = OutputDiagnostic(layer, rule, path)


def _content(envelope):
    choices = envelope.get("choices")
    if not isinstance(choices, list):
        raise _ChatOutputError("envelope_shape", "choices_not_array", ("choices",))
    if len(choices) != 1:
        rule = "empty_choices" if not choices else "multiple_choices"
        raise _ChatOutputError("envelope_shape", rule, ("choices",))
    choice = choices[0]
    if not isinstance(choice, dict):
        raise _ChatOutputError("envelope_shape", "choice_not_object", ("choices", 0))
    finish = choice.get("finish_reason")
    if finish != "stop":
        # A valid-looking partial JSON object still cannot be accepted. Do not
        # persist an unrecognized finish string: it may contain private text.
        if isinstance(finish, (str, type(None))):
            rule = {
                "length": "token_limit",
                "content_filter": "content_filtered",
                "tool_calls": "tool_call_finish",
                "function_call": "function_call_finish",
                None: "missing_finish_reason",
            }.get(finish, "unsupported_finish_reason")
        else:
            rule = "invalid_finish_reason"
        raise _ChatOutputError("completion", rule, ("choices", 0, "finish_reason"), code="incomplete_output")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise _ChatOutputError("message_shape", "message_not_object", ("choices", 0, "message"))
    for key, rule in (("refusal", "refused_message"), ("tool_calls", "tool_call_message"),
                      ("function_call", "function_call_message")):
        if message.get(key):
            raise _ChatOutputError("message_shape", rule, ("choices", 0, "message", key))
    if "content" not in message:
        raise _ChatOutputError("message_shape", "missing_content", _CONTENT_PATH)
    content = message["content"]
    if not isinstance(content, str):
        rule = ("null_content" if content is None else
                "content_array" if isinstance(content, list) else "content_not_string")
        raise _ChatOutputError("message_shape", rule, _CONTENT_PATH)
    if not content.strip():
        raise _ChatOutputError("candidate_json", "empty_content", _CONTENT_PATH)
    return content


def _candidate_text(content):
    """Remove only one complete Markdown wrapper, never extract/repair JSON.

    Explanations, reasoning, a second document and unknown fence languages stay
    invalid. The enclosed JSON still goes through the exact same strict parser
    and schema, including conflicting keys, enums and required answer fields.
    """
    stripped = content.strip()
    if not stripped.startswith("```"):
        return content, None
    match = _JSON_FENCE.fullmatch(stripped)
    if match is None:
        raise _ChatOutputError("candidate_json", "unsupported_json_fence", _CONTENT_PATH)
    return match[1], OutputDiagnostic("candidate_json", "json_fence_removed", _CONTENT_PATH)


def _candidate_json(content):
    """Tolerate repeated identical values, never choose between alternatives.

    Some observed responses repeated conditions_same=true in every item.
    Canonical JSON comparison keeps bool/number, string spelling, array order
    and all nested values distinct. Envelope parsing remains strictly unique.
    """
    repeated = False

    def object_pairs(pairs):
        nonlocal repeated
        result = {}
        for key, value in pairs:
            if key in result:
                canonical = lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False)
                if canonical(result[key]) != canonical(value):
                    raise _ChatOutputError("candidate_json", "conflicting_duplicate_keys", _CONTENT_PATH)
                repeated = True
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("non_json_number")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non_finite_number")
        return result

    data = json.loads(content, object_pairs_hook=object_pairs, parse_constant=invalid_constant,
                      parse_float=finite_float)
    diagnostic = (OutputDiagnostic("candidate_json", "identical_duplicate_keys_removed", _CONTENT_PATH)
                  if repeated else None)
    return data, diagnostic


class BoundedChatClient(ArkClient):
    MAX_OUTPUT_TOKENS = 32768

    def __init__(self, settings, *, transport=None, provider_default_thinking=False):
        super().__init__(settings, transport=transport)
        self._async_transport = transport
        self._provider_default_thinking = provider_default_thinking

    def _generate(self, body, role, validator, started, deadline):
        if self._provider_default_thinking:
            # Kimi's gateway probe was verified with its native default, not Ark's thinking extension.
            body = {key: value for key, value in body.items() if key != "thinking"}
        cfg = self.settings
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("deadline_exceeded", latency_ms=_elapsed(started))
        cfg = replace(cfg, total_timeout_seconds=remaining)
        response = read_response(cfg, "/chat/completions", body, transport=self._async_transport)
        code, model, usage, response_id, diagnostic = response.code, None, None, None, None
        rejected_candidate = None
        if code == "ok":
            layer, path = "envelope_json", ()
            try:
                envelope = _strict_json(response.body.decode("utf-8"))
                if _contains_credential(envelope, cfg.api_key):
                    raise ProviderError("provider_credential_echo")
                if not isinstance(envelope, dict):
                    raise _ChatOutputError("envelope_shape", "envelope_not_object")
                model, response_id = _token(envelope.get("model"), cfg.api_key), _token(envelope.get("id"), cfg.api_key)
                layer, path = "usage_shape", ("usage",)
                usage = _usage(envelope.get("usage"))
                content, diagnostic = _candidate_text(_content(envelope))
                layer, path = "candidate_json", _CONTENT_PATH
                data, duplicate_diagnostic = _candidate_json(content)
                diagnostic = duplicate_diagnostic or diagnostic
                if _contains_credential(data, cfg.api_key):
                    raise ProviderError("provider_credential_echo")
                layer, path = "candidate_schema", ()
                validator.validate(data)
            except _ChatOutputError as error:
                code, diagnostic = error.code, error.diagnostic
            except ProviderError as error:
                code = error.code
                diagnostic = OutputDiagnostic("content_security", "credential_echo")
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError, ValidationError, Unresolvable) as error:
                code = "invalid_output"
                if isinstance(error, ValidationError):
                    diagnostic = _schema_diagnostic(error)
                    if capture_enabled():
                        # Only complete, security-checked JSON reaches here.
                        # Do not retain transport bodies or reasoning fields.
                        rejected_candidate = data
                elif isinstance(error, Unresolvable):
                    diagnostic = OutputDiagnostic("candidate_schema", "unresolvable_reference")
                else:
                    rule = ("invalid_encoding" if isinstance(error, UnicodeError) else
                            "invalid_json" if layer.endswith("_json") else "invalid_structure")
                    diagnostic = OutputDiagnostic(layer, rule, path)
            if code == "ok" and time.monotonic() >= deadline:
                code = "deadline_exceeded"
                diagnostic = OutputDiagnostic("validation", "total_timeout")
        else:
            diagnostic = OutputDiagnostic("transport", _TRANSPORT_RULES.get(code, "request_failed"))
        attempt = Attempt(1, response.status, code, _elapsed(started), response.request_id, model, usage, diagnostic=diagnostic)
        if code != "ok":
            if rejected_candidate is not None:
                metadata = Metadata(cfg.model, model, response.request_id, response_id, usage,
                                    _elapsed(started), (attempt,))
                raise PageSchemaFailure(rejected_candidate, validator.schema, metadata) from None
            raise ProviderError(code, attempts=(attempt,), latency_ms=_elapsed(started)) from None
        return CandidateResult(role, data, Metadata(cfg.model, model, response.request_id, response_id, usage,
                                                   _elapsed(started), (attempt,)))
