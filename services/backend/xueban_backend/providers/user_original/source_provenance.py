"""Private receipt for an answer-blind source read, not an AI confidence flag.

Only recognition issues this receipt, after the independent reader succeeds.
The worker stores it in private, revision-bound annotations. Public request
fields and a model's self-reported confidence cannot create this evidence.
"""
import hashlib
import hmac
import json


VERSION = "answer-blind-source.v1"


def _digest(images, question, attempt):
    ids = list(dict.fromkeys(
        [region["asset_id"] for region in question["regions"]] + attempt["asset_ids"]))
    if not ids or any(asset not in images for asset in ids):
        raise ValueError("missing_source")
    value = {
        "source": [{"id": asset, "width": images[asset].width_px,
                    "height": images[asset].height_px,
                    "sha256": hashlib.sha256(images[asset].data_url.encode("utf8")).hexdigest()}
                   for asset in ids],
        "question": {key: question.get(key, default) for key, default in (
            ("label", None), ("stem_text", None), ("question_type", "unknown"),
            ("options", []), ("blank_count", 0), ("has_diagram", False))},
        "answer": {key: attempt.get(key, default) for key, default in (
            ("answer_text", None), ("answer_status", "unclear"), ("answer_slots", []))},
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()


def issue_source_evidence(images, item):
    """Call only after a successful independent source read, never on raw OCR."""
    attempt = {**item, "asset_ids": list(dict.fromkeys(
        region["asset_id"] for region in item["regions"] + item["answer_regions"]))}
    return {"version": VERSION, "fingerprint": _digest(images, item, attempt)}


def source_evidence_matches(images, question, attempt, *, require_ready=True):
    receipt = attempt.get("_source_evidence")
    if (not isinstance(receipt, dict) or receipt.get("version") != VERSION
            or not isinstance(receipt.get("fingerprint"), str)
            or len(receipt["fingerprint"]) != 64
            or (require_ready and (attempt.get("recognition_status") != "ready"
                or question.get("recognition_uncertainties")
                or attempt.get("recognition_uncertainties")))):
        return False
    try:
        return hmac.compare_digest(receipt["fingerprint"], _digest(images, question, attempt))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
