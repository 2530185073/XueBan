"""Immutable initial source with a separate transcript-only public projection."""
import base64
from dataclasses import dataclass, field
import hashlib
import json
from threading import Lock

PROTOCOL = "recognition-draft.v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ReadCheckpoint:
    asset_id: str
    image_sha256: str
    stage: str
    source_sha256: str
    # Canonical immutable serialized projection; repr cannot expose a child's work.
    transcript_json: str = field(repr=False)
    source_json: str | None = field(default=None, repr=False)

    @classmethod
    def completed(cls, image, stage, data):
        if stage not in {"primary", "secondary"}:
            raise ValueError("checkpoint_stage")
        rows = tuple({"label": item["label"], "stem_text": item["stem_text"],
                      "answer_text": item["answer_text"]} for item in data["items"])
        if not 1 <= len(rows) <= 80:
            raise ValueError("checkpoint_size")
        return cls(image.asset_id, hashlib.sha256(base64.b64decode(
            image.data_url.split(",", 1)[1], validate=True)).hexdigest(), stage, digest(data),
            json.dumps(rows, ensure_ascii=False, allow_nan=False),
            json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True))

    def transcript(self):
        return json.loads(self.transcript_json)

    def full_source(self):
        data = json.loads(self.source_json)
        if digest(data) != self.source_sha256:
            raise ValueError("checkpoint_source_changed")
        return data


class ReadCheckpointSink:
    """Draft-only writes are best effort; optional durable admission fails closed."""
    def __init__(self, publish, failed=lambda: None, *, recovery=None, task_id=None):
        self._publish, self._failed = publish, failed
        self._lock = Lock()
        self.recovery, self.task_id = recovery, task_id
        self.reused = {}

    def before_generate(self, image, stage, contract):
        if self.recovery is None:
            return None
        with self._lock:
            if stage not in {"primary", "secondary"}:
                self.recovery["downstream"]()
                return None
            result = self.recovery["reserve"](image.asset_id, stage, contract)
            if result["source"] is not None:
                self.reused[(image.asset_id, stage)] = result["receipt_id"]
                return result["source"]
            return None

    def failure(self, image, stage, metadata):
        if self.recovery is not None and stage in {"primary", "secondary"}:
            with self._lock:
                self.recovery["failure"](image.asset_id, stage, metadata)

    def completed(self, image, stage, data, metadata=None):
        # Serialize only this invocation's short writes. SQLite does not honor
        # FOR UPDATE, so two simultaneously completed sources must not race its
        # audit revision. Model requests and different tasks stay independent.
        with self._lock:
            if self.recovery is not None:
                if (image.asset_id, stage) not in self.reused:
                    self.recovery["complete"](ReadCheckpoint.completed(image, stage, data), metadata, "check")
                return
            try:
                self._publish(ReadCheckpoint.completed(image, stage, data))
            except Exception:
                # No exception body, candidate, headers or credentials in diagnostics.
                try:
                    self._failed()
                except Exception:
                    pass  # An unavailable database cannot persist its own outage.
