"""Negative-only check of a model's internally contradictory numeric fields.

This is not OCR or a truth judgment about the student's answer. Only complete
bounded numeric fields/explicit chains are consumed; unsupported writing is a
no-op. A separate existing AI review can replace the judgment, never the ink.
"""
import ast
import re
import hashlib
import json
from .position_arithmetic import _normal, _number, _reference

PROTOCOL = "numeric-judgment-consistency.v1"


def _constant_syntax(expression):
    """Only bound the original constant syntax; DO NOT evaluate ambiguous ÷.

    Unlike _number, this can recognize an entire numeric chain segment whose
    printed division scope is ambiguous. No numerical value is assigned to it.
    The final segment is separately required to pass unchanged _number.
    """
    text = _normal(expression).strip()
    if (len(text) > 128 or not re.fullmatch(r"[0-9.()+\-*/×÷\s]+", text)
            or re.search(r"[0-9.]\s+[0-9.]", text)
            or re.search(r"\d{13}|\.\d{7}|\*\*|//", text)):
        return False
    text = text.replace("×", "*").replace("÷", "/")
    nesting = 0
    for char in text:
        nesting += (char == "(") - (char == ")")
        if nesting < 0 or nesting > 12:
            return False
    if nesting:
        return False
    try:
        tree = ast.parse(text, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 64:
            return False
        def valid(node, depth=0):
            if depth > 12:
                return False
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                literal = ast.get_source_segment(text, node)
                return bool(literal and re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", literal))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                return valid(node.operand, depth + 1)
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                return valid(node.left, depth + 1) and valid(node.right, depth + 1)
            return False
        return valid(tree.body)
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return False


def _explicit_chain_tail(text):
    """Prove which expression is the last of one complete explicit chain.

    Each whole segment must have constant-only syntax. A newline alone cannot
    connect two calculations, and unsupported text anywhere rejects the field.
    """
    left, right = len(text) - len(text.lstrip()), len(text.rstrip())
    if left >= right:
        return None
    steps, offset = [], left
    for index, line in enumerate(text[left:right].splitlines(keepends=True)):
        if not line.strip() or index and not line.lstrip().startswith(("=", "＝")):
            return None
        separators = list(re.finditer(r"[=＝]", line))
        starts = [0, *[mark.end() for mark in separators]]
        ends = [*[mark.start() for mark in separators], len(line)]
        for part, (start, end) in enumerate(zip(starts, ends)):
            raw = line[start:end]
            if part == 0 and not raw.strip() and separators:
                continue
            start += len(raw) - len(raw.lstrip())
            end -= len(raw) - len(raw.rstrip())
            if not _constant_syntax(line[start:end]):
                return None
            mark = separators[part - 1] if part else None
            steps.append({"expression": _reference("answer_text", text, offset + start, offset + end),
                "equals": _reference("answer_text", text, offset + mark.start(), offset + mark.end()) if mark else None})
            if len(steps) > 8:
                return None
        offset += len(line)
    if len(steps) < 2 or not any(step["equals"] for step in steps):
        return None
    final = _number(steps[-1]["expression"]["text"])
    return (steps, final) if final is not None else None


def numeric_verdict_conflict(source):
    """Return evidence only when this `correct` claim contradicts its own value."""
    if (not isinstance(source, dict) or source.get("verdict") != "correct"
            or source.get("answer_status") != "present" or source.get("uncertainties")
            or source.get("options") or source.get("subject") != "数学"
            or source.get("question_type") not in {"calculation", "fill_blank", "application"}
            or source.get("target_unit", "answer_slot") != "answer_slot"):
        return None
    answer, expected = source.get("answer_text"), source.get("correct_answer")
    if not isinstance(answer, str) or not isinstance(expected, str) or len(answer) > 1200:
        return None
    expected_value = _number(expected)
    if expected_value is None:
        return None
    value = _number(answer)
    if value is not None:
        left, right = len(answer) - len(answer.lstrip()), len(answer.rstrip())
        final_reference = _reference("answer_text", answer, left, right)
        shape, steps = "complete_numeric_expression", []
    else:
        parsed = _explicit_chain_tail(answer)
        if parsed is None:
            return None
        steps, value = parsed
        final_reference = steps[-1]["expression"]
        shape = "complete_explicit_equality_chain"
    if value == expected_value:
        return None  # Fraction equality, not spelling equality; no new pending.
    observed = {key: source.get(key) for key in ("answer_text", "answer_status", "verdict", "correct_answer")}
    return {"protocol": PROTOCOL, "authority": "model_field_consistency_only_not_student_truth",
            "original_verdict": source["verdict"], "original_correct_answer": expected,
            "judgment_fields_sha256": hashlib.sha256(json.dumps(observed, ensure_ascii=False,
                allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "answer_shape": shape, "final_expression": final_reference, "explicit_steps": steps,
            "intermediate_values_evaluated": False, "intermediate_equalities_validated": False,
            "observed_final_value": str(value), "declared_correct_value": str(expected_value),
            "observation_rewritten": False, "student_verdict_assigned": False}
