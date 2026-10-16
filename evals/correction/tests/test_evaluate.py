import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import evaluate as evaluator


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = evaluator.read_json(HERE / "examples/manifest.synthetic.json")
        self.run = evaluator.read_json(HERE / "examples/predictions.synthetic.json")
        self.human = evaluator.read_json(HERE / "examples/human-decisions.synthetic.json")

    def score(self, human=None):
        return evaluator.evaluate(self.manifest, self.run, human, allow_missing=True)

    def test_failures_and_missing_stay_in_denominators(self):
        report = self.score()
        metrics = report["slices"]["all"]["model_candidates"]
        self.assertEqual(metrics["total_expected"], 14)
        self.assertEqual(metrics["decision_coverage"], evaluator.ratio(6, 14))
        self.assertEqual(metrics["correct_false_flag_rate"], evaluator.ratio(1, 3))
        self.assertEqual(metrics["wrong_missed_rate"], evaluator.ratio(9, 10))
        self.assertEqual(metrics["wrong_false_correct_rate"], evaluator.ratio(2, 10))
        self.assertEqual(metrics["known_truth_success_rate"], evaluator.ratio(3, 13))
        self.assertEqual(metrics["unresolved_ground_truth"], 1)
        self.assertEqual(report["missing_prediction_ids"], ["synthetic_14"])
        self.assertEqual(metrics["outcome_counts"]["technical_failure"], 2)
        self.assertEqual(metrics["outcome_counts"]["missing_detection"], 1)
        self.assertEqual(metrics["outcome_counts"]["no_output"], 1)

    def test_still_wrong_corrections_are_not_original_error_denominator(self):
        block = self.score()["slices"]["stage=correction"]["model_candidates"]
        self.assertEqual(block["still_wrong_correction_false_pass_rate"], evaluator.ratio(1, 2))
        self.assertEqual(block["still_wrong_correction_not_detected_rate"], evaluator.ratio(2, 2))

    def test_human_repair_does_not_overwrite_model_score(self):
        report = self.score(self.human)
        block = report["slices"]["all"]
        self.assertEqual(block["model_candidates"]["correct_false_flag_rate"], evaluator.ratio(1, 3))
        self.assertEqual(block["human_final"]["correct_false_flag_rate"], evaluator.ratio(0, 3))
        self.assertEqual(block["human_final"]["decision_coverage"], evaluator.ratio(3, 14))
        self.assertEqual(block["human_final"]["outcome_counts"]["not_reviewed"], 11)

    def test_request_cost_is_not_multiplied_by_number_of_questions(self):
        stats = self.score()["slices"]["all"]["request_telemetry"]
        self.assertEqual(stats["request_count"], 12)
        self.assertEqual(stats["known_cost_subtotal_by_currency"], {"CNY": 1.1})
        self.assertEqual(stats["cost_unknown_request_count"], 1)
        self.assertEqual(stats["latency_known_count"], 11)
        self.assertEqual(stats["latency_ms_p50"], 800)
        self.assertEqual(stats["latency_ms_p95"], 1300)
        self.assertEqual(stats["samples_without_request_telemetry"], ["synthetic_14"])

    def test_zero_denominator_and_unknown_telemetry_are_null_not_zero(self):
        for request in self.run["requests"]:
            request["latency_ms"] = None
            request["cost"] = {"amount": None, "currency": None}
        block = self.score()["slices"]["question_type=geometry_proof"]
        self.assertIsNone(block["model_candidates"]["correct_false_flag_rate"]["rate"])
        self.assertIsNone(block["request_telemetry"]["latency_ms_p50"])
        self.assertEqual(block["request_telemetry"]["known_cost_subtotal_by_currency"], {})
        self.assertEqual(block["request_telemetry"]["cost_unknown_request_count"], 1)

    def test_missing_predictions_strictly_rejected_by_default(self):
        with self.assertRaisesRegex(evaluator.EvaluationError, "missing predictions"):
            evaluator.evaluate(self.manifest, self.run)

    def test_no_provider_output_does_not_produce_empty_success_report(self):
        self.run["predictions"] = []
        self.run["requests"] = []
        block = self.score()["slices"]["all"]
        self.assertEqual(block["model_candidates"]["decision_coverage"], evaluator.ratio(0, 14))
        self.assertEqual(block["model_candidates"]["wrong_missed_rate"], evaluator.ratio(10, 10))
        self.assertEqual(block["model_candidates"]["outcome_counts"]["missing_prediction"], 14)
        self.assertIsNone(block["request_telemetry"]["latency_ms_p95"])

    def test_unreviewed_ground_truth_cannot_claim_a_known_answer(self):
        self.manifest["samples"][0]["label"]["status"] = "pending"
        with self.assertRaisesRegex(evaluator.EvaluationError, "schema"):
            self.score()

    def test_duplicate_prediction_rejected_even_if_identical(self):
        self.run["predictions"].append(copy.deepcopy(self.run["predictions"][0]))
        with self.assertRaisesRegex(evaluator.EvaluationError, "duplicate prediction"):
            self.score()

    def test_duplicate_request_cannot_double_count_bill(self):
        self.run["requests"].append(copy.deepcopy(self.run["requests"][0]))
        with self.assertRaisesRegex(evaluator.EvaluationError, "duplicate request_id"):
            self.score()

    def test_duplicate_sample_rejected(self):
        self.manifest["samples"].append(copy.deepcopy(self.manifest["samples"][0]))
        with self.assertRaisesRegex(evaluator.EvaluationError, "duplicate sample_id"):
            self.score()

    def test_question_variants_cannot_leak_across_splits(self):
        self.manifest["samples"][-1]["question_group_id"] = self.manifest["samples"][0]["question_group_id"]
        with self.assertRaisesRegex(evaluator.EvaluationError, "cross-split leakage"):
            self.score()

    def test_same_capture_cannot_leak_across_splits(self):
        self.manifest["samples"][-1]["capture_group_id"] = self.manifest["samples"][0]["capture_group_id"]
        with self.assertRaisesRegex(evaluator.EvaluationError, "cross-split leakage"):
            self.score()

    def test_same_image_hash_cannot_leak_despite_renaming_groups(self):
        self.manifest["samples"][-1]["source_asset_sha256"] = "a" * 64
        self.manifest["samples"][0]["source_asset_sha256"] = "a" * 64
        with self.assertRaisesRegex(evaluator.EvaluationError, "cross-split leakage"):
            self.score()

    def test_out_of_split_prediction_rejected(self):
        row = self.run["predictions"][0]
        row["sample_id"] = "synthetic_15"
        row["input_versions"] = self.manifest["samples"][-2]["input_versions"]
        with self.assertRaisesRegex(evaluator.EvaluationError, "outside requested split"):
            self.score()

    def test_stale_recognition_revision_rejected(self):
        self.run["predictions"][0]["input_versions"]["attempt_recognition_revision"] += 1
        with self.assertRaisesRegex(evaluator.EvaluationError, "input versions"):
            self.score()

    def test_wrong_manifest_version_rejected(self):
        self.run["dataset_version"] = "different"
        with self.assertRaisesRegex(evaluator.EvaluationError, "dataset_version"):
            self.score()

    def test_failure_cannot_also_be_correct(self):
        self.run["predictions"][6]["verdict"] = "correct"
        with self.assertRaisesRegex(evaluator.EvaluationError, "schema"):
            self.score()

    def test_model_cannot_claim_human_confirmed(self):
        self.run["predictions"][0]["outcome"] = "confirmed"
        with self.assertRaisesRegex(evaluator.EvaluationError, "schema"):
            self.score()

    def test_unknown_version_requires_explanation(self):
        self.run["provenance"]["model_version"] = None
        with self.assertRaisesRegex(evaluator.EvaluationError, "schema"):
            self.score()
        self.run["provenance"]["version_unknown_reason"] = "provider exposes alias only"
        self.assertTrue(any("version unknown" in w for w in self.score()["warnings"]))

    def test_unauthorized_real_samples_cannot_be_scored(self):
        self.manifest["synthetic_only"] = self.run["synthetic_only"] = False
        for sample in self.manifest["samples"]:
            sample["authorization"]["status"] = "authorized"
            sample["asset_ref"] = "private_asset_" + sample["sample_id"]
            sample["source_asset_sha256"] = sample["input_versions"]["fingerprint"]
            if sample["label"]["status"] == "reviewed":
                sample["label"]["basis_kind"] = "human_verified_solution"
                sample["label"]["reviewer_role"] = "independent_subject_reviewer"
        self.manifest["samples"][0]["authorization"]["status"] = "withdrawn"
        with self.assertRaisesRegex(evaluator.EvaluationError, "authorization not valid"):
            self.score()

    def test_candidate_must_reference_measured_or_unknown_request_record(self):
        self.run["predictions"][0]["request_ids"] = []
        with self.assertRaisesRegex(evaluator.EvaluationError, "schema"):
            self.score()

    def test_invalid_request_sample_link_rejected(self):
        self.run["predictions"][0]["request_ids"] = ["synthetic_request_13"]
        with self.assertRaisesRegex(evaluator.EvaluationError, "request/sample linkage"):
            self.score()

    def test_correction_cannot_reuse_original_attempt_id(self):
        self.manifest["samples"][9]["input_versions"]["attempt_id"] = "attempt_03"
        with self.assertRaisesRegex(evaluator.EvaluationError, "new attempt"):
            self.score()

    def test_correction_parent_cycle_rejected(self):
        a, b = self.manifest["samples"][2], self.manifest["samples"][9]
        a["stage"] = "correction"
        a["parent_sample_id"] = b["sample_id"]
        with self.assertRaisesRegex(evaluator.EvaluationError, "cycle"):
            self.score()

    def test_human_decision_for_different_model_run_rejected(self):
        self.human["applies_to_model_run_id"] = "another_run"
        with self.assertRaisesRegex(evaluator.EvaluationError, "different model run"):
            self.score(self.human)

    def test_partial_and_unanswered_are_not_correct(self):
        for value in ("partially_correct", "unanswered"):
            self.run["predictions"][0]["verdict"] = value
            self.assertEqual(self.score()["slices"]["all"]["model_candidates"]["correct_false_flag_rate"],
                             evaluator.ratio(1, 3))

    def test_synthetic_result_cannot_be_release_evidence(self):
        result = self.score()
        self.assertTrue(result["synthetic_only"])
        self.assertEqual(result["performance_evidence"], "synthetic_fixture_only")
        self.assertEqual(result["release_approval"], "not_assessed")

    def test_reject_duplicate_json_keys_and_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            for payload in ('{"key":1,"key":2}', '{"n": NaN}', '{"n": 1e999}'):
                path.write_text(payload)
                with self.assertRaises(evaluator.EvaluationError):
                    evaluator.read_json(path)

    def test_cli_uses_explicit_missing_mode_and_writes_hashed_report(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            command = [sys.executable, str(HERE / "evaluate.py"),
                       str(HERE / "examples/manifest.synthetic.json"),
                       str(HERE / "examples/predictions.synthetic.json"), "--output", str(output)]
            strict = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(strict.returncode, 2)
            self.assertFalse(output.exists())
            result = subprocess.run(command + ["--allow-missing"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(len(report["input_file_sha256"]["manifest"]), 64)
            self.assertTrue(report["synthetic_only"])


if __name__ == "__main__":
    unittest.main()
