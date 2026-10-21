"""Bound review evidence, separate from both transcription and correctness.

Free-form source notes are never deleted or interpreted by keywords. A review
may classify them, but every classification must quote an actual source note.
Visual target replacement needs a link to an original observation, not a check
that the selected visual source agrees with itself. These checks reject unsafe
claims; they are not a proof that a model read the photograph correctly.
"""
from copy import deepcopy
import ast
import re

from .correction_pipeline import _object, _text
from . import question_units
from .printed_identity import full_printed_presentation_rule
from .whole_page import normalize_literal_math, normalize_page_stem


ISSUE_KINDS = ("presentation", "answer_semantics", "missing_context",
               "visual_legibility", "target_location", "unknown")
ISSUE_REVIEW_SCHEMA = _object({
    "source_id": _text(100),
    "note_index": {"type": "integer", "minimum": 0, "maximum": 19},
    "note_quote": _text(1000),
    "kind": {"enum": list(ISSUE_KINDS)},
    "resolved": {"type": "boolean"},
    "evidence": _text(800),
})
TARGET_IDENTITY_SCHEMA = _object({
    "original_source_id": _text(100),
    "selected_source_id": _text(100),
    "original_quote": _text(600),
    "selected_quote": _text(600),
    "original_locator": question_units.VISIBLE_LOCATOR_SCHEMA,
    "selected_locator": question_units.VISIBLE_LOCATOR_SCHEMA,
    "image_evidence": _text(800),
})


def review_notes(review, sources, source_id):
    """Validate all supplied classifications and all notes we intend to use.

    All notes must be acknowledged. Only the selected source and independent
    reads must have their reading doubts cleared, so a better source can replace
    an uncertain old candidate without claiming the old note was false.
    Missing context cannot be upgraded to factual correctness by calling it a
    resolved note. Explicit unreadable status/markers are checked elsewhere.
    """
    indexed = {source_id(role, source): (role, source)
               for role, source in sources.items() if source is not None}
    required_roles = {review.get("selected_source"), "visual", "critical_visual"}
    # Every note is acknowledged. Unselected old handwriting doubts can stay
    # unresolved when a clear independently read source replaces them; a noted
    # missing external material must not silently disappear with source choice.
    required = {(identity, index) for identity, (_, source) in indexed.items()
                for index, _ in enumerate(source.get("uncertainties") or [])}
    used = {(identity, index) for identity, (role, source) in indexed.items()
            if role in required_roles for index, _ in enumerate(source.get("uncertainties") or [])}
    rows = review.get("observation_issue_reviews") or []
    audit = []
    def fail(code, cause="model_disagreement"):
        return {"clear": False, "rejection_code": code, "cause": cause, "entries": audit}
    if not isinstance(rows, list):
        return fail("invalid_observation_issue_review")
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            return fail("invalid_observation_issue_review")
        identity, index = item.get("source_id"), item.get("note_index")
        entry = indexed.get(identity)
        if not entry or type(index) is not int:
            return fail("observation_issue_not_bound")
        notes = entry[1].get("uncertainties") or []
        key = (identity, index)
        if not 0 <= index < len(notes) or item.get("note_quote") != notes[index] or key in seen:
            return fail("observation_issue_not_bound")
        if (item.get("kind") not in ISSUE_KINDS or type(item.get("resolved")) is not bool
                or not isinstance(item.get("evidence"), str) or not item["evidence"].strip()):
            return fail("invalid_observation_issue_review")
        seen.add(key)
        audit.append(deepcopy(item))
    if not required <= seen:
        return fail("unreviewed_observation_note")
    relevant = [item for item in audit if (item["source_id"], item["note_index"]) in used]
    if any(item["kind"] == "missing_context" for item in audit):
        return fail("missing_context")
    if any(item["kind"] in {"visual_legibility", "target_location"} for item in relevant):
        return fail("unresolved_visual_note", "visual_unreadable")
    if any(not item["resolved"] or item["kind"] not in {"presentation", "answer_semantics"}
           for item in relevant):
        return fail("unresolved_observation_note")
    return {"clear": True, "entries": audit}


def notes_allow_comparison(result, role, source, source_id):
    """Only classified nonvisual notes can relax a comparison's note gate."""
    notes = source.get("uncertainties") or []
    if not notes:
        return True
    indexed = {(entry["source_id"], entry["note_index"]): entry for entry in result["entries"]}
    identity = source_id(role, source)
    return all((entry := indexed.get((identity, i))) and entry["resolved"]
               and entry["kind"] in {"presentation", "answer_semantics"}
               for i in range(len(notes)))


def _options(item):
    return sorted((o.get("label"), normalize_literal_math(o.get("text")))
                  for o in item.get("options", []))


def _whole_printed_match(a, b):
    return (bool(normalize_page_stem(a)) and normalize_page_stem(a) == normalize_page_stem(b)
            and _options(a) == _options(b) and question_units.same_unit_identity(a, b))


def _calculation_heading_link(original, selected):
    """Bind an unchanged numeric calculation when the new read adds its heading.

    This is directional identity evidence, not condition/answer equivalence:
    the selected observation must retain the newly visible method instruction.
    Only a complete arithmetic expression before a terminal answer position is
    compared. No values, operators, grouping, restrictions or old headings are
    dropped, and the independently observed page area must also agree.
    """
    before, after = normalize_page_stem(original), normalize_page_stem(selected)
    heading = re.fullmatch(r"(计算|列竖式计算|用竖式计算)[:：](.+)", after)
    if not heading or before != heading[2] or not before.endswith("="):
        return None
    expression = before[:-1]
    # Parse for syntax only. Equality above compares the complete written body;
    # never compare evaluated answers or ASTs to establish physical identity.
    if not re.fullmatch(r"[0-9.()+\-*/÷×\s]+", expression):
        return None
    try:
        tree = ast.parse(expression.replace("÷", "/").replace("×", "*"), mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        return None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub)
    nodes = list(ast.walk(tree))
    if (not any(isinstance(node, ast.BinOp) for node in nodes)
            or any(not isinstance(node, allowed) for node in nodes)):
        return None
    a, b = original.get("visible_locator"), selected.get("visible_locator")
    if (not isinstance(a, dict) or not isinstance(b, dict)
            or a.get("page_zone") not in question_units.VISIBLE_ZONES
            or a.get("page_zone") != b.get("page_zone")
            or not question_units.same_unit_identity(original, selected)
            or _options(original) != _options(selected)):
        return None
    if (original.get("source_asset_id") and selected.get("source_asset_id")
            and original["source_asset_id"] != selected["source_asset_id"]):
        return None
    return {"printed_expression": before, "retained_selected_heading": heading[1],
            "original_locator": deepcopy(a), "selected_locator": deepcopy(b)}


def _bound_quote(quote, source):
    stem = source.get("stem_text") or ""
    # A literal source quote, not a rewritten question or student-answer quote.
    # On short stems quote the whole condition; long stems use a unique,
    # substantive excerpt. No calculation or cross-source normalization here.
    return (isinstance(quote, str) and bool(quote.strip()) and quote in stem
            and stem.count(quote) == 1
            and (quote == stem if len(stem) <= 160 else len(quote.strip()) >= 20))


def _locator_link(a, b):
    """Independently recorded visible labels and compatible page areas.

    Requested labels are not substituted for observed printed labels. Page
    zones give coarse physical support, not pixel-level localization. Missing
    or contradictory locator fields do not establish a positive identity.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    zones = [loc.get("page_zone") for loc in (a, b)]
    if (not question_units.complete_printed_labels_match(a.get("printed_label"), b.get("printed_label"))
            or any(z not in question_units.VISIBLE_ZONES for z in zones)):
        return False
    # Adjacent attention windows overlap; exact enum equality would turn
    # harmless boundary placement into another rigid formatting gate.
    bounds = {"top": (0, .42), "middle": (.29, .71), "bottom": (.58, 1)}
    areas = []
    for zone in zones:
        row, _, side = zone.partition("_")
        y0, y1 = bounds[row]
        x0, x1 = (0, .6) if side == "left" else (.4, 1) if side == "right" else (0, 1)
        areas.append((x0, y0, x1, y1))
    a, b = areas
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def target_link(review, primary, secondary, selected, source_id):
    """Return positive original-to-selected evidence or a local rejection.

    An unchanged full printed question is bound to an actual original source.
    A changed printed question/options requires separately archived visible
    locators and a review quoting both sources. Source hashes alone cannot
    establish physical identity. Legacy sources without the new optional
    locator keep their hashes but cannot use this replacement shortcut.
    """
    selected_id = source_id("visual", selected)
    for role, original in (("primary", primary), ("secondary", secondary)):
        if _whole_printed_match(original, selected):
            return {"clear": True, "basis": "full_original_printed_observation",
                    "original_source_id": source_id(role, original), "selected_source_id": selected_id,
                    "original_observation": {"stem_text": original.get("stem_text"), "options": deepcopy(original.get("options", []))},
                    "selected_observation": {"stem_text": selected.get("stem_text"), "options": deepcopy(selected.get("options", []))}}
        presentation_rule = full_printed_presentation_rule(original, selected)
        if presentation_rule:
            return {"clear": True, "basis": "full_original_printed_presentation",
                    "presentation_rule": presentation_rule,
                    "original_source_id": source_id(role, original), "selected_source_id": selected_id,
                    "original_observation": {"stem_text": original.get("stem_text"), "options": deepcopy(original.get("options", []))},
                    "selected_observation": {"stem_text": selected.get("stem_text"), "options": deepcopy(selected.get("options", []))}}
        calculation = _calculation_heading_link(original, selected)
        if calculation:
            return {"clear": True, "basis": "full_original_calculation_with_retained_heading",
                    "original_source_id": source_id(role, original), "selected_source_id": selected_id,
                    "original_observation": {"stem_text": original.get("stem_text"), "options": deepcopy(original.get("options", []))},
                    "selected_observation": {"stem_text": selected.get("stem_text"), "options": deepcopy(selected.get("options", []))},
                    **calculation}
    identity = review.get("target_identity")
    def fail(code):
        return {"clear": False, "rejection_code": code}
    if not isinstance(identity, dict):
        return fail("target_identity_unconfirmed")
    originals = {source_id(role, item): item for role, item in (("primary", primary), ("secondary", secondary))}
    original = originals.get(identity.get("original_source_id"))
    if original is None or identity.get("selected_source_id") != selected_id:
        return fail("target_identity_source_mismatch")
    if (not _bound_quote(identity.get("original_quote"), original)
            or not _bound_quote(identity.get("selected_quote"), selected)):
        return fail("target_identity_quote_not_bound")
    a, b = original.get("visible_locator"), selected.get("visible_locator")
    if identity.get("original_locator") != a or identity.get("selected_locator") != b:
        return fail("target_identity_locator_not_bound")
    if not _locator_link(a, b):
        return fail("target_identity_locator_unconfirmed")
    if not question_units.same_unit_identity(original, selected):
        return fail("target_identity_scope_conflict")
    if not isinstance(identity.get("image_evidence"), str) or not identity["image_evidence"].strip():
        return fail("target_identity_evidence_missing")
    return {"clear": True, "basis": "bound_visible_locator_review", **deepcopy(identity)}
