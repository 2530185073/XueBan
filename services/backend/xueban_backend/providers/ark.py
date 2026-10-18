"""Bounded synchronous Chat Completions adapter for a worker, not a public API.

The application does not automatically instantiate this client. Credentials and
the verified base URL must be supplied by server configuration. Result data is
always a candidate, never an authoritative correction decision.
"""

import base64
import binascii
from dataclasses import dataclass, field
import json
import math
import re
from threading import BoundedSemaphore
import time
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable

from .prompts import COMMON_PROMPT, ROLE_PROMPTS, Role

MODEL_ID = "deepseek-v4-1-flash-260910"
_SAFE_TOKEN = re.compile(r"^[a-zA-Z0-9_.:/-]{1,200}$")
_IMAGE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$")


@dataclass(frozen=True)
class ArkSettings:
    base_url: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    model: str = MODEL_ID
    timeout_seconds: float = 30.0
    total_timeout_seconds: float = 90.0
    queue_timeout_seconds: float = 1.0
    max_concurrency: int = 2
    max_attempts: int = 3
    max_tokens: int = 4096
    thinking: bool = True
    max_response_bytes: int = 1_048_576

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class OutputDiagnostic:
    """Stable codes and known field paths only, never validation messages/values."""

    layer: str
    rule: str
    path: tuple[str | int, ...] = ()


# Do not derive names from the response or schema: either may contain private text.
_DIAGNOSTIC_FIELDS = frozenset({
    "items", "coverage", "layout_index", "page", "asset_id", "label", "stem_text", "answer_text",
    "answer_status", "regions", "answer_regions", "uncertainties", "x", "y",
    "width", "height", "coordinate_space", "collection_scope",
    "unrecognized_regions", "note", "question_type", "options", "blank_count",
    "knowledge_points", "correction_method", "verdict", "reason_code", "readback",
    "recognition_consistent", "basis_summary", "first_error_region",
    "guidance_code", "focus_region", "choices", "finish_reason", "message", "content",
})
_DIAGNOSTIC_KEYWORDS = frozenset({
    "type", "required", "additionalProperties", "enum", "const", "anyOf", "oneOf",
    "allOf", "not", "minLength", "maxLength", "pattern", "format", "minItems",
    "maxItems", "uniqueItems", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minProperties", "maxProperties",
})


def _schema_diagnostic(error: ValidationError) -> OutputDiagnostic:
    path = tuple(part if (type(part) is int and 0 <= part < 1_000_000
                          or isinstance(part, str) and part in _DIAGNOSTIC_FIELDS)
                 else "*" for part in list(error.absolute_path)[:24])
    rule = error.validator if error.validator in _DIAGNOSTIC_KEYWORDS else "validation"
    return OutputDiagnostic("candidate_schema", rule, path)


@dataclass(frozen=True)
class Attempt:
    number: int
    status_code: int | None
    code: str
    latency_ms: int
    request_id: str | None = None
    model: str | None = None
    usage: Usage | None = None
    # Usage is not a bill. Even a timeout/retry may have incurred a charge.
    charge_status: Literal["unknown"] = "unknown"
    diagnostic: OutputDiagnostic | None = None


@dataclass(frozen=True)
class Metadata:
    requested_model: str
    model: str | None
    request_id: str | None
    response_id: str | None
    usage: Usage | None
    latency_ms: int
    attempts: tuple[Attempt, ...]


@dataclass(frozen=True)
class CandidateResult:
    role: Role
    data: dict[str, Any] = field(repr=False)
    metadata: Metadata
    status: Literal["candidate"] = field(default="candidate", init=False)


class ProviderError(Exception):
    """Safe to surface as a code; never contains provider bodies or credentials."""

    def __init__(self, code: str, *, attempts: tuple[Attempt, ...] = (), latency_ms: int = 0):
        super().__init__(code)
        self.code = code
        self.attempts = attempts
        self.latency_ms = latency_ms


def _elapsed(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _token(value: object, secret: str) -> str | None:
    return value if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value) and secret not in value else None


def _strict_json(value: str | bytes) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = item
        return result

    def invalid_constant(_):
        raise ValueError("non_json_number")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non_finite_number")
        return result

    return json.loads(value, object_pairs_hook=object_pairs, parse_constant=invalid_constant, parse_float=finite_float)


def _validator(schema: dict[str, Any]) -> Draft202012Validator:
    try:
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError()
        if len(json.dumps(schema, allow_nan=False)) > 65_536:
            raise ValueError()
        Draft202012Validator.check_schema(schema)

        def check_refs(item):
            if isinstance(item, dict):
                if "$dynamicRef" in item:
                    raise ValueError()
                if "$ref" in item:
                    ref = item["$ref"]
                    if ref != "#" and not ref.startswith("#/"):
                        raise ValueError()
                    target: Any = schema
                    if ref != "#":
                        for part in ref[2:].split("/"):
                            target = target[part.replace("~1", "/").replace("~0", "~")]
                for child in item.values():
                    check_refs(child)
            elif isinstance(item, list):
                for child in item:
                    check_refs(child)

        check_refs(schema)
        def forbid_remote(uri):
            raise NoSuchResource(ref=uri)

        return Draft202012Validator(schema, format_checker=FormatChecker(), registry=Registry(retrieve=forbid_remote))
    except (ValueError, TypeError, KeyError, SchemaError, RecursionError):
        raise ProviderError("invalid_output_schema") from None


def _images(values: tuple[str, ...]) -> list[dict[str, Any]]:
    if len(values) > 4:
        raise ProviderError("invalid_input")
    result = []
    total = 0
    for value in values:
        match = _IMAGE.fullmatch(value) if isinstance(value, str) and len(value) <= 5_592_500 else None
        if match is None:
            raise ProviderError("invalid_input")
        try:
            raw = base64.b64decode(match[2], validate=True)
        except (ValueError, binascii.Error):
            raise ProviderError("invalid_input") from None
        total += len(raw)
        valid_magic = (
            match[1] == "png" and raw.startswith(b"\x89PNG\r\n\x1a\n")
            or match[1] == "jpeg" and raw.startswith(b"\xff\xd8\xff")
            or match[1] == "webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
        )
        if not valid_magic or len(raw) > 4_194_304 or total > 8_388_608:
            raise ProviderError("invalid_input")
        result.append({"type": "image_url", "image_url": {"url": value}})
    return result


def _usage(value: object) -> Usage | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError()

    def count(source, key):
        result = source.get(key)
        if result is not None and (type(result) is not int or result < 0):
            raise ValueError()
        return result

    prompt_details = value.get("prompt_tokens_details", {})
    completion_details = value.get("completion_tokens_details", {})
    prompt_details = {} if prompt_details is None else prompt_details
    completion_details = {} if completion_details is None else completion_details
    if not isinstance(prompt_details, dict) or not isinstance(completion_details, dict):
        raise ValueError()
    return Usage(count(value, "prompt_tokens"), count(value, "completion_tokens"), count(value, "total_tokens"), count(prompt_details, "cached_tokens"), count(completion_details, "reasoning_tokens"))


class ArkClient:
    def __init__(self, settings: ArkSettings, *, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._sleep = sleep
        self._closed = False
        self._validate_settings()
        self._slots = BoundedSemaphore(settings.max_concurrency)
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=min(5.0, settings.timeout_seconds)),
            limits=httpx.Limits(max_connections=settings.max_concurrency, max_keepalive_connections=settings.max_concurrency),
            follow_redirects=False,
            trust_env=False,
        )

    def _validate_settings(self):
        cfg = self.settings
        try:
            url = urlsplit(cfg.base_url)
            if cfg.base_url and (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment or "?" in cfg.base_url or "#" in cfg.base_url or "\\" in cfg.base_url or any(char.isspace() for char in cfg.base_url)):
                raise ValueError()
            if cfg.base_url:
                _ = url.port
            if not isinstance(cfg.api_key, str) or any(not 33 <= ord(char) <= 126 for char in cfg.api_key):
                raise ValueError()
            if not isinstance(cfg.model, str) or not _SAFE_TOKEN.fullmatch(cfg.model):
                raise ValueError()
            for value, low, high in ((cfg.max_concurrency, 1, 16), (cfg.max_attempts, 1, 3), (cfg.max_tokens, 1, 8192), (cfg.max_response_bytes, 256, 4_194_304)):
                if type(value) is not int or not low <= value <= high:
                    raise ValueError()
            for value in (cfg.timeout_seconds, cfg.queue_timeout_seconds, cfg.total_timeout_seconds):
                if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError()
            if type(cfg.thinking) is not bool:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("invalid_configuration") from None

    def close(self):
        self._closed = True
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def generate(self, *, role: Role | str, text: str, output_schema: dict[str, Any], images: tuple[str, ...] = (), thinking: bool | None = None, max_tokens: int | None = None, instructions: str | None = None) -> CandidateResult:
        """Generate a candidate; instructions must be server-owned task policy.

        Never pass student text, image transcription or client-provided policy
        as instructions. Those belong exclusively to text/images (user data).
        The optional default preserves existing callers.
        """
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
        if not isinstance(text, str) or not text.strip() or len(text) > 65_536 or not isinstance(images, (list, tuple)):
            raise ProviderError("invalid_input")
        if instructions is not None and (not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 16_000):
            raise ProviderError("invalid_input")
        thinking = self.settings.thinking if thinking is None else thinking
        max_tokens = self.settings.max_tokens if max_tokens is None else max_tokens
        if type(thinking) is not bool or type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
            raise ProviderError("invalid_input")
        try:
            text.encode("utf-8")
            if instructions is not None:
                instructions.encode("utf-8")
        except UnicodeError:
            raise ProviderError("invalid_input") from None
        validator = _validator(output_schema)
        image_parts = _images(images)
        content = [{"type": "text", "text": text}, *image_parts] if image_parts else text
        system = COMMON_PROMPT + "\n角色任务：\n" + ROLE_PROMPTS[role]
        if instructions is not None:
            system += "\n服务端可信任务指令：\n" + instructions
        system += "\nJSON Schema:\n" + json.dumps(output_schema, ensure_ascii=False, allow_nan=False)
        body = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "stream": False,
            "max_tokens": max_tokens,
            "thinking": {"type": "enabled" if thinking else "disabled"},
            "response_format": {"type": "json_object"},
        }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("deadline_exceeded", latency_ms=_elapsed(started))
        if not self._slots.acquire(timeout=min(self.settings.queue_timeout_seconds, remaining)):
            code = "deadline_exceeded" if time.monotonic() >= deadline else "concurrency_limit"
            raise ProviderError(code, latency_ms=_elapsed(started))
        try:
            return self._generate(body, role, validator, started, deadline)
        finally:
            self._slots.release()

    def _generate(self, body, role, validator, started, deadline):
        attempts = []
        cfg = self.settings
        for number in range(1, cfg.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("deadline_exceeded", attempts=tuple(attempts), latency_ms=_elapsed(started))
            attempt_started = time.monotonic()
            request_id = None
            status = None
            retry_after = 0.1 * 2 ** (number - 1)
            raw = b""
            # HTTPX sync timeouts apply to individual I/O phases. Shrink them
            # at each request, then check the monotonic budget at each chunk.
            io_timeout = min(cfg.timeout_seconds, remaining)
            try:
                with self._http.stream("POST", cfg.base_url.rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + cfg.api_key, "Content-Type": "application/json"}, json=body, timeout=httpx.Timeout(io_timeout, connect=min(5.0, io_timeout))) as response:
                    status = response.status_code
                    request_id = _token(response.headers.get("x-request-id"), cfg.api_key)
                    if time.monotonic() >= deadline:
                        code = "deadline_exceeded"
                    elif status == 429 or 500 <= status <= 599:
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
                        raise ProviderError("deadline_exceeded", attempts=tuple(attempts), latency_ms=_elapsed(started)) from None
                    self._sleep(min(retry_after, remaining))
                    continue
                raise ProviderError(code, attempts=tuple(attempts), latency_ms=_elapsed(started)) from None

            model = None
            usage = None
            response_id = None
            layer = "envelope_json"
            diagnostic = None
            try:
                envelope = _strict_json(raw.decode("utf-8"))
                layer = "envelope_shape"
                if not isinstance(envelope, dict):
                    raise ValueError()
                model = _token(envelope.get("model"), cfg.api_key)
                response_id = _token(envelope.get("id"), cfg.api_key)
                usage = _usage(envelope.get("usage"))
                choices = envelope["choices"]
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError()
                choice = choices[0]
                if choice.get("finish_reason") != "stop":
                    code = "incomplete_output"
                    diagnostic = OutputDiagnostic(layer, "incomplete_output", ("choices", 0, "finish_reason"))
                    raise ValueError()
                message = choice["message"]
                if message.get("tool_calls") or message.get("refusal") or not isinstance(message.get("content"), str):
                    raise ValueError()
                layer = "candidate_json"
                data = _strict_json(message["content"])
                if not isinstance(data, dict):
                    raise ValueError()
                layer = "candidate_schema"
                validator.validate(data)
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError, ValidationError, Unresolvable) as error:
                code = "incomplete_output" if code == "incomplete_output" else "invalid_output"
                if isinstance(error, ValidationError):
                    diagnostic = _schema_diagnostic(error)
                elif diagnostic is None:
                    diagnostic = OutputDiagnostic(layer, "invalid_json" if layer.endswith("_json") else "invalid_structure")
                attempts.append(Attempt(number, status, code, _elapsed(attempt_started), request_id, model, usage, diagnostic=diagnostic))
                raise ProviderError(code, attempts=tuple(attempts), latency_ms=_elapsed(started)) from None
            if time.monotonic() >= deadline:
                attempts.append(Attempt(number, status, "deadline_exceeded", _elapsed(attempt_started), request_id, model, usage))
                raise ProviderError("deadline_exceeded", attempts=tuple(attempts), latency_ms=_elapsed(started))
            attempts.append(Attempt(number, status, "ok", _elapsed(attempt_started), request_id, model, usage))
            return CandidateResult(role, data, Metadata(cfg.model, model, request_id, response_id, usage, _elapsed(started), tuple(attempts)))
        raise AssertionError("Unreachable bounded retry loop")
