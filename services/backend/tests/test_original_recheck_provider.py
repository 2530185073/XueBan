"""Targeted reads have no old student answer or verdict in model inputs."""
from copy import deepcopy
import json

from test_two_model_pipeline import correct, flow, picture
from xueban_backend.providers import ProviderError
import pytest


def context():
    return {"images": (picture(),),
            "question": {"label": "1", "stem_text": "OLD_WRONG_STEM_DO_NOT_SEND",
                         "recognition_uncertainties": ["OLD_NOTE_DO_NOT_SEND"]},
            "attempt": {"asset_ids": [picture().asset_id], "answer_text": "OLD_ANSWER_DO_NOT_SEND",
                        "recognition_status": "needs_revision"},
            "input_versions": {"fingerprint": "synthetic_saved_input"}}


def test_rerecognize_scopes_two_reads_without_old_answers_and_without_shared_mutation():
    pipeline = flow([correct()], [correct()])
    result = pipeline.rerecognize_original(**context())
    assert len(result.data["items"]) == 1
    assert result.prompt_version.endswith(".original-recheck-r1")
    for client in (pipeline.client, pipeline.review_client):
        call = client.calls[0]
        task = json.loads(call["text"])["task_input"]
        assert task["original_target"]["label"] == "1"
        assert call["output_schema"]["properties"]["items"]["maxItems"] == 1
        assert "OLD_" not in call["text"]
        assert "只重新核对" in call["instructions"]
    assert not hasattr(pipeline, "_original_recheck_target")
    assert result.data["original_recheck"]["input_versions"] == context()["input_versions"]
    # A later ordinary page run does not inherit the targeted request.
    pipeline.recognize(images=(picture(),))
    assert json.loads(pipeline.client.calls[-1]["text"])["task_input"] == {}


def test_extra_target_is_not_silently_truncated_or_bound_to_requested_label():
    pipeline = flow([correct(), correct("2")], [correct(), correct("2")])
    with pytest.raises(ProviderError) as caught:
        pipeline.rerecognize_original(**context())
    assert len(caught.value.attempts) == 2


def test_target_label_is_not_written_over_actual_independent_observation():
    pipeline = flow([correct("3")], [correct("3")])
    result = pipeline.rerecognize_original(**context())
    assert result.data["items"][0]["label"] == "3"


def test_multiple_original_pages_fail_before_any_model_call():
    pipeline = flow()
    value = context()
    value["attempt"]["asset_ids"].append("different_asset")
    with pytest.raises(ProviderError, match="invalid_input"):
        pipeline.rerecognize_original(**value)
    assert not pipeline.client.calls and not pipeline.review_client.calls
