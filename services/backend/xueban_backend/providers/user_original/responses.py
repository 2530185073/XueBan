"""Bounded OpenAI Responses API adapter used for the Astra escalation path."""

from copy import deepcopy
import json
import time
from threading import BoundedSemaphore
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from jsonschema.exceptions import ValidationError
from referencing.exceptions import Unresolvable

from .ark import (
    ArkSettings, Attempt, CandidateResult, Metadata, OutputDiagnostic,
    ProviderError, Usage, _SAFE_TOKEN,
    _elapsed, _images, _schema_diagnostic, _strict_json, _token, _validator,
)
from .prompts import COMMON_PROMPT, ROLE_PROMPTS, Role


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "null" or "null" in schema.get("type", []):
        return schema
    if any(isinstance(branch, dict) and branch.get("type") == "null"
           for branch in schema.get("anyOf", [])):
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Responses strict mode requires every object property to be required."""
    result = deepcopy(schema)
    # The Responses strict subset rejects collection uniqueness constraints;
    # the local jsonschema validator still enforces them on the returned data.
    result.pop("uniqueItems", None)
    # Cross-property JSON Schema conditionals are enforced again by the local
    # validator after generation, but are not accepted by strict Responses.
    if any(isinstance(branch, dict) and any(key in branch for key in ("if", "then", "else"))
           for branch in result.get("allOf", [])):
        result.pop("allOf", None)
    if isinstance(result.get("properties"), dict):
        properties = result["properties"]
        required = set(result.get("required", []))
        for name, child in properties.items():
            properties[name] = _strict_schema(child)
            if name not in required:
                properties[name] = _nullable(properties[name])
        result["required"] = list(properties)
    for key in ("items", "allOf", "anyOf", "oneOf", "prefixItems"):
        child = result.get(key)
        if isinstance(child, dict):
            result[key] = _strict_schema(child)
        elif isinstance(child, list):
            result[key] = [_strict_schema(item) if isinstance(item, dict) else item for item in child]
    return result


def _strip_optional_nulls(value: Any, schema: dict[str, Any]) -> Any:
    """Restore the local contract's optional-property representation."""
    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        required = set(schema.get("required", []))
        for name, child_schema in schema["properties"].items():
            if name not in value:
                continue
            if name not in required and value[name] is None:
                del value[name]
            elif isinstance(value[name], (dict, list)):
                value[name] = _strip_optional_nulls(value[name], child_schema)
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [_strip_optional_nulls(item, schema["items"]) for item in value]
    return value


def _responses_usage(value: object) -> Usage | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError()

    def count(source, key):
        result = source.get(key)
        if result is not None and (type(result) is not int or result < 0):
            raise ValueError()
        return result

    input_details = value.get("input_tokens_details") or {}
    output_details = value.get("output_tokens_details") or {}
    if not isinstance(input_details, dict) or not isinstance(output_details, dict):
        raise ValueError()
    return Usage(
        prompt_tokens=count(value, "input_tokens"),
        completion_tokens=count(value, "output_tokens"),
        total_tokens=count(value, "total_tokens"),
        cached_tokens=count(input_details, "cached_tokens"),
        reasoning_tokens=count(output_details, "reasoning_tokens"),
    )


def _response_text(envelope: Any) -> str:
    if not isinstance(envelope, dict):
        raise ValueError()
    if envelope.get("status") == "incomplete" or envelope.get("incomplete_details"):
        raise EOFError()
    if envelope.get("status") != "completed" or envelope.get("error"):
        raise ValueError()
    output = envelope.get("output")
    if not isinstance(output, list):
        raise ValueError()
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ValueError()
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant" or item.get("status") != "completed":
            raise ValueError()
        content = item.get("content")
        if not isinstance(content, list):
            raise ValueError()
        for part in content:
            if not isinstance(part, dict):
                raise ValueError()
            if part.get("type") == "refusal":
                raise ValueError()
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if not texts:
        raise ValueError()
    return "".join(texts)


class ResponsesClient:
    """Responses API adapter with the same generate contract as ArkClient."""

    def __init__(self, settings: ArkSettings, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._sleep = sleep
        self._closed = False
        cfg = settings
        try:
            url = urlsplit(cfg.base_url)
            if (not cfg.base_url or url.scheme != "https" or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment or "?" in cfg.base_url
                    or "#" in cfg.base_url or "\\" in cfg.base_url or any(c.isspace() for c in cfg.base_url)):
                raise ValueError()
            _ = url.port
            if not cfg.api_key or any(not 33 <= ord(char) <= 126 for char in cfg.api_key):
                raise ValueError()
            if not isinstance(cfg.model, str) or not _SAFE_TOKEN.fullmatch(cfg.model):
                raise ValueError()
            if not 1 <= cfg.max_attempts <= 3 or not 1 <= cfg.max_concurrency <= 16:
                raise ValueError()
            if not 0 < cfg.timeout_seconds <= 240 or not 0 < cfg.total_timeout_seconds <= 240:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("invalid_configuration") from None
        self._slots = BoundedSemaphore(cfg.max_concurrency)
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(cfg.timeout_seconds, connect=min(5.0, cfg.timeout_seconds)),
            limits=httpx.Limits(max_connections=cfg.max_concurrency,
                                max_keepalive_connections=cfg.max_concurrency),
            follow_redirects=False, trust_env=False,
        )

    def close(self):
        self._closed = True
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def generate(self, *, role: Role | str, text: str, output_schema: dict[str, Any],
                 images: tuple[str, ...] = (), thinking: bool | None = None,
                 max_tokens: int | None = None, instructions: str | None = None) -> CandidateResult:
        started = time.monotonic()
        deadline = started + self.settings.total_timeout_seconds
        if not self.settings.enabled:
            raise ProviderError("provider_disabled")
        if self._closed:
            raise ProviderError("client_closed")
        try:
            role = Role(role)
        except (ValueError, TypeError):
            raise ProviderError("invalid_role") from None
        if (not isinstance(text, str) or not text.strip() or len(text) > 65_536
                or not isinstance(images, (tuple, list))):
            raise ProviderError("invalid_input")
        if instructions is not None and (not isinstance(instructions, str) or not instructions.strip()
                                         or len(instructions) > 16_000):
            raise ProviderError("invalid_input")
        max_tokens = self.settings.max_tokens if max_tokens is None else max_tokens
        thinking = self.settings.thinking if thinking is None else thinking
        if type(max_tokens) is not int or not 1 <= max_tokens <= 16_384 or type(thinking) is not bool:
            raise ProviderError("invalid_input")
        try:
            text.encode("utf-8")
            if instructions is not None:
                instructions.encode("utf-8")
        except UnicodeError:
            raise ProviderError("invalid_input") from None

        validator = _validator(output_schema)
        chat_images = _images(images)
        content = [{"type": "input_text", "text": text}]
        content.extend({"type": "input_image", "image_url": part["image_url"]["url"], "detail": "high"}
                       for part in chat_images)
        system = COMMON_PROMPT + "\n角色任务：\n" + ROLE_PROMPTS[role]
        if instructions is not None:
            system += "\n服务端可信任务指令：\n" + instructions
        strict_schema = _strict_schema(output_schema)
        body = {
            "model": self.settings.model,
            "instructions": system,
            "store": False,
            "stream": False,
            "reasoning": {"effort": "medium" if thinking else "low"},
            "max_output_tokens": max_tokens,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "xueban_candidate",
                                  "strict": True, "schema": strict_schema}},
        }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("deadline_exceeded", latency_ms=_elapsed(started))
        if not self._slots.acquire(timeout=min(self.settings.queue_timeout_seconds, remaining)):
            code = "deadline_exceeded" if time.monotonic() >= deadline else "concurrency_limit"
            raise ProviderError(code, latency_ms=_elapsed(started))
        try:
            return self._generate(body, role, validator, output_schema, started, deadline)
        finally:
            self._slots.release()

    def _generate(self, body, role, validator, output_schema, started, deadline):
        attempts = []
        cfg = self.settings
        for number in range(1, cfg.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("deadline_exceeded", attempts=tuple(attempts), latency_ms=_elapsed(started))
            attempt_started = time.monotonic()
            status = None
            request_id = None
            retry_after = 0.1 * 2 ** (number - 1)
            raw = b""
            try:
                with self._http.stream("POST", cfg.base_url.rstrip("/") + "/responses",
                    headers={"Authorization": "Bearer " + cfg.api_key, "Content-Type": "application/json"},
                    json=body, timeout=httpx.Timeout(min(cfg.timeout_seconds, remaining),
                                                     connect=min(5.0, remaining))) as response:
                    status = response.status_code
                    request_id = _token(response.headers.get("x-request-id"), cfg.api_key)
                    if status == 429 or 500 <= status <= 599:
                        code = "upstream_rate_limited" if status == 429 else "upstream_unavailable"
                        try:
                            retry_after = min(2.0, max(retry_after, float(response.headers.get("retry-after", "0"))))
                        except ValueError:
                            pass
                    elif status != 200:
                        code = "upstream_rejected"
                    else:
                        code = "ok"
                        for chunk in response.iter_bytes():
                            if time.monotonic() >= deadline:
                                code = "deadline_exceeded"
                                break
                            if len(raw) + len(chunk) > cfg.max_response_bytes:
                                code = "response_too_large"
                                break
                            raw += chunk
            except httpx.TimeoutException:
                code = "deadline_exceeded" if time.monotonic() >= deadline else "upstream_timeout"
            except httpx.HTTPError:
                code = "upstream_transport_error"
            if time.monotonic() >= deadline:
                code = "deadline_exceeded"
            if code != "ok":
                attempts.append(Attempt(number, status, code, _elapsed(attempt_started), request_id))
                if code in {"upstream_rate_limited", "upstream_unavailable"} and number < cfg.max_attempts:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProviderError("deadline_exceeded", attempts=tuple(attempts),
                                            latency_ms=_elapsed(started)) from None
                    self._sleep(min(retry_after, remaining))
                    continue
                raise ProviderError(code, attempts=tuple(attempts), latency_ms=_elapsed(started)) from None

            try:
                envelope = _strict_json(raw.decode("utf-8"))
                if not isinstance(envelope, dict):
                    raise ValueError()
                model = _token(envelope.get("model"), cfg.api_key)
                response_id = _token(envelope.get("id"), cfg.api_key)
                usage = _responses_usage(envelope.get("usage"))
                candidate_text = _response_text(envelope)
                data = _strict_json(candidate_text)
                if not isinstance(data, dict):
                    raise ValueError()
                data = _strip_optional_nulls(data, output_schema)
                validator.validate(data)
            except EOFError:
                diagnostic = OutputDiagnostic("responses_envelope", "incomplete_output")
                attempts.append(Attempt(number, status, "incomplete_output", _elapsed(attempt_started),
                                        request_id, model, usage, diagnostic=diagnostic))
                raise ProviderError("incomplete_output", attempts=tuple(attempts),
                                    latency_ms=_elapsed(started)) from None
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError,
                    ValidationError, Unresolvable) as error:
                diagnostic = _schema_diagnostic(error) if isinstance(error, ValidationError) else \
                    OutputDiagnostic("responses_output", "invalid_structure")
                attempts.append(Attempt(number, status, "invalid_output", _elapsed(attempt_started),
                                        request_id, model, usage, diagnostic=diagnostic))
                raise ProviderError("invalid_output", attempts=tuple(attempts),
                                    latency_ms=_elapsed(started)) from None
            if time.monotonic() >= deadline:
                attempts.append(Attempt(number, status, "deadline_exceeded", _elapsed(attempt_started),
                                        request_id, model, usage))
                raise ProviderError("deadline_exceeded", attempts=tuple(attempts),
                                    latency_ms=_elapsed(started))
            attempts.append(Attempt(number, status, "ok", _elapsed(attempt_started), request_id, model, usage))
            metadata = Metadata(cfg.model, model, request_id, response_id, usage,
                                _elapsed(started), tuple(attempts))
            return CandidateResult(role=role, data=data, metadata=metadata)
        raise AssertionError("unreachable bounded Responses retry loop")
