"""Replay a frozen, labeled evaluation split through the production check roles.

Produces an evals/correction predictions file. Nothing here is a decision about
a student: every verdict is a model candidate scored offline by evaluate.py.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import sys

from PIL import Image, UnidentifiedImageError

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

from evals.correction import evaluate as offline  # noqa: E402
from scripts.ark_smoke import provider_settings  # noqa: E402
from xueban_backend.providers import ArkClient, ProviderError  # noqa: E402
from xueban_backend.providers import correction_pipeline as cp  # noqa: E402
from xueban_backend.providers.prompts import COMMON_PROMPT, ROLE_PROMPTS  # noqa: E402

ADAPTER_VERSION = "backend-check-replay.v1"
MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class RunError(ValueError):
    pass


def request_config_sha256(model: str) -> str:
    """Hash everything that changes model behavior, so prompt edits are visible."""
    config = {
        "model": model, "prompt_version": cp.PROMPT_VERSION, "adapter_version": ADAPTER_VERSION,
        "common_prompt": COMMON_PROMPT,
        "role_prompts": {str(role): ROLE_PROMPTS[role] for role in sorted(ROLE_PROMPTS, key=str)},
        "check_schema": cp.CHECK_SCHEMA,
        "pipeline_source_sha256": hashlib.sha256(Path(cp.__file__).read_bytes()).hexdigest(),
    }
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _pipeline_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def load_image(base: Path, spec: dict) -> cp.PipelineImage:
    path = (base / spec["path"]).resolve()
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise RunError(f"image digest mismatch for {spec['asset_id']}")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height, kind = image.width, image.height, image.format
    except UnidentifiedImageError:
        raise RunError(f"unsupported image for {spec['asset_id']}") from None
    if kind not in MIME:
        raise RunError(f"unsupported image format for {spec['asset_id']}")
    url = f"data:{MIME[kind]};base64," + base64.b64encode(data).decode()
    return cp.PipelineImage(spec["asset_id"], url, width, height)


def prepare(manifest: dict, inputs: dict, split: str):
    checks = offline.validators()
    offline.validate_manifest(manifest, checks)
    offline._shape("pipeline-inputs", inputs, checks)
    for key in ("dataset_id", "dataset_version", "synthetic_only"):
        if inputs[key] != manifest[key]:
            raise RunError(f"pipeline inputs {key} does not match manifest")
    cases = {}
    for case in inputs["cases"]:
        if case["sample_id"] in cases:
            raise RunError(f"duplicate pipeline input {case['sample_id']}")
        cases[case["sample_id"]] = case
    selected = [s for s in manifest["samples"] if s["split"] == split]
    if not selected:
        raise RunError(f"no samples in split {split}")
    unauthorized = [s["sample_id"] for s in selected
                    if s["authorization"]["status"] not in ("synthetic", "authorized")]
    if unauthorized:
        raise RunError(f"images without valid authorization are never sent: {unauthorized[:5]}")
    unknown = cases.keys() - {s["sample_id"] for s in selected}
    if unknown:
        raise RunError(f"pipeline inputs outside split {split}: {sorted(unknown)[:5]}")
    return selected, cases


def _payload(sample: dict, case: dict):
    versions = sample["input_versions"]
    question_id = _pipeline_id("q", versions["question_id"])
    attempt_id = _pipeline_id("a", versions["attempt_id"])
    question = {"question_id": question_id, "revision": versions["question_revision"],
                "label": case["question"]["label"], "stem_text": case["question"]["stem_text"],
                "regions": case["question"]["regions"]}
    attempt = {"id": attempt_id, "question_id": question_id,
               "captured_question_revision": versions["question_revision"],
               "recognition_revision": versions["attempt_recognition_revision"],
               "recognition_status": case["attempt"]["recognition_status"],
               "answer_text": case["attempt"]["answer_text"],
               "answer_status": case["attempt"]["answer_status"],
               "answer_regions": case["attempt"]["answer_regions"],
               "asset_ids": case["attempt"]["asset_ids"]}
    pipeline_versions = {"question_id": question_id, "question_revision": versions["question_revision"],
                         "attempt_id": attempt_id,
                         "attempt_recognition_revision": versions["attempt_recognition_revision"],
                         "case_revision_at_submission": 1, "fingerprint": versions["fingerprint"]}
    return question, attempt, pipeline_versions


def run_sample(pipeline, base: Path, run_id: str, sample: dict, case: dict | None):
    request_id = re.sub(r"[^a-zA-Z0-9_.:-]", "_", f"{run_id}.{sample['sample_id']}")[:128]
    prediction = {"sample_id": sample["sample_id"], "input_versions": dict(sample["input_versions"]),
                  "request_ids": [], "outcome": "no_output", "verdict": None, "reason": "pipeline_input_missing"}
    if case is None:
        return prediction, None, None
    question, attempt, versions = _payload(sample, case)
    images = tuple(load_image(base, spec) for spec in case["images"])
    method = pipeline.recheck if sample["stage"] == "correction" else pipeline.check
    model = None
    try:
        result = method(images=images, question=question, attempt=attempt, input_versions=versions)
    except ProviderError as error:
        latency = error.latency_ms if error.attempts else None
        if error.code == "invalid_input":
            prediction["reason"] = "invalid_input"
            return prediction, None, None
        prediction.update(outcome="technical_failure", reason=error.code)
        models = {a.model for a in error.attempts if a.model}
        model = models.pop() if len(models) == 1 else None
    else:
        latency = result.metadata.latency_ms
        model = result.metadata.model
        if result.data["verdict"] == "unable_to_judge":
            prediction.update(outcome="unable_to_judge", reason=result.data["reason_code"] or "needs_staff")
        else:
            prediction.update(outcome="candidate", verdict=result.data["verdict"], reason=None)
    prediction["request_ids"] = [request_id]
    request = {"request_id": request_id, "sample_ids": [sample["sample_id"]], "latency_ms": latency,
               "cost": {"amount": None, "currency": None}, "raw_response_ref": None}
    return prediction, request, model


def run(manifest: dict, inputs: dict, base: Path, pipeline, *, split: str, run_id: str,
        requested_model: str, concurrency: int = 2) -> dict:
    selected, cases = prepare(manifest, inputs, split)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        outputs = list(pool.map(lambda s: run_sample(pipeline, base, run_id, s, cases.get(s["sample_id"])), selected))
    models = {model for _, _, model in outputs if model}
    model_version = models.pop() if len(models) == 1 else None
    run_file = {
        "schema_version": "0.1", "dataset_id": manifest["dataset_id"],
        "dataset_version": manifest["dataset_version"], "synthetic_only": manifest["synthetic_only"],
        "split": split, "run_id": run_id, "output_kind": "model_candidate",
        "provenance": {
            "provider": "byteplus_ark", "model": requested_model, "model_version": model_version,
            "version_unknown_reason": None if model_version else
                ("provider returned several model ids" if models else "provider did not report a model id"),
            "adapter_version": f"{ADAPTER_VERSION}+{cp.PROMPT_VERSION}",
            "request_config_sha256": request_config_sha256(requested_model),
        },
        "requests": [request for _, request, _ in outputs if request],
        "predictions": [prediction for prediction, _, _ in outputs],
    }
    offline._shape("predictions", run_file, offline.validators())
    return run_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("pipeline_inputs", type=Path)
    parser.add_argument("--split", required=True, choices=("development", "calibration", "holdout"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--live", action="store_true", help="required: sends authorized images to the paid model")
    args = parser.parse_args(argv)
    if not args.live:
        print("refusing to call the paid model without --live", file=sys.stderr)
        return 2
    if args.output.exists():
        print("output exists; every run gets a new file", file=sys.stderr)
        return 2
    try:
        manifest, inputs = offline.read_json(args.manifest), offline.read_json(args.pipeline_inputs)
        settings = provider_settings(args.env_file)
        with cp.CorrectionPipeline(ArkClient(settings)) as pipeline:
            result = run(manifest, inputs, args.pipeline_inputs.parent, pipeline, split=args.split,
                         run_id=args.run_id, requested_model=settings.model, concurrency=args.concurrency)
    except (offline.EvaluationError, RunError, OSError, ValueError) as exc:
        print(f"run rejected: {exc}", file=sys.stderr)
        return 2
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    counts = {}
    for prediction in result["predictions"]:
        counts[prediction["outcome"]] = counts.get(prediction["outcome"], 0) + 1
    print(json.dumps({"run_id": args.run_id, "outcomes": counts}, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
