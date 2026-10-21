"""Only a source-bound review may ignore an optional terminal choice slot."""
from copy import deepcopy

import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_two_model_reconciliation import pipeline
from test_whole_page_pipeline import picture
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers.reconciliation import (
    evaluate_review, source_id, terminal_choice_presentation_same,
)
from xueban_backend.providers.two_model import _needs_condition_readback


def pair():
    a = correct('2', question_type='single_choice',
                stem_text='两个数相除，商为正数，则这两个数', answer_text='C',
                options=[{'label': k, 'text': v} for k, v in
                         [('A', '都为正数'), ('B', '都为负数'), ('C', '同号'), ('D', '异号')]])
    return a, {**deepcopy(a), 'stem_text': a['stem_text'] + '____'}


def proposal(source, **changes):
    p = review(source=source, label=source['label'], **changes)
    p['observation_comparison']['difference_type'] = 'presentation'
    p['judgment_basis']['condition_quotes'].append(
        {'field': 'option', 'option_label': 'C', 'text': '同号'})
    return p


@pytest.mark.parametrize('reverse', [False, True])
def test_terminal_choice_slot_requires_review_and_keeps_all_sources(reverse):
    a, b = pair()
    if reverse: a, b = b, a
    old = deepcopy((a, b)); ids = source_id('primary', a), source_id('secondary', b)
    assert terminal_choice_presentation_same(a, b)
    assert not _needs_condition_readback(a, b)
    p = proposal(b, selected_source='secondary')
    out = evaluate_review(p, a, b)
    assert out['resolved'] and not out['requires_visual_support']
    assert out['audit']['condition_presentation_rule'] == 'single_choice_optional_terminal_slot_v1'
    assert out['source'] == b and out['source_id'] == ids[1]
    assert (a, b) == old and ids == (source_id('primary', a), source_id('secondary', b))


@pytest.mark.parametrize('replacement', [
    '两个数相除，商为负数，则这两个数____',
    '两个数相除，商为正数，则这两个数=____',
    '两个数相除，商为正数，则这两个数____米',
    '两个数相除，商为正数，则这两个数____（填数字）',
    '两个数相除，商为正数，则这两个数（____）',
    '两个数相除，商为正数，则这两个数＿',
    '两个数相除，商为正数，则这两个数____或____',
    '两个数相除，商为正数，则这两个数____C',
])
def test_only_exact_terminal_underscore_slot_is_eligible(replacement):
    a, b = pair(); b['stem_text'] = replacement
    assert not terminal_choice_presentation_same(a, b)
    out = evaluate_review(proposal(a), a, b)
    assert not out['resolved'] or out['requires_visual_support']


@pytest.mark.parametrize('head', [
    '已知____，选择正确说法的是', '已知（ ），选择正确说法的是',
    '已知[ ]，选择正确说法的是', '已知□，选择正确说法的是',
    '已知＿，选择正确说法的是', '3+2=', 'a+b', '-3',
])
def test_internal_blanks_or_expression_endpoints_do_not_become_choice_slots(head):
    a, b = pair(); a['stem_text'] = head; b['stem_text'] = head + '____'
    assert not terminal_choice_presentation_same(a, b)


@pytest.mark.parametrize('left,right', [
    ('当a=-3时，正确的值是', '当a=3时，正确的值是'),
    ('当a=3时，正确的值是', '当b=3时，正确的值是'),
    ('当n=3时，正确的值是', '当π=3时，正确的值是'),
    ('第2024年的值是', '第2025年的值是'),
])
def test_numbers_signs_variables_and_years_are_never_removed(left, right):
    a, b = pair(); a['stem_text'] = left; b['stem_text'] = right + '____'
    assert not terminal_choice_presentation_same(a, b)
    assert not evaluate_review(proposal(a), a, b)['resolved']


@pytest.mark.parametrize('change', [
    {'label': '3'}, {'label': None}, {'label': '题目'}, {'label': '2#1'},
    {'question_type': 'fill_blank'}, {'question_type': 'multiple_choice'},
    {'target_unit': 'question'}, {'uncertainties': ['可能不清楚']},
    {'answer_status': 'unclear'}, {'answer_text': 'B'}, {'answer_text': '(C)'},
    {'answer_text': 'C，因为同号'}, {'options': []},
])
def test_scope_and_actual_answer_are_not_inferred(change):
    a, b = pair(); b.update(change)
    assert not terminal_choice_presentation_same(a, b)


@pytest.mark.parametrize('change', ['text', 'order', 'duplicate', 'missing', 'symbol'])
def test_complete_ordered_options_must_be_identical(change):
    a, b = pair()
    if change == 'text': b['options'][2]['text'] = '异号'
    if change == 'order': b['options'].reverse()
    if change == 'duplicate': b['options'][3]['label'] = 'C'
    if change == 'missing': b['options'].pop()
    if change == 'symbol':
        a['options'][3]['text'] = '2又1/2 n'; b['options'][3]['text'] = '2又1/2π'
    assert not terminal_choice_presentation_same(a, b)
    assert not evaluate_review(proposal(a), a, b)['resolved']


@pytest.mark.parametrize('change', ['source', 'basis_source', 'quotes', 'unbound_quote',
                                    'answer', 'conditions', 'observations', 'same_question', 'direct'])
def test_source_and_quote_gates_are_not_bypassed(change):
    a, b = pair(); p = proposal(a)
    if change == 'source': p['source_id'] = 'primary:' + '0' * 64
    if change == 'basis_source': p['judgment_basis']['source_id'] = 'primary:' + '0' * 64
    if change == 'quotes': p['judgment_basis']['condition_quotes'] = []
    if change == 'unbound_quote': p['judgment_basis']['condition_quotes'][0]['text'] = '另一道题'
    if change == 'answer': p['answer_text'] = 'D'
    if change == 'conditions': p['observation_comparison']['conditions_same'] = False
    if change == 'observations': p['observation_comparison']['observations_same'] = False
    if change == 'same_question': p['same_question'] = False
    if change == 'direct': p['observation_basis'] = 'semantic_only'
    assert not evaluate_review(p, a, b)['resolved']


@pytest.mark.parametrize('resolution,difference', [
    ('non_material', 'presentation'), ('equivalent', 'paraphrase'), ('equivalent', 'none'),
])
def test_only_equivalent_presentation_review_can_resolve(resolution, difference):
    a, b = pair(); p = proposal(a, resolution=resolution)
    p['observation_comparison']['difference_type'] = difference
    out = evaluate_review(p, a, b)
    assert not out['resolved'] and out['rejection_code'] == 'material_condition_change'


def test_pipeline_without_review_does_not_match_or_release_but_bound_review_uses_three_calls():
    a, b = pair(); original = deepcopy((a, b))
    failed = pipeline([a], [b], ProviderError('offline_unavailable'))
    bad = failed.recognize(images=(picture(),))
    assert bad.data['items'][0]['recognition_status'] == 'needs_revision'
    assert failed.visual_client.calls == []
    flow = pipeline([a], [b], {'items': [proposal(a)]})
    result = flow.recognize(images=(picture(),)); row = result.data['items'][0]
    assert row['recognition_status'] == 'ready' and row['assessment']['verification']['status'] == 'reviewed'
    assert row['answer_text'] == a['answer_text'] and row['stem_text'] == a['stem_text']
    assert len(result.metadata.attempts) == 3 and result.metadata.usage.total_tokens == 90
    assert len(flow.reconciliation_client.calls) == 1 and flow.visual_client.calls == []
    assert result.data['multimodel_audit']['calls'][0]['items'] == [a]
    assert result.data['multimodel_audit']['calls'][1]['items'] == [b]
    assert (a, b) == original


def test_existing_empty_parenthesis_behavior_is_not_part_of_new_rule():
    a, b = pair(); b['stem_text'] = a['stem_text'] + '()'
    assert not terminal_choice_presentation_same(a, b)
    result = evaluate_review(proposal(a), a, b)
    assert 'condition_presentation_rule' not in result.get('audit', {})
