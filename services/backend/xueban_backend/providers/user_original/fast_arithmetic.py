"""A deliberately small, fail-closed arithmetic candidate verifier.

Only a whole numeric expression, optionally followed by ``=`` and one terminal
blank, is a question. No substring extraction, word problems, units, implicit
multiplication, or LaTeX are supported. Multiple blanks are supported only as
a complete list of independent expressions with exactly bound source slots.
Student work may be an
equality chain (including ``=`` continuation lines), or complete numeric
equalities on separate lines. Every expression and equality is checked.

``verified`` means agreement with a supplied correct/incorrect candidate; it
does NOT certify that the transcript matches the source image. Callers must
retain independent source-reading, uncertainty, and publication gates. An
incorrect (also spelled ``wrong``) candidate additionally requires a fully
verified model ``correct_answer``. Partial correctness is supported only as
the aggregate of independently correct and incorrect slots, never as a rubric
for partial credit within a calculation.

The only output answer comes from the stem. ``calculation`` is the normalized
stem followed by that exact answer, suitable for a pipeline correction. This
module performs no I/O, imports no providers, and never mutates its inputs.
"""

from fractions import Fraction
import re


_MAX_TEXT = 2048
_MAX_EXPRESSION = 256
_MAX_TOKENS = 96
_MAX_DEPTH = 24
_MAX_DIGITS = 32
_MAX_BITS = 256
_MAX_POWER = 16
_MAX_PARTS = 16
_TRANSLATION = str.maketrans(
    "０１２３４５６７８９（），：＝＋－−×÷＊／＾＿？",
    "0123456789(),:=+--*/*/^_?",
)
_TOKEN = re.compile(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)|\*\*|[+*/^()\-]")
_QUESTION_PREFIX = re.compile(r"^(?:计算|求值|直接写出得数)\s*:?\s*")
_ANSWER_PREFIX = re.compile(r"^(?:解|答|答案)\s*:\s*")
_BLANK = re.compile(r"(?:_+|\?|□|\(\s*\))")
_TYPES = (None, "calculation", "fill_blank", "single_choice")


class _Unsupported(ValueError):
    pass


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise _Unsupported("missing_or_oversized_text")
    return value.translate(_TRANSLATION).strip()


def _bounded(value):
    if (abs(value.numerator).bit_length() > _MAX_BITS
            or value.denominator.bit_length() > _MAX_BITS):
        raise _Unsupported("arithmetic_limit")
    return value


def _tokens(expression):
    if not expression or len(expression) > _MAX_EXPRESSION:
        raise _Unsupported("expression_length")
    result = []
    position = 0
    while position < len(expression):
        if expression[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(expression, position)
        if match is None:
            raise _Unsupported("unsupported_syntax")
        token = match.group()
        result.append("^" if token == "**" else token)
        if len(result) > _MAX_TOKENS:
            raise _Unsupported("token_limit")
        position = match.end()
    if not result:
        raise _Unsupported("empty_expression")
    return tuple(result)


class _Parser:
    """Recursive descent; powers bind before unary signs and associate right."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.position = 0

    def take(self, *values):
        if self.position < len(self.tokens) and self.tokens[self.position] in values:
            token = self.tokens[self.position]
            self.position += 1
            return token
        return None

    def expression(self, depth=0):
        value = self.term(depth)
        while operator := self.take("+", "-"):
            right = self.term(depth)
            value = _bounded(value + right if operator == "+" else value - right)
        return value

    def term(self, depth):
        value = self.unary(depth)
        while operator := self.take("*", "/"):
            right = self.unary(depth)
            if operator == "/" and right == 0:
                raise _Unsupported("division_by_zero")
            value = _bounded(value * right if operator == "*" else value / right)
        return value

    def unary(self, depth):
        if depth > _MAX_DEPTH:
            raise _Unsupported("depth_limit")
        if operator := self.take("+", "-"):
            value = self.unary(depth + 1)
            return -value if operator == "-" else value
        value = self.atom(depth)
        if self.take("^"):
            exponent = self.unary(depth + 1)
            if exponent.denominator != 1 or abs(exponent.numerator) > _MAX_POWER:
                raise _Unsupported("power_limit")
            power = exponent.numerator
            if value == 0 and power <= 0:
                raise _Unsupported("undefined_power")
            # Bound the allocation BEFORE exponentiation, even if its result
            # would later be multiplied by zero or cancelled by another term.
            if max(abs(value.numerator).bit_length(), value.denominator.bit_length()) * abs(power) > _MAX_BITS:
                raise _Unsupported("power_size_limit")
            value = _bounded(value ** power)
        return value

    def atom(self, depth):
        if self.take("("):
            value = self.expression(depth + 1)
            if not self.take(")"):
                raise _Unsupported("unbalanced_parentheses")
            return value
        if self.position == len(self.tokens):
            raise _Unsupported("incomplete_expression")
        token = self.tokens[self.position]
        self.position += 1
        if not (token[0].isdigit() or token.startswith(".")):
            raise _Unsupported("expected_number")
        if sum(character.isdigit() for character in token) > _MAX_DIGITS:
            raise _Unsupported("literal_limit")
        return _bounded(Fraction(token))


def _number(expression):
    tokens = _tokens(expression)
    parser = _Parser(tokens)
    value = parser.expression()
    if parser.position != len(tokens):
        raise _Unsupported("unconsumed_expression")
    return tokens, value


def _question(value):
    text = _QUESTION_PREFIX.sub("", _text(value), count=1)
    if text.endswith("。"):
        text = text[:-1].rstrip()
    parts = text.split("=")
    if len(parts) > 2:
        raise _Unsupported("multiple_question_equalities")
    if len(parts) == 2 and parts[1].strip() and not _BLANK.fullmatch(parts[1].strip()):
        raise _Unsupported("not_a_single_terminal_blank")
    return _number(parts[0].strip())


def _work(value, expected):
    """Read ALL work, returning correctness and a syntax-preserving signature.

    Full equalities on new lines can describe independent subcalculations.
    A line beginning '=' instead continues the previous value (or the stem).
    Bare values on multiple lines have ambiguous answer binding and are refused.
    """
    text = _ANSWER_PREFIX.sub("", _text(value), count=1)
    if text.endswith("。"):
        text = text[:-1].rstrip()
    lines = [line.strip() for line in re.split(r"[\r\n;；]+", text) if line.strip()]
    if not lines or len(lines) > _MAX_PARTS:
        raise _Unsupported("work_limit")
    previous = expected
    valid = True
    signature = []
    count = 0
    for index, line in enumerate(lines):
        parts = line.split("=")
        continued = not parts[0].strip()
        if continued:
            parts = parts[1:]
        if not parts or any(not part.strip() for part in parts):
            raise _Unsupported("incomplete_equality")
        if len(lines) > 1 and len(parts) == 1 and not continued:
            # A first bare expression is supported only when the next line
            # explicitly continues its equality, never as an extra answer.
            if index != 0 or not lines[1].startswith("="):
                raise _Unsupported("ambiguous_work_lines")
        count += len(parts)
        if count > _MAX_PARTS:
            raise _Unsupported("work_limit")
        parsed = [_number(part.strip()) for part in parts]
        values = [item[1] for item in parsed]
        valid &= (not continued or previous == values[0])
        valid &= all(left == right for left, right in zip(values, values[1:]))
        previous = values[-1]
        signature.append((continued, tuple(item[0] for item in parsed)))
    return bool(valid and previous == expected), tuple(signature)


def _one_record(records, field):
    if records is None:
        return None
    if not isinstance(records, (list, tuple)) or len(records) > 1:
        raise _Unsupported("unsupported_" + field)
    if not records:
        return None
    record = records[0]
    if not isinstance(record, dict) or type(record.get("index")) is not int or record["index"] != 1:
        raise _Unsupported("invalid_" + field + "_binding")
    return record


def _choices(options, question_type, expected):
    if options is None or (isinstance(options, (list, tuple)) and not options):
        if question_type == "single_choice":
            raise _Unsupported("missing_options")
        return None
    if question_type not in (None, "single_choice") or not isinstance(options, (list, tuple)) or not 2 <= len(options) <= 8:
        raise _Unsupported("unsupported_options")
    values = {}
    for option in options:
        if not isinstance(option, dict):
            raise _Unsupported("invalid_option")
        label = option.get("label")
        if not isinstance(label, str) or not re.fullmatch(r"[A-H]", label) or label in values:
            raise _Unsupported("invalid_option_label")
        _, values[label] = _number(_text(option.get("text")))
    if sum(value == expected for value in values.values()) != 1:
        raise _Unsupported("ambiguous_or_missing_correct_option")
    return values


def _listed_answers(value, count):
    """A complete ordered list, optionally numbered with '1. ' etc."""
    _text(value)  # Check type and total size without changing the transcript.
    parts = re.split(r"[;；\n]", value.replace("\r\n", "\n"))
    if len(parts) != count or any(not part.strip() for part in parts):
        raise _Unsupported("answer_list_coverage")
    result = []
    numbered = None
    for index, part in enumerate(parts, 1):
        part = part.strip()
        match = re.fullmatch(r"([1-8])\.[ \t]+(.+)", part)
        if numbered is None:
            numbered = bool(match)
        if bool(match) != numbered or match and int(match[1]) != index:
            raise _Unsupported("answer_list_order")
        result.append(match[2].strip() if match else part)
    return result


def _terminal_question(text):
    normalized = _text(text)
    parts = normalized.split("=")
    if len(parts) != 2 or not _BLANK.fullmatch(parts[1].strip()):
        raise _Unsupported("not_an_independent_terminal_blank")
    return _question(normalized)


def _multiple(stem_text, answer_text, options, question_type, slots, checks,
              candidate_verdict, correct_answer):
    if (question_type not in (None, "calculation", "fill_blank")
            or not (options is None or isinstance(options, (list, tuple)) and not options)):
        raise _Unsupported("unsupported_multi_slot_question")
    if candidate_verdict not in ("correct", "incorrect", "wrong", "partially_correct"):
        raise _Unsupported("unsupported_candidate_verdict")
    if not 2 <= len(slots) <= 8 or not isinstance(checks, (list, tuple)) or len(checks) != len(slots):
        raise _Unsupported("multi_slot_coverage")
    stem = _QUESTION_PREFIX.sub("", _text(stem_text), count=1)
    if stem.endswith("。"):
        stem = stem[:-1].rstrip()
    questions = re.split(r"[;；\n]", stem.replace("\r\n", "\n"))
    if len(questions) != len(slots):
        raise _Unsupported("stem_slot_coverage")
    # Parse the ENTIRE stem before looking at any supplied solution or slot.
    parsed = [_terminal_question(question) for question in questions]
    students = _listed_answers(answer_text, len(slots))
    references = (_listed_answers(correct_answer, len(slots))
                  if correct_answer is not None else None)
    candidate = "incorrect" if candidate_verdict == "wrong" else candidate_verdict
    if candidate != "correct" and references is None:
        raise _Unsupported("missing_model_correct_answer")
    states, answers, calculations, conflicts = [], [], [], []
    for index, (slot, check, (tokens, expected)) in enumerate(zip(slots, checks, parsed), 1):
        if any(not isinstance(record, dict) or type(record.get("index")) is not int
               or record["index"] != index for record in (slot, check)):
            raise _Unsupported("multi_slot_order")
        if slot.get("answer_status") != "present":
            raise _Unsupported("unreadable_or_empty_slot")
        if _terminal_question(slot.get("prompt"))[0] != tokens:
            raise _Unsupported("slot_prompt_mismatch")
        if check.get("prompt") != slot["prompt"]:
            raise _Unsupported("check_prompt_mismatch")
        student = slot.get("answer_text")
        _text(student)
        if students[index - 1] != student.strip() or check.get("student_answer") != student:
            raise _Unsupported("multi_slot_transcript_mismatch")
        student_correct, _ = _work(student, expected)
        verdict = "correct" if student_correct else "incorrect"
        if check.get("verdict") not in ("correct", "incorrect"):
            raise _Unsupported("unsupported_check_verdict")
        if check["verdict"] != verdict:
            conflicts.append(f"slot_{index}_verdict_conflict")
        if check.get("correct_answer") is None:
            raise _Unsupported("missing_check_correct_answer")
        if not _work(check["correct_answer"], expected)[0]:
            conflicts.append(f"slot_{index}_reference_conflict")
        if references is not None and not _work(references[index - 1], expected)[0]:
            conflicts.append(f"slot_{index}_model_reference_conflict")
        states.append(student_correct)
        answers.append(f"{index}. {expected}")
        calculations.append(f"{index}. {''.join(tokens)}={expected}")
    verdict = "correct" if all(states) else "partially_correct" if any(states) else "incorrect"
    if candidate != verdict:
        conflicts.append("candidate_verdict_conflict")
    return {"status": "contradicted" if conflicts else "verified", "verdict": verdict,
            "correct_answer": "；".join(answers), "calculation": "\n".join(calculations),
            "reason": ",".join(conflicts) if conflicts else "exact_arithmetic_agreement"}


def verify_arithmetic_candidate(
    stem_text, answer_text, *, options=None, question_type=None,
    answer_slots=None, answer_checks=None, candidate_verdict=None, correct_answer=None,
) -> dict:
    """Check a candidate against exact arithmetic independently solved from stem.

    Supported verdicts: correct, incorrect (alias wrong), and partially_correct
    only for a mixture of correct/incorrect independent slots. Wrong candidates require
    correct_answer. A supplied reference or per-slot check is always validated;
    it cannot replace the actual answer_text. For one slot, its full prompt and
    transcript must bind to the original question and answer. The unique
    ``完整作答`` slot uses the already parsed whole stem as its prompt. Only
    with that single source slot may answer_text have a ``1. `` enumeration;
    the unwrapped transcript must still match the slot, including all steps.
    Multiple slots require complete independent terminal-blank expressions in
    the stem, ordered present slots, verbatim bound checks, all slot references,
    and an ordered answer_text list. Incorrect/partial candidates additionally
    require a complete model correct_answer list. Numeric single-choice options are supported
    only with a unique correct option and an exact student option label.

    Results use status=verified/contradicted/unsupported. Supported decisions
    include the independently derived verdict, correct_answer (a rational
    number, even for choices), and calculation; choices also include
    correct_option. Unsupported inputs carry a machine-readable reason only.
    """
    try:
        if question_type not in _TYPES:
            raise _Unsupported("unsupported_question_type")
        if isinstance(answer_slots, (list, tuple)) and len(answer_slots) > 1:
            return _multiple(stem_text, answer_text, options, question_type,
                             answer_slots, answer_checks, candidate_verdict, correct_answer)
        if candidate_verdict not in ("correct", "incorrect", "wrong"):
            raise _Unsupported("unsupported_candidate_verdict")
        candidate = "incorrect" if candidate_verdict == "wrong" else candidate_verdict
        tokens, expected = _question(stem_text)
        choices = _choices(options, question_type, expected)

        def read_answer(value):
            if choices is None:
                return _work(value, expected)
            label = _text(value)
            if label not in choices:
                raise _Unsupported("unbound_choice_answer")
            return choices[label] == expected, label

        def reference_matches(value):
            if choices is not None and _text(value) in choices:
                return choices[_text(value)] == expected
            return _work(value, expected)[0]

        slot = _one_record(answer_slots, "answer_slots")
        check = _one_record(answer_checks, "answer_checks")
        student_text = answer_text
        if slot is not None:
            # Whitespace after the dot is mandatory: 1.4 remains a decimal.
            # Never strip question labels, other numbers, or multiline lists.
            enumeration = re.fullmatch(r"1\.[ \t]+(.+)", _text(answer_text))
            if enumeration:
                student_text = enumeration[1]
        student_correct, signature = read_answer(student_text)

        def prompt_matches(prompt):
            return _text(prompt) == "完整作答" or _question(prompt)[0] == tokens

        if slot is not None:
            if slot.get("answer_status") != "present":
                raise _Unsupported("unreadable_or_empty_slot")
            if not prompt_matches(slot.get("prompt")):
                raise _Unsupported("slot_prompt_mismatch")
            if read_answer(slot.get("answer_text"))[1] != signature:
                raise _Unsupported("slot_transcript_mismatch")
        if check is not None:
            if slot is None:
                raise _Unsupported("check_without_source_slot")
            if not prompt_matches(check.get("prompt")):
                raise _Unsupported("check_prompt_mismatch")
            if read_answer(check.get("student_answer"))[1] != signature:
                raise _Unsupported("check_transcript_mismatch")

        verdict = "correct" if student_correct else "incorrect"
        result = {"verdict": verdict, "correct_answer": str(expected),
                  "calculation": "".join(tokens) + "=" + str(expected)}
        if choices is not None:
            result["correct_option"] = next(label for label, value in choices.items() if value == expected)
        conflicts = []
        if candidate != verdict:
            conflicts.append("candidate_verdict_conflict")
        if correct_answer is not None and not reference_matches(correct_answer):
            conflicts.append("reference_answer_conflict")
        if check is not None:
            if check.get("verdict") not in ("correct", "incorrect"):
                raise _Unsupported("unsupported_check_verdict")
            if check["verdict"] != verdict:
                conflicts.append("check_verdict_conflict")
            if check.get("correct_answer") is not None:
                if not reference_matches(check["correct_answer"]):
                    conflicts.append("check_reference_conflict")
            elif verdict == "incorrect":
                raise _Unsupported("missing_check_correct_answer")
        if conflicts:
            return {"status": "contradicted", **result, "reason": ",".join(conflicts)}
        if candidate == "incorrect" and correct_answer is None:
            raise _Unsupported("missing_model_correct_answer")
        return {"status": "verified", **result, "reason": "exact_arithmetic_agreement"}
    except _Unsupported as error:
        return {"status": "unsupported", "reason": str(error)}
