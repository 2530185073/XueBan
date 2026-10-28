"""Answer-free blank contracts and lossless normalization for variant generation.

These checks describe text already present; they never repair OCR, split a
student's answer, invent a missing field or decide between conflicting answers.
"""

import re


MAX_BLANKS = 12  # Matches the practice submission API's field limit.
_SPACE = r"(?:\s|\\(?:qquad|quad|enspace)|\\[,;! ])*"
_BLANK = re.compile(
    rf"\\(?:underline|boxed)\{{{_SPACE}[_＿]*{_SPACE}\}}"
    rf"|[（(]{_SPACE}[_＿]*{_SPACE}[)）]|[_＿]{{2,}}"
)


def visible_blank_count(stem):
    """Count explicit empty slots, not subquestion numbers or signed operands."""
    return len(_BLANK.findall(stem))


def source_blank_contract(source):
    """Reconcile only a demonstrable legacy undercount, with explicit audit.

    Legacy prose may have no explicit markers, so a valid declared count can
    still be used. More explicit markers prove a stored undercount; fewer do
    not prove an overcount (some slots may be unmarked). Examples are ambiguous.
    No source document or fingerprint is changed by this execution-time check.
    """
    declared = source.get("blank_count")
    visible = visible_blank_count(source["stem_text"])
    expected = declared
    errors, normalizations = [], []
    if visible and visible != declared and re.search(
            r"例如|示例|例题|仿照|照例|(?:^|[\s，,。；;：:])例[：:\d]", source["stem_text"]):
        errors.append({"code": "source_blank_structure_ambiguous", "path": "source_question.stem_text"})
    elif declared is not None and (type(declared) is not int or not 1 <= declared <= MAX_BLANKS):
        errors.append({"code": "source_blank_count_invalid", "path": "source_question.blank_count",
                       "minimum": 1, "maximum": MAX_BLANKS})
    elif declared is not None and declared < visible <= MAX_BLANKS:
        expected = visible
        normalizations.append({"code": "source_blank_count_underreported", "declared": declared, "visible": visible})
    elif declared is not None and visible and visible != declared:
        errors.append({"code": "source_blank_count_mismatch", "path": "source_question.blank_count",
                       "declared": declared, "visible": visible})
    elif declared is None and not 1 <= visible <= MAX_BLANKS:
        errors.append({"code": "source_blank_count_unknown", "path": "source_question.blank_count",
                       "visible": visible, "maximum": MAX_BLANKS})
    elif declared is None:
        expected = visible
        normalizations.append({"code": "source_blank_count_from_visible_markers", "count": visible})
    return {"expected_blank_count": None if errors else expected,
            "declared_blank_count": declared, "visible_blank_count": visible,
            "errors": errors, "normalizations": normalizations}


def normalize_blank_answers(answer, fields):
    """Remove only an exact redundant top-level copy, retaining the raw output.

    The order comes from answer_fields, never field_answers' array order. Only
    one literal value per field qualifies: no semantic/numeric equivalence,
    alternative-answer flattening, truncation or selection between duplicates.
    """
    ids = [field["id"] for field in fields]
    supplied = [field["field_id"] for field in answer["field_answers"]]
    if (not ids or len(ids) != len(set(ids)) or len(supplied) != len(set(supplied))
            or set(ids) != set(supplied) or not answer["answer_values"]):
        return answer, []
    by_id = {field["field_id"]: field["answer_values"] for field in answer["field_answers"]}
    if (all(len(by_id[key]) == 1 and by_id[key][0].strip() for key in ids)
            and answer["answer_values"] == [by_id[key][0] for key in ids]):
        return {**answer, "answer_values": []}, [{"code": "redundant_top_level_answers_removed"}]
    return answer, []


def blank_answer_issues(answer, fields):
    """Validate the one-to-one mapping without interpreting answer correctness."""
    ids = [field["id"] for field in fields]
    supplied = [field["field_id"] for field in answer["field_answers"]]
    errors = []
    if not ids:
        errors.append({"code": "missing_answer_fields", "path": "answer_fields"})
    if any(not field["id"].strip() or not field["label"].strip() for field in fields):
        errors.append({"code": "empty_field_identifier_or_label", "path": "answer_fields"})
    if len(ids) != len(set(ids)):
        errors.append({"code": "duplicate_field_ids", "path": "answer_fields"})
    if len(supplied) != len(set(supplied)):
        errors.append({"code": "duplicate_field_answers", "path": "field_answers"})
    if set(ids) != set(supplied):
        errors.append({"code": "field_answer_mapping_mismatch", "path": "field_answers",
                       "missing_count": len(set(ids) - set(supplied)),
                       "unexpected_count": len(set(supplied) - set(ids))})
    if answer["answer_values"]:
        errors.append({"code": "fill_blank_top_level_answers", "path": "answer_values", "expected": []})
    if answer["answer_kind"] not in ("text", "number"):
        errors.append({"code": "fill_blank_answer_kind", "path": "answer_kind"})
    if any(not field["answer_values"] or not all(value.strip() for value in field["answer_values"])
           for field in answer["field_answers"]):
        errors.append({"code": "empty_field_answer", "path": "field_answers"})
    if not answer["solution_text"].strip():
        errors.append({"code": "missing_solution", "path": "solution_text"})
    return errors


def blank_candidate_issues(candidate, expected_count):
    fields = candidate["answer_fields"]
    errors = blank_answer_issues(candidate, fields)
    if candidate["options"]:
        errors.append({"code": "fill_blank_options", "path": "options", "expected": []})
    if expected_count is not None and len(fields) != expected_count:
        errors.append({"code": "blank_count_mismatch", "path": "answer_fields",
                       "expected": expected_count, "actual": len(fields)})
    visible = visible_blank_count(candidate["stem_text"])
    if visible != len(fields) or not visible:
        errors.append({"code": "stem_blank_count_mismatch", "path": "stem_text",
                       "expected": len(fields), "visible": visible})
    return errors
