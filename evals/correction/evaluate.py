#!/usr/bin/env python3
"""Offline, denominator-preserving evaluation; never calls a provider."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


HERE = Path(__file__).resolve().parent
WRONG = {"incorrect", "partially_correct", "unanswered"}
MODEL_OUTCOMES = (
    "candidate", "unrecognized", "unable_to_judge", "technical_failure",
    "missing_detection", "no_output", "missing_prediction",
)


class EvaluationError(ValueError):
    """Invalid input is not a performance result."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise EvaluationError("non-finite numbers are forbidden")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"),
                       object_pairs_hook=_unique_object)
    _finite(value)
    return value


def _no_remote(uri):
    raise EvaluationError(f"remote schema resolution forbidden: {uri}")


def validators():
    documents = {}
    registry = Registry(retrieve=_no_remote)
    for path in sorted((HERE / "schemas").glob("*.schema.json")):
        document = read_json(path)
        Draft202012Validator.check_schema(document)
        documents[path.stem.removesuffix(".schema")] = document
        registry = registry.with_resource(document["$id"], Resource.from_contents(document))
    return {name: Draft202012Validator(doc, registry=registry)
            for name, doc in documents.items()}


def _shape(name, value, checks):
    _finite(value)
    errors = list(checks[name].iter_errors(value))
    if errors:
        details = "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                            for e in errors[:5])
        raise EvaluationError(f"{name} schema: {details}")


def _unique_rows(rows, key, context):
    result = {}
    for row in rows:
        identity = row[key]
        if identity in result:
            raise EvaluationError(f"duplicate {context}: {identity}")
        result[identity] = row
    return result


def validate_manifest(manifest, checks):
    _shape("manifest", manifest, checks)
    samples = _unique_rows(manifest["samples"], "sample_id", "sample_id")
    seen = {}
    for sample in samples.values():
        # Group checks cover the ENTIRE manifest before selecting a split.
        identities = {
            "question_group_id": sample["question_group_id"],
            "capture_group_id": sample["capture_group_id"],
            "question_id": sample["input_versions"]["question_id"],
            "fingerprint": sample["input_versions"]["fingerprint"],
            "source_asset_sha256": sample["source_asset_sha256"],
        }
        for field, identity in identities.items():
            if identity is None:
                continue
            previous = seen.setdefault((field, identity), sample["split"])
            if previous != sample["split"]:
                raise EvaluationError(f"cross-split leakage: {field}={identity}")
        synthetic = sample["authorization"]["status"] == "synthetic"
        if synthetic != manifest["synthetic_only"]:
            raise EvaluationError("synthetic and real samples must use separate manifests")
        if sample["label"]["status"] == "reviewed":
            label = sample["label"]
            expected_role = "synthetic_fixture" if synthetic else "independent_subject_reviewer"
            if label["reviewer_role"] != expected_role:
                raise EvaluationError(f"wrong ground-truth reviewer role: {sample['sample_id']}")
            if (label["basis_kind"] == "synthetic_fixture") != synthetic:
                raise EvaluationError("real labels cannot use synthetic/self-evaluated ground truth")
        if sample["stage"] == "correction":
            parent = samples.get(sample["parent_sample_id"])
            if parent is None:
                raise EvaluationError(f"unknown correction parent: {sample['sample_id']}")
            if any(parent[key] != sample[key] for key in ("question_group_id", "split")):
                raise EvaluationError(f"correction parent group/split mismatch: {sample['sample_id']}")
            if parent["input_versions"]["question_id"] != sample["input_versions"]["question_id"]:
                raise EvaluationError(f"correction question identity mismatch: {sample['sample_id']}")
            if parent["input_versions"]["attempt_id"] == sample["input_versions"]["attempt_id"]:
                raise EvaluationError("a correction must be a new attempt, not an OCR revision")
    for sample in samples.values():
        visited = set()
        current = sample
        while current["parent_sample_id"] is not None:
            if current["sample_id"] in visited:
                raise EvaluationError("correction parent cycle")
            visited.add(current["sample_id"])
            current = samples[current["parent_sample_id"]]
    return samples


def _run_matches(manifest, run):
    for key in ("dataset_id", "dataset_version", "synthetic_only"):
        if run[key] != manifest[key]:
            raise EvaluationError(f"run/manifest mismatch: {key}")


def _versions_match(row, selected):
    sample = selected.get(row["sample_id"])
    if sample is None:
        raise EvaluationError(f"prediction outside requested split or unknown: {row['sample_id']}")
    if row["input_versions"] != sample["input_versions"]:
        raise EvaluationError(f"stale/mismatched input versions: {row['sample_id']}")


def validate_inputs(manifest, run, human=None, allow_missing=False):
    checks = validators()
    samples = validate_manifest(manifest, checks)
    _shape("predictions", run, checks)
    _run_matches(manifest, run)
    selected = {key: value for key, value in samples.items() if value["split"] == run["split"]}
    if not selected:
        raise EvaluationError("selected split is empty")
    for sample in selected.values():
        if sample["authorization"]["status"] not in ("synthetic", "authorized"):
            raise EvaluationError(f"authorization not valid: {sample['sample_id']}")
        if not manifest["synthetic_only"] and (
                sample["asset_ref"] is None or sample["source_asset_sha256"] is None):
            raise EvaluationError(f"real sample needs private asset reference and hash: {sample['sample_id']}")
    requests = _unique_rows(run["requests"], "request_id", "request_id")
    for request in requests.values():
        if not set(request["sample_ids"]) <= selected.keys():
            raise EvaluationError(f"request references unknown/out-of-split samples: {request['request_id']}")
    rows = _unique_rows(run["predictions"], "sample_id", "prediction")
    for row in rows.values():
        _versions_match(row, selected)
        for request_id in row["request_ids"]:
            if request_id not in requests or row["sample_id"] not in requests[request_id]["sample_ids"]:
                raise EvaluationError(f"request/sample linkage invalid: {row['sample_id']}/{request_id}")
    missing = sorted(selected.keys() - rows.keys())
    if missing and not allow_missing:
        raise EvaluationError(f"missing predictions (use --allow-missing to retain as failures): {missing}")
    human_rows = None
    if human is not None:
        _shape("human-decisions", human, checks)
        _run_matches(manifest, human)
        if human["split"] != run["split"] or human["applies_to_model_run_id"] != run["run_id"]:
            raise EvaluationError("human decisions reference a different model run/split")
        human_rows = _unique_rows(human["decisions"], "sample_id", "human decision")
        _unique_rows(human["decisions"], "decision_id", "decision_id")
        for row in human_rows.values():
            _versions_match(row, selected)
    return selected, rows, human_rows, missing


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def metric_block(samples, rows, human=False):
    counts = Counter()
    correct = wrong = false_flag = missed = false_correct = 0
    still_wrong = correction_pass = correction_missed = known = judged_known = matched = 0
    for sample in samples:
        row = rows.get(sample["sample_id"])
        outcome = row["outcome"] if row else ("not_reviewed" if human else "missing_prediction")
        counts[outcome] += 1
        judged = outcome == ("confirmed" if human else "candidate")
        verdict = row["verdict"] if judged else None
        truth = sample["label"]["verdict"] if sample["label"]["status"] == "reviewed" else "uncertain"
        if truth == "uncertain":
            continue
        known += 1
        judged_known += int(judged)
        matched += int(judged and ((truth == "correct" and verdict == "correct") or
                                  (truth in WRONG and verdict in WRONG)))
        if truth == "correct":
            correct += 1
            false_flag += int(verdict in WRONG)
        else:
            wrong += 1
            missed += int(verdict not in WRONG)
            false_correct += int(verdict == "correct")
            if sample["stage"] == "correction":
                still_wrong += 1
                correction_pass += int(verdict == "correct")
                correction_missed += int(verdict not in WRONG)
    total = len(samples)
    outcome_names = ("confirmed", "unresolved", "not_reviewed") if human else MODEL_OUTCOMES
    return {
        "total_expected": total,
        "reviewed_ground_truth": known,
        "unresolved_ground_truth": total - known,
        "outcome_counts": {key: counts[key] for key in outcome_names},
        "decision_coverage": ratio(counts["confirmed" if human else "candidate"], total),
        "correct_false_flag_rate": ratio(false_flag, correct),
        "wrong_missed_rate": ratio(missed, wrong),
        "wrong_false_correct_rate": ratio(false_correct, wrong),
        "still_wrong_correction_false_pass_rate": ratio(correction_pass, still_wrong),
        "still_wrong_correction_not_detected_rate": ratio(correction_missed, still_wrong),
        "known_truth_decision_coverage": ratio(judged_known, known),
        "known_truth_success_rate": ratio(matched, known),
    }


def percentile(values, fraction):
    """Nearest-rank percentile, including measured failed requests."""
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)]


def telemetry(samples, requests):
    sample_ids = {s["sample_id"] for s in samples}
    applicable = [r for r in requests if sample_ids.intersection(r["sample_ids"])]
    covered = {sample_id for r in applicable for sample_id in r["sample_ids"]} & sample_ids
    latencies = [r["latency_ms"] for r in applicable if r["latency_ms"] is not None]
    costs = {}
    for request in applicable:
        cost = request["cost"]
        if cost["amount"] is not None:
            currency = cost["currency"]
            costs[currency] = costs.get(currency, Decimal(0)) + Decimal(str(cost["amount"]))
    return {
        "unit": "provider_request_including_retries_and_failures",
        "request_count": len(applicable),
        "samples_without_request_telemetry": sorted(sample_ids - covered),
        "latency_known_count": len(latencies),
        "latency_unknown_count": len(applicable) - len(latencies),
        "latency_ms_p50": percentile(latencies, 0.50),
        "latency_ms_p95": percentile(latencies, 0.95),
        "known_cost_subtotal_by_currency": {key: float(value) for key, value in sorted(costs.items())},
        "cost_unknown_request_count": sum(r["cost"]["amount"] is None for r in applicable),
        "attribution": "whole requests intersecting slice; overlapping slices are not additive",
    }


def slice_groups(samples):
    groups = {"all": list(samples)}
    for sample in samples:
        values = {
            "question_type": sample["question_type"], "stage": sample["stage"],
            "grade": sample["curriculum"]["grade"], "term": sample["curriculum"]["term"],
            "textbook_revision": sample["curriculum"]["textbook_revision"] or "unknown",
            "ground_truth_status": sample["label"]["status"],
        }
        for key, value in values.items():
            groups.setdefault(f"{key}={value}", []).append(sample)
        for condition in sample["image_conditions"]:
            groups.setdefault(f"image_condition={condition}", []).append(sample)
    return groups


def evaluate(manifest, run, human=None, allow_missing=False):
    selected, rows, human_rows, missing = validate_inputs(manifest, run, human, allow_missing)
    warnings = []
    if manifest["synthetic_only"]:
        warnings.append("SYNTHETIC_ONLY: fixture arithmetic, NOT real provider performance or release evidence")
    if missing:
        warnings.append("Missing predictions retained in every applicable denominator")
    if run["provenance"]["model_version"] is None:
        warnings.append("Provider model version unknown; cannot claim a reproducible model-version comparison")
    result = {
        "report_schema_version": "0.1", "dataset_id": manifest["dataset_id"],
        "dataset_version": manifest["dataset_version"], "split": run["split"],
        "synthetic_only": manifest["synthetic_only"],
        "performance_evidence": "synthetic_fixture_only" if manifest["synthetic_only"] else "offline_labeled_evaluation",
        "release_approval": "not_assessed", "model_run_id": run["run_id"],
        "provenance": run["provenance"], "warnings": warnings,
        "missing_prediction_ids": missing,
        "missing_human_decision_ids": sorted(selected.keys() - human_rows.keys()) if human_rows is not None else None,
        "dataset_split_counts": dict(sorted(Counter(s["split"] for s in manifest["samples"]).items())),
        "slices": {},
    }
    for name, samples in sorted(slice_groups(selected.values()).items()):
        result["slices"][name] = {
            "model_candidates": metric_block(samples, rows),
            "human_final": metric_block(samples, human_rows, human=True) if human_rows is not None else None,
            "request_telemetry": telemetry(samples, run["requests"]),
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--human-decisions", type=Path)
    parser.add_argument("--allow-missing", action="store_true",
                        help="explicitly report missing predictions as failures; strict by default")
    parser.add_argument("--output", type=Path, help="default stdout; use a temporary path or ignored outputs/")
    args = parser.parse_args(argv)
    try:
        manifest, run = read_json(args.manifest), read_json(args.predictions)
        human = read_json(args.human_decisions) if args.human_decisions else None
        report = evaluate(manifest, run, human, args.allow_missing)
        paths = {"manifest": args.manifest, "predictions": args.predictions}
        if args.human_decisions:
            paths["human_decisions"] = args.human_decisions
        report["input_file_sha256"] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                       for name, path in paths.items()}
        output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        return 0
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"evaluation rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
