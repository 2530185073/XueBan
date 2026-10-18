"""Chat adapter with a cancellable total deadline and no automatic paid retry."""

from jsonschema.exceptions import ValidationError
from referencing.exceptions import Unresolvable

from .ark import (ArkClient, Attempt, CandidateResult, Metadata, OutputDiagnostic,
                  ProviderError, _elapsed, _schema_diagnostic, _strict_json,
                  _token, _usage)
from .bounded_http import read_response
from .correction_pipeline import _contains_credential


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
        response = read_response(cfg, "/chat/completions", body, transport=self._async_transport)
        code, model, usage, response_id, diagnostic = response.code, None, None, None, None
        if code == "ok":
            try:
                envelope = _strict_json(response.body)
                if _contains_credential(envelope, cfg.api_key):
                    raise ProviderError("provider_credential_echo")
                if not isinstance(envelope, dict):
                    raise ValueError()
                model, response_id = _token(envelope.get("model"), cfg.api_key), _token(envelope.get("id"), cfg.api_key)
                usage = _usage(envelope.get("usage"))
                choices = envelope.get("choices")
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError()
                if choices[0].get("finish_reason") != "stop":
                    raise ProviderError("incomplete_output")
                message = choices[0]["message"]
                if message.get("refusal") or message.get("tool_calls") or message.get("function_call") or not isinstance(message.get("content"), str):
                    raise ValueError()
                data = _strict_json(message["content"])
                validator.validate(data)
            except ProviderError as error:
                code = error.code
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError, ValidationError, Unresolvable) as error:
                code = "invalid_output"
                diagnostic = _schema_diagnostic(error) if isinstance(error, ValidationError) else OutputDiagnostic("chat", "invalid_structure")
        attempt = Attempt(1, response.status, code, _elapsed(started), response.request_id, model, usage, diagnostic=diagnostic)
        if code != "ok":
            raise ProviderError(code, attempts=(attempt,), latency_ms=_elapsed(started)) from None
        return CandidateResult(role, data, Metadata(cfg.model, model, response.request_id, response_id, usage,
                                                   _elapsed(started), (attempt,)))
