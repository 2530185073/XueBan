"""Synthetic protocol fixtures; do not recreate missing historical model evidence."""
from copy import deepcopy
import pytest
from xueban_backend.providers import definition_enumeration as de, semantic_alignment as sa
from xueban_backend.providers.reconciliation import source_id
from test_semantic_alignment import observation, pair, response


def sources():
    a = observation('五(一)1(1)', '解释词语：内集', subject='语文', question_type='fill_blank')
    b = observation('一1(1)', '《咏雪》解释词语：(1) 内集____', subject='语文', question_type='fill_blank')
    return a, b


def compatible(a, b):
    return sa.material_conditions_compatible(sa.material_condition_signature(a), sa.material_condition_signature(b))


def test_only_clear_label_bound_ordinal_is_not_a_condition():
    a, b = sources(); originals = deepcopy((a, b))
    assert sa._printed_literals(a) == [] and sa._printed_literals(b) == ['1']
    assert compatible(a, b)
    # No model response: no pair, despite the newly compatible condition view.
    out = sa.evaluate_alignment(response(unmatched=[source_id('primary', a), source_id('secondary', b)]), [a], [b])
    assert not out['pairs'] and out['audit']['independent_judgments_added'] == 0
    assert (a, b) == originals


def test_synthetic_complete_binding_pairs_but_never_rewrites_or_judges():
    a, b = sources(); originals = deepcopy((a, b))
    proposal = pair(a, b)
    out = sa.evaluate_alignment(response(proposal), [a], [b])
    accepted = out['pairs'][0]
    assert accepted['evidence'] == proposal
    assert accepted['primary_source_id'] == source_id('primary', a)
    assert accepted['secondary_source_id'] == source_id('secondary', b)
    audit = accepted['condition_presentation']
    assert audit['judgment_established'] is False and audit['source_text_changed'] is False
    start, end = audit['secondary_ordinal_span']
    assert b['stem_text'][start:end] == '1'
    assert audit['primary_ordinal_span'] is None
    assert out['audit']['independent_judgments_added'] == 0
    assert not any(k in accepted for k in ('verdict', 'recognition_status', 'correct_answer'))
    assert (a, b) == originals


@pytest.mark.parametrize('changes', [
    {'stem_text': '《咏雪》解释词语：(2) 内集____'},
    {'label': '一1(2)'},
    {'label': ''},
    {'label': '未知'},
    {'stem_text': '《咏雪》解释词语：(1) 乃____'},
    {'stem_text': '《咏雪》解释词语：(1) 内集2人____'},
    {'stem_text': '《咏雪》解释词语：(1) 内集（2年）____'},
    {'stem_text': '《咏雪》解释词语：(1) 内集（2分）____'},
    {'stem_text': '《咏雪》解释词语：(1) 内集，解释两种含义____'},
    {'stem_text': '《咏雪》解释词语：(1) 内集____、乃____'},
    {'stem_text': '《咏雪》解释词语：(1) 内集____', 'blank_count': 2},
    {'target_unit': 'question'},
    {'question_type': 'calculation'},
    {'subject': '数学'},
    {'options': [{'label': 'A', 'text': '内集'}]},
])
def test_different_word_question_real_quantity_or_unsupported_shape_keeps_veto(changes):
    a, b = sources(); b.update(changes)
    assert not compatible(a, b)
    result = sa.evaluate_alignment(response(pair(a, b)), [a], [b])
    assert not result['pairs']


def test_conflicting_explicit_titles_do_not_gain_exception():
    a, b = sources(); a['stem_text'] = '《别篇》解释词语：内集'
    assert not compatible(a, b)


@pytest.mark.parametrize('changes,code', [
    ({'primary_source_id': 'invented'}, 'unknown_source'),
    ({'primary_quote': '自造引文'}, 'target_quote_not_unique'),
    ({'same_target': False}, 'target_not_confirmed'),
    ({'same_question': False}, 'target_not_confirmed'),
    ({'secondary_target_count': 2}, 'split_required'),
    ({'image_evidence': ''}, 'missing_target_evidence'),
    ({'primary_slot': 1}, 'slot_echo_mismatch'),
    ({'conditions_same': False}, 'inconsistent_condition_claim'),
])
def test_every_existing_gate_still_applies(changes, code):
    a, b = sources()
    result = sa.evaluate_alignment(response(pair(a, b, **changes)), [a], [b])
    assert not result['pairs'] and result['rejected'][0]['code'] == code


def test_shared_quote_and_reused_source_still_reject():
    a, b = sources(); other = {**a, 'label': '五(二)1(1)'}
    result = sa.evaluate_alignment(response(pair(a, b)), [a, other], [b])
    assert not result['pairs'] and result['rejected'][0]['code'] == 'target_quote_not_unique'
    result = sa.evaluate_alignment(response(pair(a, b), pair(a, b)), [a], [b])
    assert not result['pairs'] and {x['code'] for x in result['rejected']} == {'source_reused'}


def test_answer_or_judgment_changes_cannot_affect_condition_comparison():
    a, b = sources(); before = sa.material_condition_signature(b)
    b.update(answer_text='秘密新作答', verdict='incorrect', correct_answer='另一个答案', basis_summary='模型猜测')
    assert sa.material_condition_signature(b) == before and compatible(a, b)


def test_dispatch_and_validator_use_same_condition_exception():
    a, b = sources()
    report = sa.alignment_dispatch([a], [b])
    assert report['decision'] == 'dispatch' and report['compatible_edges'] == 1
    b['label'] = '一1(2)'
    report = sa.alignment_dispatch([a], [b])
    assert report['decision'] == 'not_dispatched' and report['compatible_edges'] == 0


def test_original_numeric_comparison_results_unchanged():
    a = observation('1', '计算2+3=____')
    b = observation('2', '计算2+3=____')
    assert compatible(a, b)
    b['stem_text'] = '计算2+4=____'
    assert not compatible(a, b)
