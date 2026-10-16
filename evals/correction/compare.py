#!/usr/bin/env python3
"""Regression gate between two evaluate.py reports on the same frozen split.

Exit 0: no gated regression. Exit 1: regression or absolute limit breached.
Exit 2: the reports are not comparable or the gate file is invalid.
A pass is never release approval; it only says the candidate did not get worse.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

try:
    from .evaluate import EvaluationError, read_json
except ImportError:
    from evaluate import EvaluationError, read_json

LOWER_IS_BETTER = (
    "correct_false_flag_rate", "wrong_missed_rate", "wrong_false_correct_rate",
    "still_wrong_correction_false_pass_rate", "still_wrong_correction_not_detected_rate",
)
HIGHER_IS_BETTER = ("decision_coverage", "known_truth_decision_coverage", "known_truth_success_rate")
LATENCY = ("latency_ms_p50", "latency_ms_p95")
SAME = ("dataset_id", "dataset_version", "split", "synthetic_only")


def wilson(numerator, denominator, z=1.959963984540054):
    """95% Wilson score interval; stays inside [0, 1] for small samples."""
    if not denominator:
        return None
    p = numerator / denominator
    centre = (p + z * z / (2 * denominator)) / (1 + z * z / denominator)
    half = z * math.sqrt(p * (1 - p) / denominator + z * z / (4 * denominator ** 2)) / (1 + z * z / denominator)
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _validate_gates(gates):
    if gates.get("schema_version") != "0.1" or gates.get("status") not in ("provisional", "frozen"):
        raise EvaluationError("gates: schema_version 0.1 and status provisional|frozen required")
    known = set(LOWER_IS_BETTER + HIGHER_IS_BETTER + LATENCY)
    for section in ("max_regression", "limits"):
        unknown = set(gates.get(section, {})) - known
        if unknown:
            raise EvaluationError(f"gates: unknown metrics in {section}: {sorted(unknown)}")
    for name, value in gates.get("max_regression", {}).items():
        if not isinstance(value, (int, float)) or value < 0:
            raise EvaluationError(f"gates: max_regression.{name} must be >= 0")
    for name, bound in gates.get("limits", {}).items():
        if set(bound) - {"min", "max"} or not bound:
            raise EvaluationError(f"gates: limits.{name} takes min and/or max")
    if not isinstance(gates.get("min_slice_denominator", 0), int):
        raise EvaluationError("gates: min_slice_denominator must be an integer")


def _comparable(baseline, candidate):
    for key in SAME:
        if baseline.get(key) != candidate.get(key):
            raise EvaluationError(f"reports differ in {key}; only the same frozen split is comparable")
    base_hash = (baseline.get("input_file_sha256") or {}).get("manifest")
    cand_hash = (candidate.get("input_file_sha256") or {}).get("manifest")
    if not base_hash or base_hash != cand_hash:
        raise EvaluationError("manifest SHA-256 missing or different; regenerate both reports from one manifest")
    if baseline.get("model_run_id") == candidate.get("model_run_id"):
        raise EvaluationError("baseline and candidate are the same model run")


def _metric(block, telemetry, name):
    if name in LATENCY:
        value = telemetry.get(name)
        return {"value": value, "denominator": telemetry.get("latency_known_count", 0), "interval": None}
    ratio = block[name]
    return {"value": ratio["rate"], "denominator": ratio["denominator"],
            "interval": wilson(ratio["numerator"], ratio["denominator"])}


def _worse(name, base, cand):
    """Signed regression: positive means the candidate is worse."""
    if name in HIGHER_IS_BETTER:
        return base - cand
    return cand - base


def compare(baseline, candidate, gates):
    _validate_gates(gates)
    _comparable(baseline, candidate)
    min_denominator = gates.get("min_slice_denominator", 0)
    findings, failures = {}, []
    for slice_name in sorted(set(baseline["slices"]) | set(candidate["slices"])):
        base_slice, cand_slice = baseline["slices"].get(slice_name), candidate["slices"].get(slice_name)
        if base_slice is None or cand_slice is None:
            raise EvaluationError(f"slice {slice_name} missing from one report")
        rows = {}
        for name in LOWER_IS_BETTER + HIGHER_IS_BETTER + LATENCY:
            base = _metric(base_slice["model_candidates"], base_slice["request_telemetry"], name)
            cand = _metric(cand_slice["model_candidates"], cand_slice["request_telemetry"], name)
            row = {"baseline": base, "candidate": cand, "delta": None, "status": "insufficient_data"}
            gated = slice_name == "all" or min(base["denominator"], cand["denominator"]) >= min_denominator
            if base["value"] is not None and cand["value"] is not None:
                row["delta"] = cand["value"] - base["value"]
                worse = _worse(name, base["value"], cand["value"])
                allowed = gates.get("max_regression", {}).get(name)
                if not gated:
                    row["status"] = "below_min_denominator"
                elif allowed is not None and worse > allowed:
                    row["status"] = "regressed"
                    failures.append(f"{slice_name}:{name} worse by {worse:.4g} (allowed {allowed})")
                else:
                    row["status"] = "improved" if worse < 0 else "within_tolerance" if worse > 0 else "unchanged"
            if slice_name == "all" and name in gates.get("limits", {}):
                bound = gates["limits"][name]
                value = cand["value"]
                if value is None:
                    row["limit"] = "unmeasured"
                    failures.append(f"all:{name} unmeasured; limit cannot be shown to hold")
                elif ("max" in bound and value > bound["max"]) or ("min" in bound and value < bound["min"]):
                    row["limit"] = "breached"
                    failures.append(f"all:{name}={value:.4g} outside {bound}")
                else:
                    row["limit"] = "held"
            rows[name] = row
        findings[slice_name] = rows
    warnings = list(candidate.get("warnings", []))
    if gates["status"] != "frozen":
        warnings.append("PROVISIONAL_GATES: thresholds not frozen on real labeled data; not release evidence")
    if candidate.get("synthetic_only"):
        warnings.append("SYNTHETIC_ONLY: comparison exercises the gate mechanics only")
    return {
        "report_schema_version": "0.1", "dataset_id": candidate["dataset_id"],
        "dataset_version": candidate["dataset_version"], "split": candidate["split"],
        "baseline_run_id": baseline["model_run_id"], "candidate_run_id": candidate["model_run_id"],
        "baseline_provenance": baseline["provenance"], "candidate_provenance": candidate["provenance"],
        "gates_status": gates["status"], "regression_gate": "fail" if failures else "pass",
        "release_approval": "not_assessed", "failures": failures, "warnings": warnings,
        "slices": findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_report", type=Path)
    parser.add_argument("candidate_report", type=Path)
    parser.add_argument("--gates", type=Path, default=Path(__file__).resolve().parent / "gates.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare(read_json(args.baseline_report), read_json(args.candidate_report), read_json(args.gates))
    except (EvaluationError, OSError, ValueError, KeyError) as exc:
        print(f"comparison rejected: {exc}", file=sys.stderr)
        return 2
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    for failure in result["failures"]:
        print(f"REGRESSION {failure}", file=sys.stderr)
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
