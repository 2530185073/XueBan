"""Offline tests for the commercial photo manifest and gold-only metrics."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import commercial_eval as evaluation


def write_image(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def make_fixture(root: Path):
    rows = [
        ("按科目整理/数学/五年级_01.jpg", b"math-primary"),
        ("按科目整理/数学/五年级_01-copy.jpg", b"math-primary"),
        ("按科目整理/数学/五年级_02.jpg", b"math-primary-2"),
        ("按科目整理/数学/七年级_01.jpg", b"math-junior"),
        ("按科目整理/英语/年级待确认_01.jpg", b"english-unknown-grade"),
        ("按科目整理/生物/七年级_01.jpg", b"biology-junior"),
    ]
    return [(relative, write_image(root / relative, content)) for relative, content in rows]


def test_scan_hash_dedup_is_relative_and_does_not_copy(tmp_path):
    rows = make_fixture(tmp_path / "photos")
    manifest = evaluation.build_manifest(tmp_path / "photos", seed="test-seed", holdout_ratio=0.5,
                                         repo_root=tmp_path / "missing-repo")

    assert manifest["model_calls"] == 0
    assert manifest["coverage"]["total_image_files"] == 6
    assert manifest["coverage"]["unique_pages"] == 5
    assert manifest["coverage"]["duplicate_files_excluded"] == 1
    assert manifest["coverage"]["duplicate_files_removed"] == 1
    assert "未物理删除" in manifest["coverage"]["duplicate_files_note"]
    assert len(manifest["samples"]) == 5
    assert all(not Path(sample["asset_ref"]).is_absolute() for sample in manifest["samples"])
    assert all(sample["duplicate_count"] >= 1 for sample in manifest["samples"])
    duplicate = next(sample for sample in manifest["samples"] if sample["duplicate_count"] == 2)
    assert {duplicate["asset_ref"], *duplicate["duplicate_paths"]} == {
        "按科目整理/数学/五年级_01.jpg",
        "按科目整理/数学/五年级_01-copy.jpg",
    }
    assert {path for path in (tmp_path / "photos").rglob("*") if path.is_file()} == {
        tmp_path / "photos" / relative for relative, _ in rows
    }


def test_split_is_deterministic_and_duplicate_group_has_one_split(tmp_path):
    root = tmp_path / "photos"
    make_fixture(root)
    first = evaluation.build_manifest(root, seed="stable", holdout_ratio=0.5,
                                      repo_root=tmp_path / "missing-repo")
    second = evaluation.build_manifest(root, seed="stable", holdout_ratio=0.5,
                                       repo_root=tmp_path / "missing-repo")
    first_splits = {sample["sha256"]: sample["split"] for sample in first["samples"]}
    second_splits = {sample["sha256"]: sample["split"] for sample in second["samples"]}
    assert first_splits == second_splits
    assert all(sample["split"] in {"development", "holdout"} for sample in first["samples"])
    assert first["split_policy"]["holdout_independence"] == "not_established"
    assert "历史调试或测试" in first["split_policy"]["holdout_notice"]
    math_grade_5 = [sample for sample in first["samples"]
                    if sample["subject"] == "数学" and sample["grade"] == 5]
    assert {sample["split"] for sample in math_grade_5} == {"development", "holdout"}


def test_subject_grade_stratification_marks_unknown_grade_out_of_scope(tmp_path):
    root = tmp_path / "photos"
    make_fixture(root)
    manifest = evaluation.build_manifest(root, repo_root=tmp_path / "missing-repo")

    unknown = next(sample for sample in manifest["samples"] if sample["subject"] == "英语")
    assert unknown["grade"] is None
    assert unknown["stratum"] == "英语/年级待确认"
    assert unknown["in_product_scope"] is False
    math = next(sample for sample in manifest["samples"] if sample["subject"] == "数学"
                and sample["grade"] == 5)
    assert math["in_product_scope"] is True
    assert manifest["coverage"]["approved_scope_pair_count"] == 26
    covered = next(row for row in manifest["coverage"]["approved_scope_coverage"]
                   if row["grade"] == 5 and row["subject"] == "数学")
    assert covered["unique_pages"] == 2


def test_fallback_scope_keeps_primary_science_publishers_distinct(tmp_path):
    manifest = evaluation.build_manifest(tmp_path, repo_root=tmp_path / "missing-repo")
    assert manifest["coverage"]["source_scope"] == "embedded_requested_scope"
    science = {row["grade"]: row["publisher"]
               for row in manifest["coverage"]["approved_scope_coverage"]
               if row["subject"] == "科学"}
    assert science == {5: "科学教育出版社", 6: "教育科学出版社"}


def test_duplicates_with_conflicting_folder_labels_still_share_one_split(tmp_path):
    digest = write_image(tmp_path / "数学/五年级_01.jpg", b"same-page")
    write_image(tmp_path / "语文/七年级_01.jpg", b"same-page")
    manifest = evaluation.build_manifest(tmp_path, holdout_ratio=0.5,
                                         repo_root=tmp_path / "missing-repo")
    assert len(manifest["samples"]) == 1
    sample = manifest["samples"][0]
    assert sample["sha256"] == digest
    assert sample["metadata_conflict"] is True
    assert sample["duplicate_count"] == 2
    assert len(sample["duplicate_paths"]) == 1


def test_only_verified_gold_can_report_accuracy_or_wrong_as_correct(tmp_path):
    root = tmp_path / "photos"
    make_fixture(root)
    manifest = evaluation.build_manifest(root, seed="gold", holdout_ratio=1.0,
                                         repo_root=tmp_path / "missing-repo")
    holdout = [sample for sample in manifest["samples"] if sample["split"] == "holdout"]
    assert holdout

    predictions = {"predictions": []}
    labels = {"labels": []}
    for index, sample in enumerate(holdout):
        if index == 0:
            labels["labels"].append({"sample_id": sample["sample_id"], "verified": True,
                                      "gold_verdict": "incorrect"})
            predictions["predictions"].append({"sample_id": sample["sample_id"],
                                                "verdict": "correct"})
        else:
            labels["labels"].append({"sample_id": sample["sample_id"], "verified": False,
                                      "gold_verdict": "incorrect"})
            predictions["predictions"].append({"sample_id": sample["sample_id"],
                                                "verdict": "incorrect"})
    labeled = evaluation.apply_gold(manifest, labels)
    metrics = evaluation.evaluate_predictions(labeled, predictions)
    assert metrics["verified_gold_evaluated"] == 1
    assert metrics["unverified_or_unlabeled_excluded"] == len(holdout) - 1
    assert metrics["accuracy"] == 0.0
    assert metrics["wrong_as_correct_count"] == 1
    assert metrics["wrong_as_correct_denominator"] == 1
    assert metrics["wrong_as_correct_rate"] == 1.0

    no_gold = evaluation.build_manifest(root, seed="gold", holdout_ratio=1.0,
                                        repo_root=tmp_path / "missing-repo")
    no_gold_metrics = evaluation.evaluate_predictions(no_gold, predictions)
    assert no_gold_metrics["accuracy"] is None
    assert no_gold_metrics["wrong_as_correct_rate"] is None
    assert no_gold_metrics["accuracy_status"] == "not_available_no_verified_gold"


def test_cli_emits_json_and_never_calls_a_model(tmp_path, capsys):
    root = tmp_path / "photos"
    make_fixture(root)
    assert evaluation.main([str(root), "--seed", "cli", "--holdout-ratio", "0.5"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model_calls"] == 0
    assert payload["samples"]
    assert payload["coverage"]["unique_pages"] == 5


def test_invalid_verified_gold_is_rejected(tmp_path):
    root = tmp_path / "photos"
    make_fixture(root)
    manifest = evaluation.build_manifest(root, repo_root=tmp_path / "missing-repo")
    with pytest.raises(ValueError, match="verified gold verdict"):
        evaluation.apply_gold(manifest, {"labels": [{
            "sample_id": manifest["samples"][0]["sample_id"],
            "verified": True,
            "gold_verdict": "unknown",
        }]})


@pytest.mark.parametrize("verified", [False, None, "true", 1])
def test_truthy_verification_values_are_excluded_from_metrics(tmp_path, verified):
    write_image(tmp_path / "数学/六年级_01.jpg", b"synthetic-page")
    manifest = evaluation.build_manifest(tmp_path, holdout_ratio=1.0,
                                         repo_root=tmp_path / "missing-repo")
    sample_id = manifest["samples"][0]["sample_id"]
    labeled = evaluation.apply_gold(manifest, [{
        "sample_id": sample_id, "verified": verified, "gold_verdict": "incorrect",
    }])
    metrics = evaluation.evaluate_predictions(labeled, [{
        "sample_id": sample_id, "verdict": "correct",
    }])
    assert metrics["verified_gold_evaluated"] == 0
    assert metrics["unverified_or_unlabeled_excluded"] == 1
    assert metrics["accuracy"] is None
    assert metrics["wrong_as_correct_denominator"] == 0
    assert metrics["wrong_as_correct_rate"] is None


def test_verified_correct_gold_cannot_produce_wrong_as_correct_rate(tmp_path):
    write_image(tmp_path / "数学/六年级_01.jpg", b"synthetic-page")
    manifest = evaluation.build_manifest(tmp_path, holdout_ratio=1.0,
                                         repo_root=tmp_path / "missing-repo")
    sample_id = manifest["samples"][0]["sample_id"]
    labeled = evaluation.apply_gold(manifest, [{
        "sample_id": sample_id, "verified": True, "gold_verdict": "correct",
    }])
    metrics = evaluation.evaluate_predictions(labeled, [{
        "sample_id": sample_id, "verdict": "correct",
    }])
    assert metrics["accuracy"] == 1.0
    assert metrics["wrong_as_correct_denominator"] == 0
    assert metrics["wrong_as_correct_rate"] is None


def test_cli_writes_manifest_and_leaves_source_bytes_unchanged(tmp_path, capsys):
    root = tmp_path / "photos"
    rows = make_fixture(root)
    before = {relative: (root / relative).read_bytes() for relative, _ in rows}
    output = tmp_path / "outputs/商业验收题库清单.json"
    assert evaluation.main([str(root), "--output", str(output)]) == 0
    assert capsys.readouterr().out == ""
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["coverage"]["total_image_files"] == 6
    assert payload["coverage"]["duplicate_files_excluded"] == 1
    assert payload["model_calls"] == 0
    assert payload["split_policy"]["holdout_independence"] == "not_established"
    assert all(sample["gold"] == {"verified": False, "verdict": None}
               for sample in payload["samples"])
    assert {relative: (root / relative).read_bytes() for relative, _ in rows} == before
