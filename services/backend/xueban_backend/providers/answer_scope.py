"""A narrow negative guard for a single transcript covering a multi-blank chain.

This never repairs text, counts unseen blanks, or verifies arithmetic. It only
recognizes an explicit numeric equation-chain shape already present in a source.
The current legacy contract cannot attest complete slot coverage, so requests whose outputs cannot resolve this gap are not dispatched. All sources
remain immutable. Structured scope recovery is deliberately a separate future change.
"""
import re
import unicodedata

REASON = "target_scope_unverified"
SUMMARY = "本题的作答范围尚未完整核对，原图和已有笔迹已保留，待AI继续核对。"
PROTOCOL = "numeric-chain-scope.v1"



def _text(value):
    return unicodedata.normalize("NFKC", value).replace("−", "-") if isinstance(value, str) else ""


def candidate_positions(item):
    """Return an observed boundary count only for the deliberately narrow shape."""
    if not isinstance(item, dict) or item.get("target_unit", "answer_slot") != "answer_slot":
        return None
    if re.search(r"#[1-9][0-9]*$", _text(item.get("label")).strip()):
        return None
    stem = _text(item.get("stem_text")).strip().rstrip("。")
    if not stem or not re.fullmatch(r"[0-9._()+\-*/×÷=^\s]+", stem) or stem.count("=") < 2:
        return None
    parts = stem.split("=")
    if any(not part.strip() for part in parts) or "_" in parts[0] or not re.search(r"[0-9]", parts[0]):
        return None
    balance = 0
    for char in stem:
        balance += (char == "(") - (char == ")")
        if balance < 0:
            return None
    if balance:
        return None
    # An underline broken only by whitespace is still one visible placeholder.
    count = len(re.findall(r"_{2,}(?:\s+_{2,})*", stem))
    return count if 2 <= count <= 32 else None


def scope_gap(item):
    """A single unlocated numeric answer cannot account for this known shape."""
    if candidate_positions(item) is None or item.get("answer_status") != "present":
        return False
    answer = _text(item.get("answer_text")).strip()
    number = r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
    scalar = rf"{number}(?:\s*/\s*{number})?"
    return bool(re.fullmatch(rf"(?:{scalar}|\(\s*{scalar}\s*\))", answer))


def review_blocked(primary, secondary, selected=None):
    """Ordinary equivalence cannot restore a known missing response scope.

    Do not turn more verbose prose or newly formatted labels into independent
    visual coverage. The existing contract lacks that authority.
    """
    return any(scope_gap(item) for item in (primary, secondary, selected))
