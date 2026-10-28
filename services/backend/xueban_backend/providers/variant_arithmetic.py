"""Exact, bounded substitution checks for plain arithmetic fill-in chains.

This is not a general solver. Unsupported notation is left to the independent
model checks; a demonstrably false numeric equality can never be published.
"""
import ast
from fractions import Fraction
from itertools import product
import re

from .variant_structure import _BLANK


def _number(expression):
    expression = expression.strip().translate(str.maketrans('（）：＝−×÷', '():=-*/'))
    if not expression or len(expression) > 200 or not re.fullmatch(r'[\d\s.+*/()\-]+', expression):
        return None
    try:
        tree = ast.parse(expression, mode='eval')
        if sum(1 for _ in ast.walk(tree)) > 80:
            return None

        def evaluate(node, depth=0):
            if depth > 20:
                raise ValueError()
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                # Parse the literal, not Python's rounded binary float.
                value = Fraction(ast.get_source_segment(expression, node))
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = evaluate(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                a, b = evaluate(node.left, depth + 1), evaluate(node.right, depth + 1)
                if isinstance(node.op, ast.Add): value = a + b
                elif isinstance(node.op, ast.Sub): value = a - b
                elif isinstance(node.op, ast.Mult): value = a * b
                else: value = a / b
            else:
                raise ValueError()
            if abs(value.numerator) > 10**15 or value.denominator > 10**15:
                raise ValueError()
            return value

        return evaluate(tree.body)
    except (ValueError, TypeError, SyntaxError, ZeroDivisionError, OverflowError, RecursionError):
        return None


def arithmetic_blank_issues(question, answer):
    if question.get('question_kind') != 'fill_blank':
        return []
    fields = question.get('answer_fields', [])
    answers = {field['field_id']: field.get('answer_values', []) for field in answer.get('field_answers', [])}
    stem = question.get('stem_text', '')
    if len(stem) > 16000 or len(_BLANK.findall(stem)) != len(fields) or not fields:
        return []  # Structural validators handle these cases.
    alternatives = []
    for field in fields:
        value = answers.get(field['id'], [])
        if len(value) > 1 and all(_number(v) is not None for v in value):
            distinct = {_number(v): v for v in value}
            alternatives.append((field['id'], list(distinct.values())))
    if alternatives:
        combinations = 1
        for _, values in alternatives:
            combinations *= len(values)
        if combinations > 64:
            return [{'code': 'arithmetic_alternatives_exceeded', 'path': 'field_answers',
                     'message': '数值答案组合过多，无法在本次预算内核实。'}]
        for values in product(*(values for _, values in alternatives)):
            selected = dict(zip((field_id for field_id, _ in alternatives), values))
            variant = {**answer, 'field_answers': [
                {**field, 'answer_values': [selected[field['field_id']]]}
                if field['field_id'] in selected else field for field in answer['field_answers']]}
            found = arithmetic_blank_issues(question, variant)
            if found:
                return found
        return []
    cursor, issues = 0, []
    for line_number, line in enumerate(re.split(r'[\n；;。]', stem), 1):
        slots = list(_BLANK.finditer(line))
        line_fields = fields[cursor:cursor + len(slots)]
        cursor += len(slots)
        if not slots or not re.search(r'[=＝]', line):
            continue
        values = [answers.get(field['id'], []) for field in line_fields]
        # Multiple spellings of the same number (3, 3.0) are not a bypass.
        values = [value[:1] if value and all(_number(v) is not None for v in value)
                  and len({_number(v) for v in value}) == 1 else value for value in values]
        if any(len(value) != 1 or len(value[0]) > 200 for value in values):
            continue
        # A fraction belongs to one blank: 2÷(1/2), not 2÷1/2.
        # Strip only a marker present in the source. A substituted leading
        # answer such as (2)+3=5 is an operand, never a question number.
        expression = re.sub(r'^\s*[\u4e00-\u9fff\s，,]+[：:]\s*', '', line)
        expression = re.sub(r'^\s*[（(]\d+[）)]\s*(?=[\d_＿]|[（(][+\-−])', '', expression)
        replacements = iter(value[0] if value[0].strip() in ('+', '-', '−')
                            else f'({value[0]})' for value in values)
        filled = _BLANK.sub(lambda _: next(replacements), expression).strip()
        parts = re.split(r'[=＝]', filled)
        parsed = [_number(part) for part in parts]
        if len(parsed) < 2 or any(value is None for value in parsed):
            continue
        if len(set(parsed)) <= 1:
            continue
        issue = {'code': 'arithmetic_substitution_conflict', 'path': 'field_answers',
                 'line': line_number, 'equation_with_answers': filled,
                 'field_ids': [field['id'] for field in line_fields],
                 'message': '把各空答案代入后等式不成立；符号空不能替代终答的正负号。'}
        # Only when the final segment is exactly a single blank and all earlier
        # segments agree can arithmetic identify its expected value uniquely.
        original_parts = re.split(r'[=＝]', line)
        if (_BLANK.fullmatch(original_parts[-1].strip()) and len(set(parsed[:-1])) == 1):
            expected = parsed[0]
            issue.update(field_id=line_fields[-1]['id'], expected=str(expected),
                         expected_source='exact_arithmetic_substitution')
        issues.append(issue)
    return issues


def item_arithmetic_issues(item):
    """Use the same reference guard at publish, read, grade and feedback time."""
    if item.question_kind == 'fill_blank':
        from .variant_structure import blank_answer_issues, visible_blank_count
        question_fields = item.answer_fields or []
        answers = {'field_answers': item.field_answers or [], 'answer_values': item.answer_values or [],
                   'answer_kind': item.answer_kind, 'solution_text': item.solution_text or ''}
        structure = blank_answer_issues(answers, question_fields)
        visible = visible_blank_count(item.stem_text or '')
        if visible != len(question_fields) or not visible:
            structure.append({'code': 'stem_blank_count_mismatch', 'path': 'stem_text'})
        if structure:
            return structure
    return arithmetic_blank_issues(
        {'question_kind': item.question_kind, 'stem_text': item.stem_text,
         'answer_fields': item.answer_fields or []},
        {'field_answers': item.field_answers or []},
    )
