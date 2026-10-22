"""Build an offline evaluation manifest with SHA-256 duplicate isolation.

This module deliberately does not call a model.  It only reads image files and
optional, human-authored labels/predictions.

The manifest contains relative references to the source files; it never copies
or embeds an image.  Images are grouped by content SHA-256 before assigning a
split.  A verified gold label is the only kind of label accepted by
``evaluate_predictions``.  Unverified or missing labels are reported as
excluded and can never contribute to accuracy or the wrong-as-correct rate.

Example::

    python3 services/backend/scripts/commercial_eval.py \
        /Users/1130wake/Desktop/习题分类

The default output is JSON on stdout.  Use ``--output`` when a manifest file is
wanted.  The source image directory is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


IMAGE_EXTENSIONS = frozenset({
    ".avif", ".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg",
    ".png", ".tif", ".tiff", ".webp",
})

GRADE_LABELS = {5: "五年级", 6: "六年级", 7: "七年级", 8: "八年级"}
GRADE_PATTERNS = (
    (re.compile(r"五年级|小学五年级|五年"), 5),
    (re.compile(r"六年级|小学六年级|六年"), 6),
    (re.compile(r"七年级|初一|七年"), 7),
    (re.compile(r"八年级|初二|八年"), 8),
)

SUBJECT_ALIASES = {
    "语文": "语文",
    "数学": "数学",
    "英语": "英语",
    "物理": "物理",
    "化学": "化学",
    "历史": "历史",
    "地理": "地理",
    "生物": "生物学",
    "生物学": "生物学",
    "政治": "道德与法治",
    "道德与法治": "道德与法治",
    "科学": "科学",
    "美术": "美术",
    "艺术音乐": "艺术音乐",
    "音乐": "艺术音乐",
    "体育与健康": "体育与健康",
}

# Fallback scope.  In a checkout the scope is read from
# curriculum/selection.json, which remains the source of truth for the repo.
# Keep the explicitly approved science publishers distinct: grade 5 remains
# 科学教育出版社 (unresolved in the official catalog); grade 6 is 教育科学出版社.
REQUESTED_SCOPE = {
    5: {
        "语文": "人民教育出版社",
        "数学": "人民教育出版社",
        "英语": "陕西旅游出版社",
        "体育与健康": "人民教育出版社",
        "艺术音乐": "人民音乐出版社",
        "科学": "科学教育出版社",
    },
    6: {
        "语文": "人民教育出版社",
        "数学": "人民教育出版社",
        "英语": "陕西旅游出版社",
        "美术": "人民美术出版社",
        "科学": "教育科学出版社",
    },
    7: {
        "语文": "人民教育出版社",
        "数学": "北京师范大学出版社",
        "英语": "人民教育出版社",
        "历史": "人民教育出版社",
        "道德与法治": "人民教育出版社",
        "地理": "人民教育出版社",
        "生物学": "江苏凤凰科学技术出版社",
    },
    8: {
        "语文": "人民教育出版社",
        "数学": "北京师范大学出版社",
        "英语": "人民教育出版社",
        "历史": "人民教育出版社",
        "道德与法治": "人民教育出版社",
        "地理": "人民教育出版社",
        "生物学": "江苏凤凰科学技术出版社",
        "物理": "江苏凤凰科学技术出版社",
    },
}


def _canonical_subject(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return SUBJECT_ALIASES.get(value.strip(), value.strip() or None)


def _scope_pairs(scope: Mapping[int, Mapping[str, str]]) -> set:
    return {(grade, _canonical_subject(subject))
            for grade, subjects in scope.items()
            for subject in subjects}


def _scope_from_selection(repo_root: Optional[Path]) -> Tuple[Dict[int, Dict[str, str]], str]:
    """Read the checkout's approved scope without changing it.

    A fallback is intentionally available so the script remains usable when a
    copied script is run outside the repository.  The publisher is not inferred
    from a photo path; it only comes from this explicit scope file.
    """
    if repo_root is not None:
        selection_path = repo_root / "curriculum" / "selection.json"
        try:
            payload = json.loads(selection_path.read_text(encoding="utf-8"))
            scope: Dict[int, Dict[str, str]] = {}
            for class_row in payload.get("classes", []):
                grade = int(class_row["grade"])
                scope[grade] = {}
                for row in class_row.get("subjects", []):
                    subject = _canonical_subject(row.get("subject"))
                    if subject:
                        scope[grade][subject] = str(row.get("publisher", ""))
            if scope:
                return scope, str(selection_path)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass
    return {grade: dict(subjects) for grade, subjects in REQUESTED_SCOPE.items()}, "embedded_requested_scope"


def _infer_subject(relative_path: Path) -> Optional[str]:
    parts = list(relative_path.parts)
    # Prefer the directory immediately below the known organizer directory.
    if "按科目整理" in parts:
        index = parts.index("按科目整理")
        if index + 1 < len(parts):
            subject = _canonical_subject(parts[index + 1])
            if subject in set(SUBJECT_ALIASES.values()):
                return subject
    for part in parts[:-1]:
        subject = _canonical_subject(part)
        if subject in set(SUBJECT_ALIASES.values()):
            return subject
    return None


def _infer_grade(relative_path: Path) -> Optional[int]:
    text = "/".join(relative_path.parts)
    for pattern, grade in GRADE_PATTERNS:
        if pattern.search(text):
            return grade
    return None


def _grade_label(grade: Optional[int]) -> str:
    return GRADE_LABELS.get(grade, "年级待确认")


def _stratum(subject: Optional[str], grade: Optional[int]) -> str:
    return "%s/%s" % (subject or "科目待确认", _grade_label(grade))


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_hash(path: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _split_order_key(sha256: str, stratum: str, seed: str) -> str:
    """Return a stable ordering key for one content group in one stratum."""
    key = "%s\0%s\0%s" % (seed, stratum, sha256)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _assign_splits(groups: Sequence[Mapping[str, Any]], seed: str,
                   holdout_ratio: float) -> Dict[str, str]:
    """Assign SHA groups with a stable, per-stratum holdout allocation.

    Hashing each page against a global threshold is deterministic but can leave
    a small subject/grade stratum with no holdout at all.  Ordering groups by a
    seeded hash and allocating the requested count within each stratum keeps
    the split stratified while still making duplicate content one indivisible
    unit.  A non-trivial stratum with at least two unique pages gets at least
    one page in each split.
    """
    by_stratum: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for group in groups:
        by_stratum[str(group["stratum"])].append(group)
    assignments: Dict[str, str] = {}
    for stratum, rows in by_stratum.items():
        ordered = sorted(rows, key=lambda row: _split_order_key(str(row["sha256"]), stratum, seed))
        count = len(ordered)
        holdout_count = int(count * holdout_ratio + 0.5)
        if 0.0 < holdout_ratio < 1.0 and count >= 2:
            holdout_count = max(1, min(count - 1, holdout_count))
        holdout_hashes = {str(row["sha256"]) for row in ordered[:holdout_count]}
        for row in ordered:
            sha256 = str(row["sha256"])
            assignments[sha256] = "holdout" if sha256 in holdout_hashes else "development"
    return assignments


def _candidate_image_files(root: Path) -> List[Path]:
    return sorted(
        (path for path in root.rglob("*")
         if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _coverage(samples: Sequence[Mapping[str, Any]], total_files: int,
              scope: Mapping[int, Mapping[str, str]], scope_source: str) -> Dict[str, Any]:
    pairs = _scope_pairs(scope)
    strata: Dict[str, Dict[str, Any]] = {}
    for sample in samples:
        key = sample["stratum"]
        row = strata.setdefault(key, {
            "subject": sample["subject"],
            "grade": sample["grade"],
            "grade_label": sample["grade_label"],
            "in_product_scope": sample["in_product_scope"],
            "unique_pages": 0,
            "split_counts": {"development": 0, "holdout": 0},
        })
        row["unique_pages"] += 1
        row["split_counts"][sample["split"]] += 1

    by_scope = []
    for grade in sorted(scope):
        for subject, publisher in sorted(scope[grade].items()):
            canonical = _canonical_subject(subject)
            key = _stratum(canonical, grade)
            row = strata.get(key)
            by_scope.append({
                "grade": grade,
                "grade_label": _grade_label(grade),
                "subject": canonical,
                "publisher": publisher,
                "stratum": key,
                "unique_pages": row["unique_pages"] if row else 0,
                "split_counts": row["split_counts"] if row else {"development": 0, "holdout": 0},
            })

    in_scope = sum(1 for sample in samples if sample["in_product_scope"])
    return {
        "source_scope": scope_source,
        "approved_scope_pair_count": len(pairs),
        "total_image_files": total_files,
        "unique_pages": len(samples),
        "duplicate_files_excluded": total_files - len(samples),
        # Compatibility alias: excluded from the inventory, never deleted.
        "duplicate_files_removed": total_files - len(samples),
        "duplicate_files_note": "仅在清单中按 SHA256 合并重复文件，未物理删除、移动或复制任何图片；duplicate_files_removed 为兼容旧版保留的别名。",
        "in_scope_unique_pages": in_scope,
        "out_of_scope_or_unresolved_unique_pages": len(samples) - in_scope,
        "strata": [strata[key] | {"stratum": key} for key in sorted(strata)],
        "approved_scope_coverage": by_scope,
    }


def build_manifest(root: Path, *, seed: str = "xueban-commercial-v1",
                   holdout_ratio: float = 0.20,
                   repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Scan ``root`` and return a deterministic manifest without copying files."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("image root is not a directory: %s" % root)
    if not 0.0 <= holdout_ratio <= 1.0:
        raise ValueError("holdout_ratio must be between 0 and 1")
    if repo_root is None:
        candidate = Path(__file__).resolve().parents[3]
        repo_root = candidate if (candidate / "curriculum" / "selection.json").is_file() else None
    scope, scope_source = _scope_from_selection(repo_root)
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    files = _candidate_image_files(root)
    for path in files:
        sha256, byte_size = _file_hash(path)
        relative = _relative(path, root)
        subject = _infer_subject(Path(relative))
        grade = _infer_grade(Path(relative))
        grouped[sha256].append({
            "asset_ref": relative,
            "byte_size": byte_size,
            "subject": subject,
            "grade": grade,
        })

    groups: List[Dict[str, Any]] = []
    for sha256 in sorted(grouped):
        occurrences = sorted(grouped[sha256], key=lambda row: row["asset_ref"])
        canonical = occurrences[0]
        subject = canonical["subject"]
        grade = canonical["grade"]
        stratum = _stratum(subject, grade)
        pair_in_scope = (grade, subject) in _scope_pairs(scope)
        groups.append({
            "sha256": sha256,
            "occurrences": occurrences,
            "canonical": canonical,
            "subject": subject,
            "grade": grade,
            "stratum": stratum,
            "in_product_scope": pair_in_scope,
        })

    assignments = _assign_splits(groups, seed, holdout_ratio)
    samples: List[Dict[str, Any]] = []
    for group in groups:
        sha256 = str(group["sha256"])
        occurrences = group["occurrences"]
        canonical = group["canonical"]
        samples.append({
            "sample_id": "page-" + sha256[:16],
            "sha256": sha256,
            "byte_size": canonical["byte_size"],
            "asset_ref": canonical["asset_ref"],
            "duplicate_paths": [row["asset_ref"] for row in occurrences[1:]],
            "duplicate_count": len(occurrences),
            "subject": group["subject"],
            "grade": group["grade"],
            "grade_label": _grade_label(group["grade"]),
            "stratum": group["stratum"],
            "in_product_scope": group["in_product_scope"],
            "split": assignments[sha256],
            "gold": {"verified": False, "verdict": None},
            "metadata_conflict": len({(row["subject"], row["grade"]) for row in occurrences}) > 1,
        })

    return {
        "schema_version": 1,
        "dataset_id": "xueban-commercial-homework-photos",
        "manifest_kind": "offline_image_inventory",
        "source_root": str(root),
        "split_policy": {
            "seed": seed,
            "holdout_ratio": holdout_ratio,
            "group_key": "sha256",
            "stratification": "subject/grade",
            "splits": ["development", "holdout"],
            "holdout_independence": "not_established",
            "holdout_notice": "已用于历史调试或测试的照片，即使分到 holdout，也不是独立、未触碰的验收集。本次分层仅用于离线组织，不证明样本未被使用。",
        },
        "model_calls": 0,
        "samples": samples,
        "coverage": _coverage(samples, len(files), scope, scope_source),
        "evaluation_policy": {
            "gold_required": "verified=true",
            "unverified_labels_excluded": True,
            "unlabeled_accuracy": None,
            "wrong_as_correct_rate": "gold_verdict=incorrect and prediction=correct, verified gold only",
        },
    }


def _rows(payload: Any, key: str) -> List[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, Mapping):
        value = payload.get(key, payload.get("rows", []))
        if isinstance(value, list):
            return [row for row in value if isinstance(row, Mapping)]
    raise ValueError("%s JSON must be a list or an object containing %s" % (key, key))


def _sample_key(row: Mapping[str, Any]) -> Optional[str]:
    value = row.get("sample_id") or row.get("sha256")
    return str(value) if value else None


def apply_gold(manifest: Mapping[str, Any], labels: Any) -> Dict[str, Any]:
    """Return a copy of ``manifest`` with optional human gold annotations."""
    output = json.loads(json.dumps(manifest, ensure_ascii=False))
    by_key = {sample["sample_id"]: sample for sample in output["samples"]}
    by_key.update({sample["sha256"]: sample for sample in output["samples"]})
    for row in _rows(labels, "labels"):
        key = _sample_key(row)
        if key not in by_key:
            raise ValueError("gold label references an unknown sample: %s" % key)
        sample = by_key[key]
        verified = row.get("verified") is True
        verdict = row.get("gold_verdict", row.get("verdict"))
        if verified and verdict not in {"correct", "incorrect"}:
            raise ValueError("verified gold verdict must be correct or incorrect")
        sample["gold"] = {"verified": verified, "verdict": verdict if verified else None}
    return output


def evaluate_predictions(manifest: Mapping[str, Any], predictions: Any,
                         *, split: str = "holdout") -> Dict[str, Any]:
    """Evaluate only verified gold; return null rates when no gold is eligible."""
    if split not in {"development", "holdout"}:
        raise ValueError("split must be development or holdout")
    sample_by_key = {sample["sample_id"]: sample for sample in manifest["samples"]}
    sample_by_key.update({sample["sha256"]: sample for sample in manifest["samples"]})
    seen = set()
    eligible = []
    excluded_unverified = 0
    for row in _rows(predictions, "predictions"):
        key = _sample_key(row)
        if key not in sample_by_key:
            raise ValueError("prediction references an unknown sample: %s" % key)
        sample = sample_by_key[key]
        if sample["split"] != split:
            continue
        if sample["sample_id"] in seen:
            raise ValueError("duplicate prediction for sample: %s" % sample["sample_id"])
        seen.add(sample["sample_id"])
        gold = sample.get("gold") or {}
        if gold.get("verified") is not True or gold.get("verdict") not in {"correct", "incorrect"}:
            excluded_unverified += 1
            continue
        prediction = row.get("verdict")
        if prediction not in {"correct", "incorrect", "partially_correct", "unable_to_judge"}:
            raise ValueError("prediction verdict is invalid: %s" % prediction)
        eligible.append((gold["verdict"], prediction))

    verified_total = len(eligible)
    exact = sum(gold == prediction for gold, prediction in eligible)
    wrong_gold = sum(gold == "incorrect" for gold, _ in eligible)
    false_passes = sum(gold == "incorrect" and prediction == "correct"
                       for gold, prediction in eligible)
    predictions_in_split = sum(1 for sample in manifest["samples"] if sample["split"] == split)
    return {
        "split": split,
        "predictions_in_split": predictions_in_split,
        "predictions_received_in_split": len(seen),
        "verified_gold_evaluated": verified_total,
        "unverified_or_unlabeled_excluded": excluded_unverified,
        "accuracy": (exact / verified_total) if verified_total else None,
        "accuracy_status": ("reported_from_verified_gold" if verified_total
                             else "not_available_no_verified_gold"),
        "wrong_as_correct_count": false_passes,
        "wrong_as_correct_denominator": wrong_gold,
        "wrong_as_correct_rate": (false_passes / wrong_gold) if wrong_gold else None,
        "wrong_as_correct_status": ("reported_from_verified_gold" if wrong_gold
                                    else "not_available_no_verified_wrong_gold"),
        "note": "未标注或 verified!=true 的样本不参与任何准确率或误放行率计算。",
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(value: Mapping[str, Any], output: str) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    if output == "-":
        sys.stdout.write(text)
        return
    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="root directory containing homework photos")
    parser.add_argument("--output", default="-", help="manifest JSON path, or - for stdout")
    parser.add_argument("--gold", type=Path, help="optional human label JSON")
    parser.add_argument("--predictions", type=Path, help="optional offline prediction JSON")
    parser.add_argument("--eval-split", choices=("development", "holdout"), default="holdout")
    parser.add_argument("--seed", default="xueban-commercial-v1")
    parser.add_argument("--holdout-ratio", type=float, default=0.20)
    args = parser.parse_args(argv)

    manifest = build_manifest(args.root, seed=args.seed, holdout_ratio=args.holdout_ratio)
    if args.gold:
        manifest = apply_gold(manifest, _read_json(args.gold))
    if args.predictions:
        manifest["evaluation"] = evaluate_predictions(
            manifest, _read_json(args.predictions), split=args.eval_split)
    _write_json(manifest, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
