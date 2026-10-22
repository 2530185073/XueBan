"""Independent closed-grammar counterexamples. No network/model/DB calls."""
from copy import deepcopy
import pytest
from xueban_backend.providers import definition_enumeration as de, semantic_alignment as sa
from xueban_backend.providers.reconciliation import source_id
from test_semantic_alignment import observation,pair,response


def original(term='内集'):
    return observation('五(一)1(1)','解释词语：'+term,subject='语文',question_type='fill_blank')
def numbered(stem='《咏雪》解释词语：(1) 内集____'):
    return observation('一1(1)',stem,subject='语文',question_type='fill_blank')
def compatible(a,b):
    return sa.material_conditions_compatible(sa.material_condition_signature(a),sa.material_condition_signature(b))


@pytest.mark.parametrize('stem',[
    '《咏雪2026》解释词语：(1) 内集____',
    '《咏雪》解释词语：(1) 内集2026年____',
    '《咏雪》解释词语：(1) 内集２０２６年____',
    '《咏雪》解释词语：(1) 内集2人____',
    '《咏雪》解释词语：(1) 内集2-1人____',
    '《咏雪》解释词语：(1) 内集10%____',
    '《咏雪》解释词语：(1) 内集（1分）____',
    '《咏雪》解释词语：(1) 内集=2____',
    '《咏雪》解释词语：(1) 内集≥2____',
    '《咏雪》解释词语：(1) 内集，至少一义____',
    '《咏雪》解释词语：(-1) 内集____',
    '《咏雪》解释词语：(1.0) 内集____',
    '《咏雪》解释词语：(1) 内集____与友期____',
])
def test_real_printed_conditions_or_extra_target_cannot_use_exception(stem):
    a,b=original(),numbered(stem)
    assert de.parse(b) is None
    # All selected examples create a genuinely different existing literal view.
    assert sa.material_condition_signature(a)[:2] != sa.material_condition_signature(b)[:2]
    assert not compatible(a,b)
    proposal=pair(a,b)
    outcome=sa.evaluate_alignment(response(proposal),[a],[b])
    assert outcome['pairs']==[]


@pytest.mark.parametrize('first_term,second_term', [('一日','二日'),('三令五申','三令六申'),('二〇二六年','二〇二五年')])
def test_chinese_numeral_inside_word_never_discarded(first_term,second_term):
    a=original(first_term); b=numbered('解释词语：(1) '+second_term+'____')
    assert de.parse(a) is None or de.parse(a).term==first_term
    assert de.parse(b) is None or de.parse(b).term==second_term
    assert not compatible(a,b)
    assert not sa.evaluate_alignment(response(pair(a,b)),[a],[b])['pairs']


def test_same_term_and_same_leaf_are_not_enough_to_pair():
    a,b=original(),numbered()
    before=deepcopy((a,b)); hashes=[source_id('primary',a),source_id('secondary',b)]
    for proposal in (response(),response(unmatched=hashes),response(pair(a,b,primary_quote='not an original quote'))):
        out=sa.evaluate_alignment(proposal,[a],[b])
        assert not out['pairs']
        assert out['audit']['independent_judgments_added']==0
    assert (a,b)==before
    assert [source_id('primary',a),source_id('secondary',b)]==hashes


def test_pair_only_changes_veto_and_retains_exact_ordinal_span_source_and_evidence():
    a,b=original(),numbered('《咏雪》解释词语：（1） 内集____')
    before=deepcopy((a,b)); proposal=pair(a,b); out=sa.evaluate_alignment(response(proposal),[a],[b])
    assert len(out['pairs'])==1
    accepted=out['pairs'][0]; audit=accepted['condition_presentation']
    start,end=audit['secondary_ordinal_span']
    assert b['stem_text'][start:end]=='1'
    assert audit['scope']=='numeric_condition_veto_only'
    assert audit['source_text_changed'] is audit['judgment_established'] is False
    assert accepted['evidence']==proposal
    assert not {'verdict','recognition_status','correct_answer','knowledge_points'} & set(accepted)
    assert (a,b)==before
    exported=sa.build_alignment_input([a],[b])
    assert exported['primary'][0]['stem_text']==a['stem_text']
    assert exported['secondary'][0]['stem_text']==b['stem_text']


def test_source_alias_and_ordinal_mismatch_cannot_be_repaired_by_parent_number():
    a=original();b=numbered()
    b['label']='一1(2)'
    assert de.parse(b) is None and not compatible(a,b)
    assert not sa.evaluate_alignment(response(pair(a,b)),[a],[b])['pairs']
    b['label']='一1(1)#2'
    assert de.parse(b) is None
    assert not sa.evaluate_alignment(response(pair(a,b)),[a],[b])['pairs']


def test_original_literal_equality_is_preserved_without_claiming_new_semantics():
    a,b=original(),original()
    # Even when the new grammar does not apply, identical pre-existing literal
    # signatures stay identical; this patch is not a new semantic judge.
    b.update(label='strange',subject='数学',question_type='calculation')
    assert de.parse(b) is None
    assert sa.material_condition_signature(a)[:2]==sa.material_condition_signature(b)[:2]
    assert compatible(a,b)

