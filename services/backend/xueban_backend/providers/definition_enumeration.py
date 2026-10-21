"""A printed definition-list ordinal is not a mathematical operand.

This is only a narrow exception to the numeric condition veto. It never pairs
sources, edits text, validates an image, or establishes a student's correctness.
"""
from dataclasses import dataclass
import re

PROTOCOL = 'definition-enumeration.v1'
_LABEL_TOKEN = re.compile(r'[一二三四五六七八九十百零〇0-9]+|\([一二三四五六七八九十百零〇0-9]+\)')
_STEM = re.compile(
    r'(?:《(?P<title>[\u4e00-\u9fff]{1,24})》)?解释词语[：:]\s*'
    r'(?:[（(](?P<ordinal>[1-9][0-9]*)[)）]\s*)?'
    r'(?P<term>[\u4e00-\u9fff]{1,16})(?:_{2,})?')


@dataclass(frozen=True)
class Definition:
    leaf: str
    term: str
    title: str | None
    ordinal: str | None
    ordinal_span: tuple[int, int] | None


def parse(source):
    # The current question protocol and multi-slot content are deliberately not
    # included. No answer, judge, student text, or model rationale is inspected.
    if (source.get('subject') not in {'语文', 'chinese'}
            or source.get('question_type') != 'fill_blank'
            or source.get('target_unit') not in {None, 'answer_slot'}
            or source.get('options') != []
            or (type(source.get('blank_count')) is int and source['blank_count'] > 1)):
        return None
    label, stem = source.get('label'), source.get('stem_text')
    if not isinstance(label, str) or not isinstance(stem, str) or len(label) > 100 or len(stem) > 200:
        return None
    tokens = _LABEL_TOKEN.findall(label)
    if len(tokens) < 2 or ''.join(tokens) != label:
        return None
    lm, sm = re.fullmatch(r'\(([1-9][0-9]*)\)', tokens[-1]), _STEM.fullmatch(stem)
    if not lm or not sm:
        return None
    ordinal = sm['ordinal']
    if ordinal is not None and ordinal != lm[1]:
        return None
    return Definition(lm[1], sm['term'], sm['title'], ordinal,
                      sm.span('ordinal') if ordinal is not None else None)


def equivalent(first, second):
    return (type(first) is Definition and type(second) is Definition
            and first.leaf == second.leaf and first.term == second.term
            and (not first.title or not second.title or first.title == second.title)
            and (first.ordinal is not None or second.ordinal is not None))


def evidence(first, second):
    return {'protocol': PROTOCOL, 'rule': 'label_bound_definition_ordinal',
            'scope': 'numeric_condition_veto_only',
            'primary_ordinal': first.ordinal, 'secondary_ordinal': second.ordinal,
            'primary_ordinal_span': list(first.ordinal_span) if first.ordinal_span else None,
            'secondary_ordinal_span': list(second.ordinal_span) if second.ordinal_span else None,
            'source_text_changed': False, 'judgment_established': False}
