"""Text-only BytePlus multimodal embeddings; no credentials are read on import.

API: https://docs.byteplus.com/en/docs/modelark/multimodal-vectorization-api
Instructions: https://docs.byteplus.com/en/docs/modelark/vectorization

One request embeds one text, not a batch of separate documents. Query/corpus
instructions form a versioned pair. Persist the result's space with each index
and call require_compatible before inserting or searching. Changing the model,
dimension, provider endpoint or instruction pair requires a separate index.
This adapter does not create a database, infer mastery, retry billable requests,
or infer monetary charges from token counts.

The UTF-8 byte guard is not a tokenizer. The provider enforces its separate
8,000-token limit per text; callers must chunk material using a verified
tokenizer when needed. A byte/character count cannot establish token validity.

The total time budget is checked before I/O, after response headers, at every
response chunk and before returning parsed output. Synchronous HTTPX cannot
preempt an already-blocking transport operation: its per-operation timeout is
capped at the remaining budget when the request starts, but a later blocking
read may finish after that budget. This is not a hard process-kill deadline;
no background thread or automatic retry is used.
"""

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx


MODEL_ID = "skylark-embedding-vision-251215"
_SAFE_ID = re.compile(r"^[a-zA-Z0-9_.:/-]{1,200}$")
_CORPUS_INSTRUCTION = "Instruction:Compress the text into one word.\nQuery:"
_QUERY_TASK = "Retrieve junior middle school curriculum passages that explain the mathematical concept or homework question."


@dataclass(frozen=True)
class EmbeddingSettings:
    base_url: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    model: str = MODEL_ID
    dimensions: int = 2048
    query_task: str = field(default=_QUERY_TASK, repr=False)
    timeout_seconds: float = 20.0
    total_timeout_seconds: float = 30.0
    max_response_bytes: int = 262_144

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)


@dataclass(frozen=True)
class EmbeddingSpace:
    """Persist this complete value as the immutable index configuration."""

    model: str
    dimension: int
    profile_id: str

    def require_compatible(self, other: "EmbeddingSpace") -> None:
        if not isinstance(other, EmbeddingSpace) or self != other:
            raise EmbeddingError("incompatible_embedding_space")


@dataclass(frozen=True)
class EmbeddingUsage:
    prompt_tokens: int | None = None
    total_tokens: int | None = None
    text_tokens: int | None = None
    image_tokens: int | None = None


@dataclass(frozen=True)
class EmbeddingMetadata:
    request_id: str | None
    response_id: str | None
    usage: EmbeddingUsage | None
    latency_ms: int
    status_code: int | None
    # A successful vector and reported tokens still do not establish its bill.
    cost: None = field(default=None, init=False)
    charge_status: Literal["unknown"] = field(default="unknown", init=False)


@dataclass(frozen=True)
class EmbeddingResult:
    embedding: tuple[float, ...] = field(repr=False)
    space: EmbeddingSpace
    role: Literal["query", "corpus"]
    metadata: EmbeddingMetadata

    @property
    def model(self) -> str:
        return self.space.model

    @property
    def dimension(self) -> int:
        return self.space.dimension

    def require_compatible(self, index_space: EmbeddingSpace) -> None:
        self.space.require_compatible(index_space)


class EmbeddingError(Exception):
    """Contains only our static error code and sanitized operational metadata."""

    def __init__(self, code: str, *, metadata: EmbeddingMetadata | None = None):
        super().__init__(code)
        self.code = code
        self.metadata = metadata


def _safe_id(value: object, secret: str) -> str | None:
    if isinstance(value, str) and _SAFE_ID.fullmatch(value) and not (secret and secret in value):
        return value
    return None


def _usage(value: object) -> EmbeddingUsage | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError()
    details = value.get("prompt_tokens_details", {})
    if not isinstance(details, dict):
        raise ValueError()

    def count(source, key):
        result = source.get(key)
        if result is not None and (type(result) is not int or result < 0):
            raise ValueError()
        return result

    return EmbeddingUsage(count(value, "prompt_tokens"), count(value, "total_tokens"), count(details, "text_tokens"), count(details, "image_tokens"))


def _decode(raw: bytes):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError()

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError()
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant, parse_float=finite_float)


class EmbeddingClient:
    """Synchronous worker adapter. Use as a context manager; no automatic retry."""

    def __init__(self, settings: EmbeddingSettings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._validate_settings()
        self._closed = False
        self._query_instruction = "Target_modality: text.\nInstruction:" + settings.query_task + "\nQuery:"
        profile = json.dumps({"version": 1, "endpoint": settings.base_url.rstrip("/"), "model": settings.model, "dimension": settings.dimensions, "query": self._query_instruction, "corpus": _CORPUS_INSTRUCTION}, sort_keys=True, separators=(",", ":"))
        self.space = EmbeddingSpace(settings.model, settings.dimensions, hashlib.sha256(profile.encode("utf-8")).hexdigest())
        self._http = httpx.Client(transport=transport, timeout=httpx.Timeout(settings.timeout_seconds, connect=min(5.0, settings.timeout_seconds)), follow_redirects=False, trust_env=False, limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))

    def _validate_settings(self) -> None:
        cfg = self.settings
        try:
            if not isinstance(cfg.base_url, str):
                raise ValueError()
            if cfg.base_url:
                url = urlsplit(cfg.base_url)
                if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment or "?" in cfg.base_url or "#" in cfg.base_url or "\\" in cfg.base_url or any(char.isspace() or ord(char) < 32 for char in cfg.base_url) or url.path.rstrip("/") != "/api/v3"):
                    raise ValueError()
                _ = url.port
            if not isinstance(cfg.api_key, str) or any(not 33 <= ord(char) <= 126 for char in cfg.api_key):
                raise ValueError()
            if cfg.model != MODEL_ID or type(cfg.dimensions) is not int or cfg.dimensions not in (1024, 2048):
                raise ValueError()
            for timeout in (cfg.timeout_seconds, cfg.total_timeout_seconds):
                if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0 < timeout <= 120:
                    raise ValueError()
            if type(cfg.max_response_bytes) is not int or not 1024 <= cfg.max_response_bytes <= 1_048_576:
                raise ValueError()
            if not isinstance(cfg.query_task, str) or not cfg.query_task.strip() or len(cfg.query_task) > 2048 or any(ord(char) < 32 for char in cfg.query_task):
                raise ValueError()
            cfg.query_task.encode("utf-8")
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise EmbeddingError("invalid_embedding_configuration") from None

    def close(self) -> None:
        self._closed = True
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def embed_query(self, text: str, *, index_space: EmbeddingSpace | None = None) -> EmbeddingResult:
        return self._embed(text, "query", index_space)

    def embed_document(self, text: str, *, index_space: EmbeddingSpace | None = None) -> EmbeddingResult:
        return self._embed(text, "corpus", index_space)

    def _embed(self, text: str, role: Literal["query", "corpus"], index_space: EmbeddingSpace | None) -> EmbeddingResult:
        cfg = self.settings
        if not cfg.enabled:
            raise EmbeddingError("embedding_disabled")
        if self._closed:
            raise EmbeddingError("embedding_client_closed")
        if index_space is not None:
            self.space.require_compatible(index_space)
        if not isinstance(text, str) or not text.strip() or len(text) > 65_536:
            raise EmbeddingError("invalid_embedding_input")
        try:
            # Official per-text cap; does not prove the separate token cap.
            if len(text.encode("utf-8")) > 100_000:
                raise EmbeddingError("invalid_embedding_input")
        except UnicodeError:
            raise EmbeddingError("invalid_embedding_input") from None
        body = {"model": cfg.model, "input": [{"type": "text", "text": text}], "encoding_format": "float", "dimensions": cfg.dimensions, "instructions": self._query_instruction if role == "query" else _CORPUS_INSTRUCTION}
        started = time.monotonic()
        deadline = started + cfg.total_timeout_seconds
        request_id = response_id = None
        usage = None
        status = None

        def metadata():
            return EmbeddingMetadata(request_id, response_id, usage, max(0, round((time.monotonic() - started) * 1000)), status)

        def check_deadline():
            if time.monotonic() >= deadline:
                raise EmbeddingError("embedding_deadline_exceeded", metadata=metadata())

        check_deadline()
        io_timeout = min(cfg.timeout_seconds, deadline - time.monotonic())
        if io_timeout <= 0:
            raise EmbeddingError("embedding_deadline_exceeded", metadata=metadata())
        try:
            with self._http.stream("POST", cfg.base_url.rstrip("/") + "/embeddings/multimodal", headers={"Authorization": "Bearer " + cfg.api_key, "Content-Type": "application/json"}, json=body, timeout=httpx.Timeout(io_timeout, connect=min(5.0, io_timeout))) as response:
                status = response.status_code
                request_id = _safe_id(response.headers.get("x-request-id"), cfg.api_key)
                check_deadline()
                if status != 200:
                    code = {401: "embedding_unauthorized", 403: "embedding_forbidden", 404: "embedding_not_available", 429: "embedding_rate_limited"}.get(status)
                    code = code or ("embedding_redirect_refused" if 300 <= status < 400 else "embedding_upstream_unavailable" if status >= 500 else "embedding_upstream_rejected")
                    raise EmbeddingError(code, metadata=metadata())
                raw = bytearray()
                for chunk in response.iter_bytes():
                    check_deadline()
                    if len(raw) + len(chunk) > cfg.max_response_bytes:
                        raise EmbeddingError("embedding_response_too_large", metadata=metadata())
                    raw.extend(chunk)
        except httpx.TimeoutException:
            code = "embedding_deadline_exceeded" if time.monotonic() >= deadline else "embedding_timeout"
            raise EmbeddingError(code, metadata=metadata()) from None
        except httpx.HTTPError:
            raise EmbeddingError("embedding_transport_error", metadata=metadata()) from None

        check_deadline()
        code = "invalid_embedding_response"
        try:
            envelope = _decode(bytes(raw))
            if not isinstance(envelope, dict):
                raise ValueError()
            response_id = _safe_id(envelope.get("id"), cfg.api_key)
            request_id = request_id or response_id
            usage = _usage(envelope.get("usage"))
            if response_id is None or envelope.get("object") != "list":
                raise ValueError()
            if envelope.get("model") != cfg.model:
                code = "embedding_model_mismatch"
                raise ValueError()
            data = envelope["data"]
            if not isinstance(data, dict) or data.get("object") != "embedding":
                raise ValueError()
            values = data["embedding"]
            if not isinstance(values, list) or len(values) != cfg.dimensions:
                code = "embedding_dimension_mismatch"
                raise ValueError()
            if any(type(value) not in (int, float) for value in values):
                raise ValueError()
            vector = tuple(float(value) for value in values)
            if not all(math.isfinite(value) for value in vector):
                raise ValueError()
            scale = max(abs(value) for value in vector)
            if scale == 0:
                code = "embedding_zero_vector"
                raise ValueError()
            # Stable L2 normalization avoids overflow and underflow. Both query
            # and corpus use this representation for cosine/dot-product search.
            scaled = tuple(value / scale for value in vector)
            norm = math.sqrt(math.fsum(value * value for value in scaled))
            normalized = tuple(value / norm for value in scaled)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise EmbeddingError(code, metadata=metadata()) from None
        check_deadline()
        return EmbeddingResult(normalized, self.space, role, metadata())
