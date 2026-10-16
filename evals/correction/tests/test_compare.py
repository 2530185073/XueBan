import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import json
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import compare
import evaluate as evaluator


def report(run, run_id):
    manifest = evaluator.read_json(HERE / "examples/manifest.synthetic.json")
    run = copy.deepcopy(run)
    run["run_id"] = run_id
    result = evaluator.evaluate(manifest, run, allow_missing=True)
    result["input_file_sha256"] = {"manifest": "a" * 64}
    return result


def wrongly_flagged(run):
    manifest = evaluator.read_json(HERE / "examples/manifest.synthetic.json")
    truth = {s["sample_id"]: s["label"]["verdict"] for s in manifest["samples"]}
    return next(p for p in run["predictions"]
                if p["verdict"] == "incorrect" and truth[p["sample_id"]] in evaluator.WRONG)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.run = evaluator.read_json(HERE / "examples/predictions.synthetic.json")
        self.gates = evaluator.read_json(HERE / "gates.json")
        self.baseline = report(self.run, "baseline")

    def test_identical_behavior_passes_but_is_not_release_approval(self):
        result = compare.compare(self.baseline, report(self.run, "candidate"), self.gates)
        self.assertEqual(result["regression_gate"], "pass")
        self.assertEqual(result["release_approval"], "not_assessed")
        self.assertIn("unchanged", {row["status"] for row in result["slices"]["all"].values()})
        self.assertTrue(any(w.startswith("PROVISIONAL_GATES") for w in result["warnings"]))

    def test_new_false_pass_of_wrong_answer_fails_gate(self):
        run = copy.deepcopy(self.run)
        flipped = wrongly_flagged(run)
        flipped["verdict"] = "correct"
        result = compare.compare(self.baseline, report(run, "candidate"), self.gates)
        self.assertEqual(result["regression_gate"], "fail")
        self.assertEqual(result["slices"]["all"]["wrong_false_correct_rate"]["status"], "regressed")

    def test_small_slices_are_reported_not_gated(self):
        run = copy.deepcopy(self.run)
        flipped = wrongly_flagged(run)
        flipped["verdict"] = "correct"
        gates = dict(self.gates, max_regression={})
        result = compare.compare(self.baseline, report(run, "candidate"), gates)
        self.assertEqual(result["regression_gate"], "pass")
        statuses = {rows["wrong_false_correct_rate"]["status"]
                    for name, rows in result["slices"].items() if name != "all"}
        self.assertIn("below_min_denominator", statuses)

    def test_latency_limit_and_unmeasured_latency(self):
        run = copy.deepcopy(self.run)
        for request in run["requests"]:
            request["latency_ms"] = 45000
        result = compare.compare(self.baseline, report(run, "candidate"), self.gates)
        self.assertEqual(result["slices"]["all"]["latency_ms_p95"]["limit"], "breached")
        self.assertEqual(result["regression_gate"], "fail")

    def test_wilson_interval_bounds(self):
        low, high = compare.wilson(0, 10)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.2)
        self.assertIsNone(compare.wilson(0, 0))

    def test_incomparable_reports_are_rejected(self):
        other = report(self.run, "candidate")
        other["dataset_version"] = "fixture-v2"
        with self.assertRaises(evaluator.EvaluationError):
            compare.compare(self.baseline, other, self.gates)
        other = report(self.run, "candidate")
        other["input_file_sha256"] = {"manifest": "b" * 64}
        with self.assertRaises(evaluator.EvaluationError):
            compare.compare(self.baseline, other, self.gates)
        with self.assertRaises(evaluator.EvaluationError):
            compare.compare(self.baseline, report(self.run, "baseline"), self.gates)
        with self.assertRaises(evaluator.EvaluationError):
            compare.compare(self.baseline, report(self.run, "c"), dict(self.gates, max_regression={"bogus": 1}))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, cand = Path(tmp, "base.json"), Path(tmp, "cand.json")
            base.write_text(json.dumps(self.baseline))
            run = copy.deepcopy(self.run)
            wrongly_flagged(run)["verdict"] = "correct"
            cand.write_text(json.dumps(report(run, "candidate")))
            command = [sys.executable, str(HERE / "compare.py"), str(base), str(cand)]
            failed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 1, failed.stderr)
            self.assertIn("REGRESSION", failed.stderr)
            cand.write_text(json.dumps(report(self.run, "candidate")))
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run([*command[:2], str(base), str(base)], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
