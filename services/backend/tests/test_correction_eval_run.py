import hashlib
from pathlib import Path
import sys
import threading

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

from evals.correction import evaluate as offline  # noqa: E402
from scripts import correction_eval_run as runner  # noqa: E402
from xueban_backend.providers import ProviderError  # noqa: E402
from xueban_backend.providers.ark import Attempt, Metadata  # noqa: E402

FIXTURES = ROOT / "evals" / "correction" / "fixtures" / "ark-smoke"
EXAMPLES = ROOT / "evals" / "correction" / "examples"


class Result:
    def __init__(self, verdict, reason=None, latency=1200):
        self.data = {"verdict": verdict, "reason_code": reason}
        self.metadata = Metadata("deepseek-v4-1-flash-260910", "deepseek-v4-1-flash-260910", "r", "s", None, latency, ())


class FakePipeline:
    def __init__(self, script):
        self.script = script
        self.calls = []
        self.lock = threading.Lock()

    def _answer(self, role, images, question, attempt, input_versions):
        with self.lock:
            self.calls.append((role, question["question_id"], input_versions["fingerprint"], len(images)))
        outcome = self.script[input_versions["fingerprint"]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def check(self, **kwargs):
        return self._answer("check", **kwargs)

    def recheck(self, **kwargs):
        return self._answer("recheck", **kwargs)


def region(asset):
    return {"asset_id": asset, "coordinate_space": "original_pixels", "x": 0, "y": 0, "width": 10, "height": 10}


def case(sample_id, image="original.png"):
    data = (FIXTURES / image).read_bytes()
    return {"sample_id": sample_id,
            "images": [{"asset_id": "page-one", "path": f"../fixtures/ark-smoke/{image}",
                        "sha256": hashlib.sha256(data).hexdigest()}],
            "question": {"label": "1", "stem_text": "Expand 3(x - 2)", "regions": [region("page-one")]},
            "attempt": {"answer_text": "3x - 2", "answer_status": "present", "answer_regions": [region("page-one")],
                        "asset_ids": ["page-one"], "recognition_status": "ready"}}


@pytest.fixture
def manifest():
    return offline.read_json(EXAMPLES / "manifest.synthetic.json")


def inputs_for(manifest, sample_ids):
    return {"schema_version": "0.1", "dataset_id": manifest["dataset_id"],
            "dataset_version": manifest["dataset_version"], "synthetic_only": True,
            "cases": [case(s) for s in sample_ids]}


def by_id(manifest):
    return {s["sample_id"]: s for s in manifest["samples"]}


def test_run_maps_outcomes_and_scores_with_fixed_denominator(manifest):
    samples = by_id(manifest)
    correction = next(s for s in manifest["samples"] if s["stage"] == "correction")["sample_id"]
    ids = ["synthetic_01", "synthetic_02", "synthetic_03", correction]
    script = {
        samples["synthetic_01"]["input_versions"]["fingerprint"]: Result("incorrect"),
        samples["synthetic_02"]["input_versions"]["fingerprint"]: Result("unable_to_judge", "unclear_image"),
        samples["synthetic_03"]["input_versions"]["fingerprint"]: ProviderError(
            "timeout", attempts=(Attempt(1, None, "timeout", 90000),), latency_ms=90000),
        samples[correction]["input_versions"]["fingerprint"]: Result("correct", latency=800),
    }
    pipeline = FakePipeline(script)
    result = runner.run(manifest, inputs_for(manifest, ids), EXAMPLES, pipeline, split="holdout",
                        run_id="regress-001", requested_model="deepseek-v4-1-flash-260910")
    outcomes = {p["sample_id"]: (p["outcome"], p["verdict"], p["reason"]) for p in result["predictions"]}
    assert outcomes["synthetic_01"] == ("candidate", "incorrect", None)
    assert outcomes["synthetic_02"] == ("unable_to_judge", None, "unclear_image")
    assert outcomes["synthetic_03"] == ("technical_failure", None, "timeout")
    assert outcomes[correction] == ("candidate", "correct", None)
    assert outcomes["synthetic_05"] == ("no_output", None, "pipeline_input_missing")
    assert {role for role, *_ in pipeline.calls} == {"check", "recheck"}
    assert all(q.startswith("q-") for _, q, _, _ in pipeline.calls)
    assert result["provenance"]["model_version"] == "deepseek-v4-1-flash-260910"
    holdout = [s for s in manifest["samples"] if s["split"] == "holdout"]
    assert len(result["predictions"]) == len(holdout)

    report = offline.evaluate(manifest, result)
    telemetry = report["slices"]["all"]["request_telemetry"]
    assert telemetry["request_count"] == 4
    assert telemetry["latency_ms_p95"] == 90000
    assert report["slices"]["all"]["model_candidates"]["total_expected"] == len(holdout)


def test_prompt_change_changes_config_hash(monkeypatch):
    before = runner.request_config_sha256("m")
    monkeypatch.setattr(runner.cp, "PROMPT_VERSION", "photo-correction.vNext")
    assert runner.request_config_sha256("m") != before


def test_rejects_tampered_image_and_mismatched_inputs(manifest):
    inputs = inputs_for(manifest, ["synthetic_01"])
    inputs["cases"][0]["images"][0]["sha256"] = "0" * 64
    pipeline = FakePipeline({})
    with pytest.raises(runner.RunError, match="digest"):
        runner.run(manifest, inputs, EXAMPLES, pipeline, split="holdout", run_id="r1", requested_model="m")
    inputs = inputs_for(manifest, ["synthetic_01"])
    inputs["dataset_version"] = "other"
    with pytest.raises(runner.RunError, match="dataset_version"):
        runner.run(manifest, inputs, EXAMPLES, pipeline, split="holdout", run_id="r1", requested_model="m")
    assert pipeline.calls == []


def test_cli_requires_live_flag(tmp_path):
    assert runner.main([str(EXAMPLES / "manifest.synthetic.json"), str(tmp_path / "in.json"),
                        "--split", "holdout", "--run-id", "r", "--output", str(tmp_path / "o.json")]) == 2
