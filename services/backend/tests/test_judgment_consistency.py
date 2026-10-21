"""Constant-only model-field conflicts route to existing AI review, not a verdict."""
from copy import deepcopy
import pytest
from test_two_model_pipeline import correct
from test_reconciliation import review
from test_whole_page_pipeline import FakeClient,picture
from xueban_backend.providers.judgment_consistency import numeric_verdict_conflict
from xueban_backend.providers.reconciliation import evaluate_review,source_id
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def candidate(answer='-7/9',expected='-9/7',**changes):
    return correct(stem_text='已知两数的商和被除数，求除数。',question_type='application',
        answer_text=answer,correct_answer=expected,**changes)

@pytest.mark.parametrize('answer,expected',[
 ('-7/9','-9/7'),('1','-1'),('−１','1'),('(-7/9)','(-9)/7'),
 ('=-(7/2÷9/2)\n=-(7/2×2/9)\n=-7/9','-9/7'),('2+2=4','3'),
])
def test_complete_constant_fields_conflict_without_modifying_source(answer,expected):
    item=candidate(answer,expected);before=deepcopy(item);identity=source_id('primary',item)
    proof=numeric_verdict_conflict(item)
    assert proof and proof['original_verdict']=='correct' and proof['original_correct_answer']==expected
    assert proof['student_verdict_assigned'] is False and proof['observation_rewritten'] is False
    assert proof['intermediate_equalities_validated'] is False
    ref=proof['final_expression'];assert answer[ref['start']:ref['end']]==ref['text']
    assert item==before and source_id('primary',item)==identity

@pytest.mark.parametrize('answer,expected',[
 ('1/2','0.5'),('  +2/4  ','0.500'),('−１','-1'),('=2/4\n=1/2','0.5'),
 ('=-(7/2÷9/2)\n=-(7/2×2/9)\n=-7/9','-7/9'),
])
def test_equal_values_not_format_conflicts(answer,expected):
    assert numeric_verdict_conflict(candidate(answer,expected)) is None

@pytest.mark.parametrize('answer',[
 '答：-7/9','-7/9米','x=-7/9','A','-7/9\n另：-9/7','-7/9;-9/7','-7/9；-9/7',
 '第一空：-1；第二空：7','第1空：-1\n第2空：7','改：-7/9','[不清]=-7/9',
 '2+2\n4','2+2\n\n=4','1+1=2\n3+3=6','2 3/7','-2又3/7','2(3)','(1+2=3',
 '2**3=8','2^3=8','2²=4','1//2=0','1e2=100','True=1','abs(-1)=1',
 '1='+('+'*15)+'1','='+'('*300+'1'+')'*300+'=1','1='+'1'*1300,'1='+'1'*13,
 '1='+('('*13)+'2'+(')'*13),
 '2=7/2÷9/2','1=1=1=1=1=1=1=1=1',
])
def test_unsupported_whole_field_is_noop(answer):
    assert numeric_verdict_conflict(candidate(answer,'-123')) is None

@pytest.mark.parametrize('changes',[
 {'answer_status':'blank'}, {'answer_status':'unclear'}, {'uncertainties':['划改待辨']},
 {'subject':'物理'}, {'question_type':'proof'}, {'question_type':'single_choice'},
 {'options':[{'label':'A','text':'-7/9'}]}, {'target_unit':'question'}, {'target_unit':'unsupported'},
 {'verdict':'incorrect'}, {'verdict':'unable_to_judge'},
])
def test_scope_and_observation_guards(changes):
    item=candidate();item.update(changes);assert numeric_verdict_conflict(item) is None


def test_matched_pair_is_not_ready_when_one_judge_contradicts_itself():
    a,b=candidate(),candidate(expected=None);original=deepcopy((a,b));identities=(source_id('primary',a),source_id('secondary',b))
    pipeline=TwoModelWholePagePipeline(FakeClient('one',{'items':[a]}),FakeClient('two',{'items':[b]}))
    result=pipeline.recognize(images=(picture(),));row=result.data['items'][0]
    assert row['recognition_status']=='needs_revision' and row['assessment']['verdict']=='unable_to_judge'
    assert row['assessment']['verification']['reason_codes']==['judgment_disagreement']
    assert row['assessment']['knowledge_points']==[]
    audit=result.data['multimodel_audit']['judgment_field_conflicts'];assert len(audit)==1
    assert audit[0]['source_id']==identities[0] and audit[0]['original_correct_answer']=='-9/7'
    assert (a,b)==original and identities==(source_id('primary',a),source_id('secondary',b))
    assert len(pipeline.client.calls)==len(pipeline.review_client.calls)==1


def test_one_existing_review_can_clear_old_conflict_without_extra_visual():
    a,b=candidate(),candidate(expected=None)
    proposal=review(source=a,correct_answer=None,verdict='correct')
    reviewer=FakeClient('review',{'items':[proposal]})
    pipeline=TwoModelWholePagePipeline(FakeClient('one',{'items':[a]}),FakeClient('two',{'items':[b]}),reconciliation_client=reviewer)
    result=pipeline.recognize(images=(picture(),));row=result.data['items'][0]
    assert row['recognition_status']=='ready' and row['assessment']['verdict']=='correct'
    assert len(reviewer.calls)==1 and len(result.metadata.attempts)==3
    assert row['answer_text']==a['answer_text'] and row['assessment']['correct_answer'] is None
    assert result.data['multimodel_audit']['judgment_field_conflicts'][0]['original_correct_answer']=='-9/7'


def test_review_repeating_own_conflict_rejected_and_bounded():
    a,b=candidate(),candidate(expected=None)
    proposal=review(source=a,correct_answer='-9/7',verdict='correct')
    original=deepcopy((a,b,proposal))
    outcome=evaluate_review(proposal,a,b)
    assert outcome['resolved'] is False and outcome['rejection_code']=='numeric_judgment_self_contradiction'
    assert outcome['judgment_field_conflict']['source_id']==source_id('primary',a)
    reviewer=FakeClient('review',{'items':[proposal]})
    pipeline=TwoModelWholePagePipeline(FakeClient('one',{'items':[a]}),FakeClient('two',{'items':[b]}),reconciliation_client=reviewer)
    result=pipeline.recognize(images=(picture(),));row=result.data['items'][0]
    assert row['recognition_status']=='needs_revision' and row['assessment']['knowledge_points']==[]
    assert len(reviewer.calls)==1 and len(result.metadata.attempts)==3
    calls=result.data['multimodel_audit']['calls']
    application=next(call for call in calls if call['stage']=='reconciliation')['applications'][0]
    assert application['judgment_field_conflict']==outcome['judgment_field_conflict']
    assert (a,b,proposal)==original


def test_review_giving_equal_value_can_clear_old_conflict():
    a,b=candidate(),candidate(expected=None)
    outcome=evaluate_review(review(source=a,correct_answer='-14/18'),a,b)
    assert outcome['resolved'] is True
    assert outcome['correct_answer'] is None  # existing correct-verdict presentation
