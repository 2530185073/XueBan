"""Bounded arithmetic on explicit positions in an already-bound observation.

Not an image reader, identity check, or authority to release a pending result.
Only a proved wrong slot produces an override. Sources and student text remain
unchanged; unsupported shapes and wholly correct calculations return None.
"""
import ast
from fractions import Fraction
import hashlib
import json
import re

PROTOCOL = "numeric-position-arithmetic.v1"
_MAX_POSITIONS = 8
_ORDINALS = {str(n): n for n in range(1, _MAX_POSITIONS + 1)} | {
    char: n for n, char in enumerate("一二三四五六七八", 1)}
_HEADING = re.compile(r"\s*(?:计算下列各式|计算下列各题|求下列各式的值|计算|求值)\s*[:：]")
_BLANK = re.compile(r"[_＿]{2,}")
_ENTRY = re.compile(r"\s*第\s*([一二三四五六七八0-9０-９]+)\s*空\s*[:：]\s*(\S(?:[^\n]*?\S)?)\s*\Z")
_SCALAR = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:/[+-]?(?:\d+(?:\.\d+)?|\.\d+))?\Z")


def _normal(value):
    # Compatibility normalization would change powers (2² -> 22) and mixed
    # number glyphs. Only map the supported full-width ASCII characters.
    return value.translate({**{n: n - 0xFEE0 for n in range(0xFF01, 0xFF5F)},
                            ord("−"): ord("-"), ord("　"): ord(" ")})


def _outer_parentheses(text):
    if not text.startswith("(") or not text.endswith(")"):
        return False
    depth = 0
    for index, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if depth < 0 or (depth == 0 and index != len(text) - 1):
            return False
    return depth == 0


def _bounded(value):
    if value.numerator.bit_length() > 256 or value.denominator.bit_length() > 256:
        raise ValueError()
    return value


def _number(expression):
    """Exact +-*/ constants only; no calls, powers, units, or implicit product.

    Shares the Fraction/AST approach of correction_pipeline._fraction_expression,
    but deliberately does not reuse its unit stripping or ambiguous divisor
    fraction interpretation. Every AST operation and intermediate is bounded.
    """
    text = _normal(expression).strip()
    if (len(text) > 128 or not re.fullmatch(r"[0-9.()+\-*/×÷\s]+", text)
            or re.search(r"[0-9.]\s+[0-9.]", text)
            or re.search(r"\d{13}|\.\d{7}|\*\*|//", text)
            or re.search(r"÷\s*[+-]?\d+(?:\.\d+)?\s*/", text)):
        return None
    text = text.replace("×", "*").replace("÷", "/")
    try:
        tree = ast.parse(text, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 64:
            return None
        def visit(node, depth=0):
            if depth > 12:
                raise ValueError()
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                # Read the original literal, avoiding float-roundtrip rounding.
                literal = ast.get_source_segment(text, node)
                if not literal or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", literal):
                    raise ValueError()
                return _bounded(Fraction(literal))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                value = visit(node.operand, depth + 1)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
                if isinstance(node.op, ast.Add): return _bounded(left + right)
                if isinstance(node.op, ast.Sub): return _bounded(left - right)
                if isinstance(node.op, ast.Mult): return _bounded(left * right)
                return _bounded(left / right)
            raise ValueError()
        return visit(tree.body)
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError, RecursionError):
        return None


def _reference(field, text, start, end):
    return {"field": field, "start": start, "end": end, "text": text[start:end]}


def _answers(text):
    if not isinstance(text, str) or len(text) > 1200:
        return None
    end = len(text.rstrip())
    if end and text[end - 1] == "。":
        end -= 1
    answers = {}
    for segment in re.finditer(r"[^;；\n]+", text[:end]):
        if not segment[0].strip():
            continue
        match = _ENTRY.fullmatch(segment[0])
        if match is None:
            return None
        ordinal = _ORDINALS.get(_normal(match[1]))
        if ordinal is None or ordinal in answers or len(match[2]) > 64:
            return None
        start, finish = segment.start() + match.start(2), segment.start() + match.end(2)
        answers[ordinal] = _reference("answer_text", text, start, finish)
    return answers


def _segments(stem):
    if not isinstance(stem, str) or len(stem) > 1600:
        return None
    start, end = 0, len(stem.rstrip())
    if end and stem[end - 1] == "。":
        end -= 1
    heading = _HEADING.match(stem)
    if heading:
        start = heading.end()
    cuts = [start, *[match.end() for match in re.finditer(r"[=＝]", stem[start:end])]]
    # re.finditer on a slice uses slice-relative offsets.
    cuts = [start, *[start + value for value in cuts[1:]]]
    ends = [cut - 1 for cut in cuts[1:]] + [end]
    pieces = [(left, right, stem[left:right]) for left, right in zip(cuts, ends)]
    if not 3 <= len(pieces) <= _MAX_POSITIONS + 1:
        return None
    return pieces


def check_position_arithmetic(source, *, source_id):
    """Return a proved error plus references, never a new reading or ready flag."""
    if (not isinstance(source, dict) or source.get("answer_status") != "present"
            or source.get("uncertainties") or source.get("options")
            or source.get("target_unit", "answer_slot") != "answer_slot"
            or re.search(r"#[1-9][0-9]*$", _normal(str(source.get("label", ""))))
            or not isinstance(source_id, str)
            or not re.fullmatch(r"(?:primary|secondary|visual):[0-9a-f]{64}", source_id)):
        return None
    stem, text = source.get("stem_text"), source.get("answer_text")
    pieces, answers = _segments(stem), _answers(text)
    if pieces is None or answers is None or set(answers) != set(range(1, len(pieces))):
        return None
    expected = _number(pieces[0][2])
    if expected is None:
        return None
    checks, positions = [], []
    for index, (start, end, printed) in enumerate(pieces[1:], 1):
        placeholders = list(_BLANK.finditer(printed))
        if len(placeholders) != 1:
            return None
        blank = placeholders[0]
        before, after = printed[:blank.start()], printed[blank.end():]
        if before.strip():
            return None
        raw_answer = answers[index]["text"]
        if re.search(r"[0-9.]\s+[0-9.]", _normal(raw_answer)):
            return None
        answer = re.sub(r"\s+", "", _normal(raw_answer))
        suffix = _normal(after).strip()
        if not suffix:
            scalar = answer[1:-1] if answer.startswith("(") and answer.endswith(")") else answer
            if not _SCALAR.fullmatch(scalar):
                return None
            actual = _number(answer)
            correction = str(expected)
        else:
            # A sign is a sign, not an invented coefficient -1. Require the
            # full remaining printed segment to be one parenthesized constant.
            if answer not in {"+", "-"} or not _outer_parentheses(suffix):
                return None
            inner = _number(suffix)
            if inner is None:
                return None
            actual = inner if answer == "+" else -inner
            possible = [sign for sign, value in (("+", inner), ("-", -inner)) if value == expected]
            if not possible:
                return None  # Printed chain would need more than a sign repair.
            correction = possible[0]
        if actual is None:
            return None
        correct = actual == expected
        explanation = (f"第{index}空代入本等号位置后为{actual}，与左端{expected}一致。" if correct else
                       f"第{index}空代入本等号位置后为{actual}，左端为{expected}；应填{correction}，不能借用其他空的符号。")
        checks.append({"index": index, "prompt": printed.strip(), "student_answer": raw_answer,
                       "verdict": "correct" if correct else "incorrect",
                       "correct_answer": None if correct else correction, "explanation": explanation})
        positions.append({"index": index,
            "printed_segment": _reference("stem_text", stem, start, end),
            "placeholder": _reference("stem_text", stem, start + blank.start(), start + blank.end()),
            "student_answer": answers[index], "substituted_value": str(actual),
            "expected_value": str(expected), "verdict": checks[-1]["verdict"]})
    wrong = [check for check in checks if check["verdict"] == "incorrect"]
    if not wrong:
        return None  # Never turn a previous incorrect/unknown judgment correct.
    correct_answer = "；".join(f'第{c["index"]}空：{c["correct_answer"]}' for c in wrong)
    method = "；".join(c["explanation"] for c in wrong)
    summary = "逐空代入原印刷等式核验：" + "；".join(c["explanation"] for c in checks)
    if (len(correct_answer) > 1200 or len(method) > 1000 or len(summary) > 2000
            or any(len(c["prompt"]) > 600 or len(c["explanation"]) > 600 for c in checks)):
        return None
    observed = {key: source.get(key) for key in ("stem_text", "answer_text", "answer_status")}
    return {"verdict": "partially_correct" if len(wrong) < len(checks) else "incorrect",
            "judgment_reason_code": "other", "answer_checks": checks,
            "correct_answer": correct_answer, "correction_method": method, "basis_summary": summary,
            "audit": {"protocol": PROTOCOL, "source_id": source_id,
                "source_text_sha256": hashlib.sha256(json.dumps(observed, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "printed_left": _reference("stem_text", stem, pieces[0][0], pieces[0][1]),
                "positions": positions, "observation_rewritten": False,
                "authority": "bounded_arithmetic_after_existing_source_and_visual_gates"}}
