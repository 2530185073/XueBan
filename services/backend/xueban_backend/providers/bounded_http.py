"""Actual wall-clock cancellation for multimodel paid HTTP calls."""

import asyncio
from dataclasses import dataclass, field
import json

import httpx

from .ark import ProviderError, _strict_json, _token
from .correction_pipeline import _contains_credential


@dataclass
class ResponseRead:
    status: int | None = None
    request_id: str | None = None
    body: bytes = field(default=b"", repr=False)
    code: str = "ok"


def read_response(settings, endpoint, body, *, transport=None, responses=False):
    """Cancel the entire request, including waiting for headers and each read.

    Sync HTTPX phase timeouts are insufficient for a stream that trickles bytes.
    Every call owns its async connection pool so cancellation closes that call.
    The worker invokes this synchronous entry point from its dedicated thread.
    """
    result = ResponseRead()

    async def request():
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(settings.timeout_seconds,
                    connect=min(15.0, settings.timeout_seconds, settings.total_timeout_seconds))) as client:
            async with client.stream("POST", settings.base_url.rstrip("/") + endpoint,
                    headers={"Authorization": "Bearer " + settings.api_key, "Content-Type": "application/json"},
                    json=body) as response:
                result.status = response.status_code
                result.request_id = _token(response.headers.get("x-request-id"), settings.api_key)
                if result.status != 200:
                    result.code = ("upstream_rate_limited" if result.status == 429 else
                                   "upstream_unavailable" if 500 <= result.status <= 599 else "upstream_rejected")
                    return
                is_sse = responses and "text/event-stream" in response.headers.get("content-type", "").lower()
                size, buffer, lines = 0, b"", []
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > settings.max_response_bytes:
                        raise ProviderError("response_too_large")
                    buffer += chunk
                    if not is_sse:
                        continue
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.rstrip(b"\r")
                        if line.startswith(b"data:"):
                            lines.append(line[5:].lstrip(b" "))
                        elif not line and lines:
                            data, lines = b"\n".join(lines), []
                            if data == b"[DONE]":
                                raise ProviderError("incomplete_output")
                            event = _strict_json(data)
                            if not isinstance(event, dict):
                                raise ProviderError("invalid_output")
                            if _contains_credential(event, settings.api_key):
                                raise ProviderError("provider_credential_echo")
                            kind = event.get("type")
                            if kind == "error":
                                raise ProviderError("upstream_rejected")
                            if kind in {"response.completed", "response.incomplete", "response.failed"}:
                                envelope = event.get("response")
                                if not isinstance(envelope, dict) or envelope.get("status") != kind.removeprefix("response."):
                                    raise ProviderError("incomplete_output")
                                result.body = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()
                                return
                if is_sse:
                    raise ProviderError("incomplete_output")
                result.body = buffer

    async def timed():
        await asyncio.wait_for(request(), timeout=settings.total_timeout_seconds)

    try:
        asyncio.run(timed())
    except TimeoutError:
        result.code = "deadline_exceeded"
    except httpx.TimeoutException:
        result.code = "upstream_timeout"
    except httpx.HTTPError:
        result.code = "upstream_transport_error"
    except ProviderError as error:
        result.code = error.code
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        result.code = "invalid_output"
    return result
