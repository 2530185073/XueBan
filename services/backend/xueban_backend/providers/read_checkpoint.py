"""Invocation-owned, immutable draft event; never an assessment or wire receipt."""
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
            json.dumps(rows, ensure_ascii=False, allow_nan=False))

    def transcript(self):
        return json.loads(self.transcript_json)


class ReadCheckpointSink:
    """A host callback. Its failure never retries or fails provider inference."""
    def __init__(self, publish, failed=lambda: None):
        self._publish, self._failed = publish, failed
        self._lock = Lock()

    def completed(self, image, stage, data):
        # Serialize only this invocation's short writes. SQLite does not honor
        # FOR UPDATE, so two simultaneously completed sources must not race its
        # audit revision. Model requests and different tasks stay independent.
        with self._lock:
            try:
                self._publish(ReadCheckpoint.completed(image, stage, data))
            except Exception:
                # No exception body, candidate, headers or credentials in diagnostics.
                try:
                    self._failed()
                except Exception:
                    pass  # An unavailable database cannot persist its own outage.
