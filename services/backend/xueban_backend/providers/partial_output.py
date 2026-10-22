"""Opt-in, local isolation of complete page JSON; never repair observations.

Transport/envelope/JSON security gates run before this module. The original
provider request remains invalid_output; a usable item is a separately
validated fragment, not a successful whole-page provider candidate.
"""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import json

from jsonschema import Draft202012Validator

from .ark import Metadata, ProviderError, _schema_diagnostic
from .correction_pipeline import _contains_credential

PROTOCOL = "partial-page-output-experimental-v1"
_CAPTURE = ContextVar("xueban_capture_page_schema_failure", default=False)


@contextmanager
def capture_page_failures(enabled):
    """Per-call scope; shared clients/threads cannot enable each other's capture."""
    token = _CAPTURE.set(enabled is True)
    try:
        yield
    finally:
        _CAPTURE.reset(token)


def capture_enabled():
    return _CAPTURE.get()


def _schema_key(schema):
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class PageSchemaFailure(ProviderError):
    """Private, one-use payload. Safe exception repr/str never include content.

    Never serialize __dict__, pickle, or attach this error to public task data.
    Only the page reader may consume the payload after rechecking all secrets.
    No provider envelope/reasoning content is retained here.
    """
    def __init__(self, candidate, schema, metadata):
        super().__init__("invalid_output", attempts=metadata.attempts, latency_ms=metadata.latency_ms)
        self.metadata = metadata
        self.__candidate = deepcopy(candidate)
        self.__schema_key = _schema_key(schema)

    def take_candidate(self, schema, secrets):
        candidate, self.__candidate = self.__candidate, None
        if candidate is None or self.__schema_key != _schema_key(schema):
            raise ProviderError("invalid_output", attempts=self.attempts, latency_ms=self.latency_ms)
        if any(_contains_credential(candidate, secret) for secret in secrets):
            raise ProviderError("provider_credential_echo", latency_ms=self.latency_ms,
                attempts=tuple(replace(attempt, code="provider_credential_echo", request_id=None, model=None)
                               for attempt in self.attempts))
        return candidate


@dataclass(frozen=True)
class AcceptedItem:
    source_index: int
    item: dict = field(repr=False)


@dataclass(frozen=True)
class RejectedItem:
    source_index: int
    layer: str
    rule: str
    path: tuple


@dataclass(frozen=True)
class Partition:
    source_count: int
    accepted: tuple[AcceptedItem, ...]
    rejected: tuple[RejectedItem, ...]

    def audit(self):
        return {"protocol": PROTOCOL, "source_count": self.source_count,
                "accepted_source_indices": [row.source_index for row in self.accepted],
                "rejected": [{"source_index": row.source_index, "layer": row.layer,
                              "rule": row.rule, "path": list(row.path)} for row in self.rejected],
                "bad_items_repaired": False, "missing_sources_fabricated": False}


@dataclass(frozen=True)
class ObservedPageFragment:
    """Internal validated subset, deliberately not a successful CandidateResult."""
    data: dict = field(repr=False)
    metadata: Metadata


def partition_page(data, schema):
    """Validate the original page shell, then each unchanged original item.

    Quarantine every member of a canonical-label collision, even if one
    member also fails its item schema. An invalid label is only a page gap;
    this function never assigns it to a question from another source.
    """
    from .whole_page import canonical_label
    shell = deepcopy(schema)
    try:
        items_schema = shell["properties"]["items"]
        item_schema = deepcopy(items_schema["items"])
        items_schema["items"] = {}
        Draft202012Validator(shell).validate(data)
    except Exception as error:
        # Shell errors must not expose JSONSchema's raw error.instance.
        raise ProviderError("invalid_output") from None
    # Schemas are server owned. This guard prevents callers using a relaxed
    # non-page schema from treating arbitrary JSON as a page response.
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not isinstance(item_schema, dict):
        raise ProviderError("invalid_output")
    values = data["items"]
    labels = [canonical_label(row["label"]) if isinstance(row, dict)
              and isinstance(row.get("label"), str) and len(row["label"]) <= 120 else None for row in values]
    counts = Counter(label for label in labels if label)
    accepted, rejected = [], []
    validator = Draft202012Validator(item_schema)
    for index, (item, label) in enumerate(zip(values, labels)):
        if label and counts[label] > 1:
            rejected.append(RejectedItem(index, "candidate_semantics", "duplicate_target_label", ("items", index, "label")))
            continue
        error = next(validator.iter_errors(item), None)
        if error is not None:
            diagnostic = _schema_diagnostic(error)
            rejected.append(RejectedItem(index, diagnostic.layer, diagnostic.rule, ("items", index, *diagnostic.path)))
            continue
        if not label:
            rejected.append(RejectedItem(index, "candidate_semantics", "empty_target_label", ("items", index, "label")))
            continue
        # A schema-valid null stem is an honest missing printed target, not
        # enough identity for an accepted answer observation in this experiment.
        if not isinstance(item.get("stem_text"), str) or not item["stem_text"].strip():
            rejected.append(RejectedItem(index, "candidate_semantics", "missing_target_text", ("items", index, "stem_text")))
            continue
        accepted.append(AcceptedItem(index, deepcopy(item)))
    return Partition(len(values), tuple(accepted), tuple(rejected))


def available(call):
    return call["code"] in ("ok", "partial_output")
