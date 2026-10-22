"""Actual wall-clock cancellation for multimodel paid HTTP calls."""

import asyncio
from dataclasses import asdict, dataclass, field
import json
import time
from types import SimpleNamespace

import httpx

from .ark import ProviderError, TransportDiagnostic, _strict_json, _token
from .correction_pipeline import _contains_credential


@dataclass
class ResponseRead:
    status: int | None = None
    request_id: str | None = None
    body: bytes = field(default=b"", repr=False)
    code: str = "ok"
    progress: TransportDiagnostic = field(default_factory=TransportDiagnostic)


def read_response(settings, endpoint, body, *, transport=None, responses=False):
    """Cancel the entire request, including waiting for headers and each read.

    Sync HTTPX phase timeouts are insufficient for a stream that trickles bytes.
    Every call owns its async connection pool so cancellation closes that call.
    The worker invokes this synchronous entry point from its dedicated thread.
    """
    result = ResponseRead()
    progress = SimpleNamespace(**asdict(result.progress))
    started = time.monotonic()

    def elapsed():
        return max(0, int((time.monotonic() - started) * 1000))

    async def request():
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(settings.timeout_seconds,
                    connect=min(15.0, settings.timeout_seconds, settings.total_timeout_seconds))) as client:
            async with client.stream("POST", settings.base_url.rstrip("/") + endpoint,
                    headers={"Authorization": "Bearer " + settings.api_key, "Content-Type": "application/json"},
                    json=body) as response:
                result.status = response.status_code
                progress.headers_received_ms = elapsed()
                progress.phase = "headers_received"
                result.request_id = _token(response.headers.get("x-request-id"), settings.api_key)
                if result.status != 200:
                    result.code = ("upstream_rate_limited" if result.status == 429 else
                                   "upstream_unavailable" if 500 <= result.status <= 599 else "upstream_rejected")
                    return
                is_sse = responses and "text/event-stream" in response.headers.get("content-type", "").lower()
                progress.content_mode = "sse" if is_sse else "body"
                progress.phase = "reading_sse" if is_sse else "reading_body"
                size, buffer, lines = 0, b"", []
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    progress.decoded_body_bytes = size
                    progress.body_chunks_received += 1
                    if chunk:
                        stamp = elapsed()
                        if progress.first_body_byte_ms is None:
                            progress.first_body_byte_ms = stamp
                        progress.last_body_byte_ms = stamp
                    if size > settings.max_response_bytes:
                        raise ProviderError("response_too_large")
                    buffer += chunk
                    progress.pending_buffer_bytes = len(buffer)
                    if not is_sse:
                        continue
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        progress.pending_buffer_bytes = len(buffer)
                        line = line.rstrip(b"\r")
                        if line.startswith(b"data:"):
                            progress.sse_data_lines += 1
                            if progress.first_sse_data_ms is None:
                                progress.first_sse_data_ms = elapsed()
                            lines.append(line[5:].lstrip(b" "))
                            progress.pending_data_lines = len(lines)
                        elif line.startswith(b":"):
                            progress.sse_comment_lines += 1
                        elif not line and lines:
                            data, lines = b"\n".join(lines), []
                            progress.pending_data_lines = 0
                            progress.sse_events += 1
                            progress.phase = "validating_sse_event"
                            if data == b"[DONE]":
                                progress.done_marker_seen = True
                                raise ProviderError("incomplete_output")
                            event = _strict_json(data)
                            if not isinstance(event, dict):
                                raise ProviderError("invalid_output")
                            if _contains_credential(event, settings.api_key):
                                raise ProviderError("provider_credential_echo")
                            kind = event.get("type")
                            if kind in {"response.output_text.delta", "response.output_text.done"}:
                                progress.output_text_events += 1
                                if progress.first_output_text_ms is None:
                                    progress.first_output_text_ms = elapsed()
                                if kind == "response.output_text.delta" and isinstance(event.get("delta"), str):
                                    progress.output_text_delta_chars += len(event["delta"])
                            elif kind in {"response.reasoning_text.delta", "response.reasoning_text.done",
                                          "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done"}:
                                progress.reasoning_events += 1
                            else:
                                progress.other_events += 1
                            if kind == "error":
                                raise ProviderError("upstream_rejected")
                            if kind in {"response.completed", "response.incomplete", "response.failed"}:
                                progress.terminal_event = kind.removeprefix("response.")
                                envelope = event.get("response")
                                if not isinstance(envelope, dict) or envelope.get("status") != kind.removeprefix("response."):
                                    raise ProviderError("incomplete_output")
                                result.body = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()
                                progress.terminal_accepted = True
                                progress.phase = "terminal_received"
                                return
                            progress.phase = "reading_sse"
                if is_sse:
                    raise ProviderError("incomplete_output")
                result.body = buffer
                progress.phase = "body_complete"

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
    progress.elapsed_ms = elapsed()
    result.progress = TransportDiagnostic(**vars(progress))
    return result
