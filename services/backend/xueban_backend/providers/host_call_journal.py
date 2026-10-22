"""Private, per-run generate-call metadata; never wire or fee authorization.

This in-memory journal has no pre-send durable reservation. A crash may lose it.
It records host invocation parameters and returned metadata, not raw requests,
model candidates, images, credentials, provider envelopes, or reasoning.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from threading import Lock
from uuid import uuid4

from .ark import Metadata, ProviderError, _SAFE_TOKEN

PROTOCOL = "host-call-journal.v1"
STAGES = frozenset({"primary", "secondary", "reconciliation", "visual_reconciliation",
                    "visual_adjudication", "visual_readback", "critical_readback", "semantic_alignment"})


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _token(value, secrets):
    from .correction_pipeline import _contains_credential
    if (not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value)
            or any(_contains_credential(value, secret) for secret in secrets)):
        return None
    return value


def _metadata(value, secrets):
    """Use the same safe provider Metadata, with cross-provider echoes removed."""
    from .correction_pipeline import _contains_credential
    if not isinstance(value, Metadata):
        return None
    result = asdict(value)
    echoed = any(_contains_credential(result, secret) for secret in secrets)
    for key in ("requested_model", "model", "request_id", "response_id"):
        result[key] = None if echoed else _token(result[key], secrets)
    for attempt in result["attempts"]:
        attempt["model"] = None if echoed else _token(attempt["model"], secrets)
        attempt["request_id"] = None if echoed else _token(attempt["request_id"], secrets)
        attempt["charge_status"] = "unknown"
        if echoed:
            attempt["code"] = "provider_credential_echo"
            attempt["diagnostic"] = None
    return result


@dataclass(frozen=True)
class HostCallJournalSnapshot:
    _json: str = field(repr=False)

    def document(self):
        return json.loads(self._json)


class HostCallJournal:
    """One host-created collector per recognize invocation, never shared clients."""
    def __init__(self):
        self._lock = Lock()
        self._invocation_id = "hcj_" + uuid4().hex
        self._calls = {}
        self._status = "not_started"

    def start(self):
        with self._lock:
            if self._status != "not_started":
                raise ProviderError("invalid_configuration")
            self._status = "running"

    def begin(self, *, stage, asset_id, role, requested_model, max_output_tokens,
              target_labels, text, image_count, secrets):
        if stage not in STAGES:
            raise ProviderError("invalid_configuration")
        row = {"call_id": "hcall_" + uuid4().hex, "stage": stage,
               "asset_id": _token(asset_id, secrets), "role": role,
               "requested_model": _token(requested_model, secrets),
               "max_output_tokens": max_output_tokens, "image_count": image_count,
               "target_scope": "whole_page" if target_labels is None else "requested_targets",
               "requested_target_count": (None if target_labels is None else
                   sum(len(values) for values in target_labels.values()) if isinstance(target_labels, dict) else len(target_labels)),
               "requested_targets_sha256": None if target_labels is None else _digest(target_labels),
               "input_text_sha256": _digest(text), "started_at": _stamp(),
               "finished_at": None, "outcome": "started", "metadata": None,
               "attempt_metadata_available": False, "attempt_count": None}
        with self._lock:
            if self._status != "running":
                raise ProviderError("invalid_configuration")
            row["ordinal"] = len(self._calls) + 1
            self._calls[row["call_id"]] = row
        return row["call_id"]

    def finish(self, call_id, *, outcome, metadata, secrets):
        sanitized = _metadata(metadata, secrets)
        with self._lock:
            row = self._calls[call_id]
            if row["outcome"] != "started":
                return
            row.update(outcome=outcome, finished_at=_stamp(), metadata=sanitized,
                       attempt_metadata_available=sanitized is not None,
                       attempt_count=len(sanitized["attempts"]) if sanitized is not None else None)

    def close(self, outcome):
        with self._lock:
            self._status = outcome
            for row in self._calls.values():
                if row["outcome"] == "started":
                    row.update(outcome="interrupted", finished_at=_stamp())

    def snapshot(self):
        with self._lock:
            value = {"protocol": PROTOCOL, "evidence_level": "host_call_metadata",
                     "wire_verified": False, "scope_admission_eligible": False,
                     "invocation_id": self._invocation_id, "run_status": self._status,
                     "charge_status": "unknown", "calls": deepcopy(list(self._calls.values()))}
        return HostCallJournalSnapshot(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True))
