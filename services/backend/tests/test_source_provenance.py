from copy import deepcopy
from dataclasses import replace

import pytest

from test_user_original_pipeline import picture, region
from xueban_backend.providers.user_original.source_provenance import (
    issue_source_evidence, source_evidence_matches,
)


def bound():
    images = {"asset_original": picture()}
    item = {"label": "1", "stem_text": "计算2+2。", "question_type": "calculation",
            "regions": [region()], "answer_regions": [], "answer_text": "4",
            "answer_status": "present", "answer_slots": [], "recognition_status": "ready"}
    receipt = issue_source_evidence(images, item)
    question = deepcopy(item)
    attempt = {**deepcopy(item), "asset_ids": ["asset_original"], "_source_evidence": receipt}
    return images, question, attempt


def test_receipt_matches_unchanged_source_and_transcription():
    assert source_evidence_matches(*bound())


@pytest.mark.parametrize("target,key,value", [
    ("question", "stem_text", "计算2+3。"),
    ("question", "label", "2"),
    ("question", "has_diagram", True),
    ("question", "options", [{"label": "A", "text": "5"}]),
    ("question", "recognition_uncertainties", ["missing condition"]),
    ("attempt", "answer_text", "-4"),
    ("attempt", "answer_status", "blank"),
    ("attempt", "answer_slots", [{"index": 1, "answer_text": "4"}]),
    ("attempt", "recognition_status", "needs_revision"),
    ("attempt", "recognition_uncertainties", ["unreadable"]),
    ("attempt", "_source_evidence", {"version": "answer-blind-source.v1", "fingerprint": "a"*64}),
    ("attempt", "_source_evidence", True),
])
def test_changes_do_not_inherit_source_permission(target, key, value):
    images, question, attempt = bound()
    (question if target == "question" else attempt)[key] = value
    assert not source_evidence_matches(images, question, attempt)


def test_changed_source_pixels_are_not_the_original_receipt():
    images, question, attempt = bound()
    images["asset_original"] = replace(images["asset_original"], data_url="data:image/png;base64,changed")
    assert not source_evidence_matches(images, question, attempt)


def test_receipt_is_never_implied_by_ready_status():
    images, question, attempt = bound()
    attempt.pop("_source_evidence")
    assert not source_evidence_matches(images, question, attempt)


def test_quick_first_opinion_may_keep_uncertain_but_bound_source():
    images, question, attempt = bound()
    question["recognition_uncertainties"] = ["faint printing"]
    attempt["recognition_status"] = "needs_revision"
    attempt["recognition_uncertainties"] = ["faint handwriting"]
    assert source_evidence_matches(images, question, attempt, require_ready=False)
    assert not source_evidence_matches(images, question, attempt)
    attempt["answer_text"] = "-4"
    assert not source_evidence_matches(images, question, attempt, require_ready=False)


def test_quick_first_opinion_still_requires_real_unchanged_source_receipt():
    images, question, attempt = bound()
    attempt.pop("_source_evidence")
    assert not source_evidence_matches(images, question, attempt, require_ready=False)
