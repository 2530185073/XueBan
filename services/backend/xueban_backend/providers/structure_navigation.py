"""Experimental page navigation, without model calls or judgment authority.

Only labels and the requested unit may cross from the first reader to the
second. These helpers never pair, filter, merge or manufacture observations.
Shared navigation correlates coverage; it is not independent visual evidence.
"""

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re

from . import question_units
from .whole_page import canonical_label


PROTOCOL = "structure-navigation.experimental.v1"
MAX_TARGETS = 80
MAX_LABEL_LENGTH = 120
NAVIGATION_FIELDS = frozenset(("label", "target_unit"))
REPORT_FIELD = "unlocated_navigation_labels"
RULES = (
    "task_input.navigation仅提供可能有误的题号与作答单元导航，不含其他模型的题文、笔迹或判定。"
    "仍须独立通读完整原图，按原图实际内容识读和纠错；导航不是完整题目清单，不是题目身份的证据。"
    "可靠对应时可沿用导航label；#1、#2仅提示同题空位顺序，不是印刷字样。"
    "visible_locator仍独立记录原图可见题号，不照抄导航，也不按导航猜空数、造字、合并或拆分。"
    "找不到或不能可靠对应的导航label列入unlocated_navigation_labels，不制造占位题。"
    "导航之外的可见题目也必须正常返回items，使用原有题号；不能为了列表或数量一致而漏题。"
    "实际需要不同范围或题号时保留实际识读，不强行迎合导航；所有题面、作答与判断仍以原图为准。"
)

# Restrict this experiment to number-like locators. A free-form label could
# itself contain an old question or answer; unsupported labels use the ordinary
# reader when the caller prepares the request, before any provider is invoked.
_NUMBER = r"(?:[0-9]+|[一二三四五六七八九十百]+)"
_LABEL = re.compile(
    rf"(?:{_NUMBER}[A-Za-z]?|\({_NUMBER}\))"
    rf"(?:\.?\((?:式)?{_NUMBER}[A-Za-z]?\)|\.{_NUMBER})*"
    r"(?:#[1-9][0-9]*)?"
)


def _rows(items):
    if isinstance(items, Mapping):
        items = list(items.values())
    if not isinstance(items, (list, tuple)):
        raise ValueError("invalid_navigation_items")
    if not 1 <= len(items) <= MAX_TARGETS:
        raise ValueError("invalid_navigation_count")
    if any(not isinstance(row, Mapping) for row in items):
        raise ValueError("invalid_navigation_item")
    return items


def _label(value):
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_LABEL_LENGTH:
        raise ValueError("unsupported_navigation_label")
    label = canonical_label(value)
    if not _LABEL.fullmatch(label):
        raise ValueError("unsupported_navigation_label")
    return label


def build_navigation(items, target_unit):
    """Return a fresh whitelist projection, or reject the entire navigation.

    Rejection is not a failed image read: the caller can send its one ordinary
    secondary request instead. Never silently omit unsupported primary items.
    """
    question_units.validate_unit(target_unit)
    result, seen = [], set()
    for row in _rows(items):
        if row.get("target_unit", target_unit) != target_unit:
            raise ValueError("navigation_unit_mismatch")
        label = _label(row.get("label"))
        if label in seen:
            raise ValueError("duplicate_navigation_label")
        seen.add(label)
        result.append({"label": label, "target_unit": target_unit})
    return result


def _validated_navigation(navigation):
    rows = _rows(navigation)
    if any(set(row) != NAVIGATION_FIELDS for row in rows):
        raise ValueError("invalid_navigation_fields")
    return build_navigation(rows, rows[0]["target_unit"])


def request_scope(schema, prompt, task_input, navigation):
    """Copy the page request and add a navigation-only optional experiment.

    R1 is deliberately excluded: its one-target scope and retry budget are
    separate from a full-page navigation request.
    """
    if not isinstance(task_input, Mapping):
        raise ValueError("invalid_navigation_task_input")
    if "original_target" in task_input:
        raise ValueError("navigation_not_supported_for_original_recheck")
    if "navigation" in task_input or REPORT_FIELD in schema.get("properties", {}):
        raise ValueError("navigation_already_applied")
    navigation = _validated_navigation(navigation)
    result_schema, result_input = deepcopy(schema), deepcopy(dict(task_input))
    result_schema["properties"][REPORT_FIELD] = {
        "type": "array", "items": {"type": "string", "minLength": 1, "maxLength": MAX_LABEL_LENGTH},
        "maxItems": MAX_TARGETS,
    }
    result_schema["required"] = [*result_schema.get("required", []), REPORT_FIELD]
    result_input["navigation"] = navigation
    return result_schema, prompt + RULES, result_input


def _reported_labels(values):
    """Report unknown labels too; do not make them disappear via a whitelist."""
    if not isinstance(values, (list, tuple)):
        raise ValueError("invalid_navigation_report")
    result = []
    for value in values:
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_LABEL_LENGTH:
            raise ValueError("invalid_navigation_report_label")
        label = canonical_label(value)
        if not label:
            raise ValueError("invalid_navigation_report_label")
        result.append(label)
    return result


def coverage_report(navigation, observed_items, unlocated_navigation_labels):
    """Describe differences only; absence of differences never proves coverage.

    The returned report contains no replacement observations. In particular,
    the caller must retain every extra returned item and every original value.
    """
    navigation = _validated_navigation(navigation)
    if isinstance(observed_items, Mapping):
        observed_items = list(observed_items.values())
    if not isinstance(observed_items, (list, tuple)) or any(
            not isinstance(row, Mapping) for row in observed_items):
        raise ValueError("invalid_navigation_observations")
    returned = _reported_labels([row.get("label") for row in observed_items])
    unlocated = _reported_labels(unlocated_navigation_labels)
    expected_set = {row["label"] for row in navigation}
    returned_set, unlocated_set = set(returned), set(unlocated)
    return {
        "unlocated_navigation_labels": list(dict.fromkeys(unlocated)),
        "unknown_unlocated_navigation_labels": sorted(unlocated_set - expected_set),
        "unlisted_output_labels": sorted(returned_set - expected_set),
        "navigation_labels_without_exact_output": sorted(expected_set - returned_set),
        "unreported_missing_navigation_labels": sorted(expected_set - returned_set - unlocated_set),
        "reported_unlocated_but_returned": sorted(unlocated_set & returned_set),
        "duplicate_output_labels": sorted(label for label, count in Counter(returned).items() if count > 1),
        "duplicate_unlocated_labels": sorted(label for label, count in Counter(unlocated).items() if count > 1),
        "coverage_proven": False,
    }


def provenance(primary_items, navigation):
    """Hash source observations for the private audit, never for model input."""
    rows = _rows(primary_items)
    navigation = _validated_navigation(navigation)
    if navigation != build_navigation(rows, navigation[0]["target_unit"]):
        raise ValueError("navigation_source_mismatch")

    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()

    return {
        "protocol": PROTOCOL,
        "navigation": navigation,
        "navigation_sha256": digest(navigation),
        "primary_items_sha256": digest(list(rows)),
        "structure_shared": True,
        "content_shared": False,
    }
