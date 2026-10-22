"""Narrow full-print identity relations for an already archived blind source.

These compare only complete printed observations. They do not read handwriting,
solve expressions, establish model independence, or rewrite source hashes.
"""
import re
import unicodedata

from . import question_units
from .whole_page import canonical_label, normalize_literal_math, normalize_page_stem


def _boundary(a, b):
    labels = [item.get("label") for item in (a, b)]
    return (all(isinstance(label, str) and label.strip() for label in labels)
            and canonical_label(labels[0]) == canonical_label(labels[1])
            and all(question_units.source_unit(item) == question_units.LEGACY_UNIT for item in (a, b))
            and question_units.same_unit_identity(a, b)
            and not (a.get("source_asset_id") and b.get("source_asset_id")
                     and a["source_asset_id"] != b["source_asset_id"]))


def _ordered_options(item):
    values = item.get("options")
    if not isinstance(values, list) or not 2 <= len(values) <= 12:
        return None
    result = []
    for value in values:
        if (not isinstance(value, dict) or not isinstance(value.get("label"), str)
                or re.fullmatch(r"[A-Z]", value["label"]) is None
                or not isinstance(value.get("text"), str) or not value["text"].strip()):
            return None
        result.append((value["label"], normalize_literal_math(value["text"])))
    if len({label for label, _ in result}) != len(result):
        return None
    return tuple(result)


def _choice_stem(item):
    text = item.get("stem_text")
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"(.+?)(_(?:[ \t]*_)+|\([ \t\u3000]*\)|（[ \t\u3000]*）)[ \t]*[。.\uFF0E]?[ \t]*", text)
    if match is None:
        return None
    head = match[1].rstrip()
    # A unique terminal choice slot, never an omitted operand, internal blank,
    # function call, or multiple independently requested answer positions.
    if (not re.search(r"[\u4e00-\u9fff]$", head)
            or "_" in head or re.search(r"[（(]\s*[）)]", head)):
        return None
    return normalize_page_stem({**item, "stem_text": head}) or None


def _choice_same(original, selected):
    if (original.get("question_type") != "single_choice"
            or selected.get("question_type") not in (None, "single_choice")):
        return False
    a, b = _choice_stem(original), _choice_stem(selected)
    options = _ordered_options(original)
    return bool(a and a == b and options is not None and options == _ordered_options(selected))


_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ"
_TRANSLATE = str.maketrans(_SUPERSCRIPTS, "0123456789+-n")
# Only this explicit linear exponent vocabulary is in scope. Text is compared
# verbatim after notation conversion; n+n is not simplified to 2n.
_EXPONENT = r"[+-]?(?:[0-9]+n|n|[0-9]+)(?:[+-](?:[0-9]+n|n|[0-9]+))*"


def _power_stem(item):
    text = item.get("stem_text")
    if not isinstance(text, str):
        return None
    # Unsupported raised/subscript symbols cannot be left outside a partly
    # converted run (e.g. ²ⁿᵐ must never become ^(2n)ᵐ).
    if any(char not in _SUPERSCRIPTS and
           (unicodedata.category(char) == "Lm" or
            unicodedata.name(char, "").startswith(("SUPERSCRIPT", "SUBSCRIPT")))
           for char in text):
        return None
    superscript_count = 0
    def expand(match):
        nonlocal superscript_count
        exponent = match[2].translate(_TRANSLATE)
        if re.fullmatch(_EXPONENT, exponent) is None:
            return match[0]
        superscript_count += 1
        return match[1] + "^(" + exponent + ")"
    # Read the complete superscript run before the legacy digit-only normalizer.
    # A superscript n stays inside the exponent; it is never flattened by NFKC.
    text = re.sub(r"([A-Za-z0-9)\]])([" + _SUPERSCRIPTS + r"]+)", expand, text)
    if any(char in text for char in _SUPERSCRIPTS):
        return None
    value = normalize_page_stem({**item, "stem_text": text})
    # A single fully delimited power. Bare ASCII ^2n, ^2*n and ^2 are not
    # inferred or repaired. Parentheses around the base remain in the string.
    powers = list(re.finditer(r"\^\((" + _EXPONENT + r")\)", value))
    if value.count("^") != 1 or len(powers) != 1:
        return None
    return value, superscript_count


def _power_same(original, selected):
    if (original.get("question_type") != "fill_blank"
            or selected.get("question_type") not in (None, "fill_blank")
            or original.get("options") != [] or selected.get("options") != []):
        return False
    a, b = _power_stem(original), _power_stem(selected)
    return bool(a and b and a[0] == b[0] and a[1] + b[1] >= 1)


def full_printed_presentation_rule(original, selected):
    """Return a fixed audit rule only for an entire same printed observation.

    Caller still requires the actual bound review and every existing note,
    source-id, answer, judgment, independent-read and scope gate.
    """
    if not _boundary(original, selected):
        return None
    if _choice_same(original, selected):
        return "single_choice_terminal_slot_v1"
    if _power_same(original, selected):
        return "explicit_superscript_scope_v1"
    return None
