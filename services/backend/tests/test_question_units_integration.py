"""Experimental integration only. Text fixtures and real local DB, no API calls."""
from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_multimodal_worker import submit_correction, current_case
from test_reconciliation import review
from test_semantic_alignment import pair, response
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture, check_context
from xueban_backend.correction_models import Entity
from xueban_backend.providers import question_units as units, reconciliation, semantic_alignment, two_model
from xueban_backend.providers.ark import ProviderError


def question(label='3(1)', **changes):
    return correct(label, **{
        'stem_text': '若正方形的边长为a，则面积是____，周长是____。',
        'answer_text': '第1空：a^2；第2空：4a', 'blank_count': 2,
        'unit_complete': True, 'target_context': {
            'section': '用代数式表示', 'row': None, 'column': None, 'anchor': '若正方形的边长为a'},
        'correct_answer': None, 'correction_method': None, **changes})


def pipeline(a=None, b=None, **kwargs):
    return two_model.TwoModelWholePagePipeline(
        FakeClient('primary', {'items': a if a is not None else [question()]}),
        FakeClient('secondary', {'items': b if b is not None else [question()]}),
        target_unit='question', **kwargs)


def bound(item=None, asset='asset_original'):
    return units.bind_observation(item or question(), 'question', asset)


def test_new_two_blank_unit_is_one_record_and_keeps_raw_originals():
    a = question()
    pipe = pipeline([a], [a])
    result = pipe.recognize(images=(picture(),))
    assert len(result.data['items']) == 1
    row = result.data['items'][0]
    assert row['blank_count'] == 2 and row['target_unit'] == 'question'
    assert row['answer_text'] == a['answer_text']
    assert row['assessment']['verdict'] == 'correct'
    assert 'answer_checks' not in row['assessment']  # ordered text is not a slot audit
    assert result.prompt_version.endswith('.question-experimental')
    for call in result.data['multimodel_audit']['calls']:
        assert call['raw_items'] == [a]
        assert call['items'][0]['source_asset_id'] == 'asset_original'
    for client in (pipe.client, pipe.review_client):
        request = client.calls[0]
        assert '每个独立空格单独一项' not in request['instructions']
        assert '同题多空另造#1/#2' in request['instructions']
        assert 'blank_count' in request['output_schema']['properties']['items']['items']['required']
        assert json.loads(request['text'])['task_input'] == {}


def test_wrong_second_position_is_not_silently_changed_to_copy_first_sign():
    row = question(answer_text='第1空：-；第2空：5', verdict='incorrect',
                   correct_answer='第2空：-5', correction_method='第二个空应填写一个完整负数。')
    result = pipeline([row], [row]).recognize(images=(picture(),)).data['items'][0]
    assert result['answer_text'] == '第1空：-；第2空：5'
    assert result['assessment']['verdict'] == 'incorrect'
    assert result['assessment']['correct_answer'] == '第2空：-5'


@pytest.mark.parametrize('change', [
    {'answer_text': '第1空：a^2；第2空：[不清]'},
    {'answer_text': '第1空：a^2；第2空：[未作答]'},
    {'answer_text': 'a^2；4a'},
    {'answer_text': '第1空：a^2'},
    {'unit_complete': False},
])
def test_one_unclear_omitted_or_unwritten_position_never_releases_correct(change):
    source = question(**change)
    rows = pipeline([source], [source]).recognize(images=(picture(),)).data['items']
    assert rows and all(r['assessment']['verdict'] == 'unable_to_judge' for r in rows)
    assert all(r['assessment']['knowledge_points'] == [] for r in rows)
    assert all(r['recognition_status'] == 'needs_revision' for r in rows)


def test_whole_blank_is_unanswered_not_incorrect():
    q = question(answer_text=None, answer_status='blank', verdict='unanswered')
    row = pipeline([q], [q]).recognize(images=(picture(),)).data['items'][0]
    assert row['blank_count'] == 2 and row['assessment']['verdict'] == 'unanswered'


@pytest.mark.parametrize('field,value', [('section','普通光学显微镜的使用'),('row','Peter'),('column','Country')])
def test_same_label_different_section_or_table_location_retains_both_pending(field,value):
    a = question('1')
    b = question('1', target_context={**a['target_context'], field:value})
    reviewer = FakeClient('review', AssertionError('identity conflict cannot be washed away by review'))
    result = pipeline([a],[b],reconciliation_client=reviewer).recognize(images=(picture(),))
    rows=result.data['items']
    assert len(rows)==2 and not reviewer.calls
    assert [r['target_context'] for r in rows]==[a['target_context'],b['target_context']]
    assert all(r['assessment']['verdict']=='unable_to_judge' for r in rows)
    assert all('target_identity_conflict' in r['assessment']['verification']['reason_codes'] for r in rows)
    assert all(r['assessment']['verification']['independent_judgments']==1 for r in rows)


def test_six_blank_continuous_response_cannot_be_certified_by_four_blank_source():
    a = question('2b', blank_count=6, answer_text='；'.join(f'第{i}空：word{i}' for i in range(1,7)))
    b = {**a,'blank_count':4,'answer_text':'；'.join(f'第{i}空：word{i}' for i in range(1,5))}
    rows = pipeline([a],[b]).recognize(images=(picture(),)).data['items']
    assert len(rows)==2 and {r['blank_count'] for r in rows}=={4,6}
    assert all(r['assessment']['verdict']=='unable_to_judge' for r in rows)


def test_question_mode_page_identity_does_not_merge_same_label_between_photos():
    result=pipeline().recognize(images=(picture(),picture('page-b')))
    assert len(result.data['items'])==2
    sources=[c['items'][0] for c in result.data['multimodel_audit']['calls'] if c['stage']=='primary']
    assert reconciliation.source_id('primary',sources[0])!=reconciliation.source_id('primary',sources[1])


def test_valid_complete_multi_blank_semantic_pair_is_one_to_one_and_answer_blind():
    a,b=bound(),bound(question('三(一)'))
    payload=semantic_alignment.build_alignment_input([a],[b],target_unit='question')
    assert payload['target_unit']=='question'
    assert payload['primary'][0]['visible_blank_count']==2
    assert 'answer_text' not in json.dumps(payload) and 'verdict' not in json.dumps(payload)
    result=semantic_alignment.evaluate_alignment(response(pair(a,b)),[a],[b],target_unit='question')
    assert len(result['pairs'])==1 and not result['split_required']
    assert result['audit']['independent_judgments_added']==0
    # Actual answer content is irrelevant to pairing, including uncertainty.
    b['answer_text']='第1空：[不清]；第2空：4a'
    b['answer_status']='unclear'
    result=semantic_alignment.evaluate_alignment(response(pair(a,b)),[a],[b],target_unit='question')
    assert len(result['pairs'])==1


@pytest.mark.parametrize('change', [
    {'blank_count':1}, {'unit_complete':False}, {'target_unit':'answer_slot'},
    {'source_asset_id':'another-page'},
])
def test_new_unit_alignment_does_not_join_partial_legacy_or_other_page(change):
    a,b=bound(),{**bound(question('3(1)#2')),**change}
    result=semantic_alignment.evaluate_alignment(response(pair(a,b)),[a],[b],target_unit='question')
    assert result['pairs']==[] and result['rejected'][0]['code']=='target_unit_mismatch'


def test_different_old_slot_ids_are_never_combined_in_new_mode():
    a,b=bound(question('3(1)#1')),bound(question('3(1)#2'))
    result=semantic_alignment.evaluate_alignment(response(pair(a,b)),[a],[b],target_unit='question')
    assert result['pairs']==[] and result['rejected'][0]['code']=='different_slot'


def test_every_unit_extension_changes_bound_source_but_legacy_hash_stays_exact():
    legacy=correct()
    record={key:deepcopy(legacy.get(key,[] if key=='options' else None))
            for key in ('stem_text','options','answer_text','answer_status')}
    old='primary:'+hashlib.sha256(json.dumps({'label':legacy['label'],'observation':record},ensure_ascii=False,
                                  sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert reconciliation.source_id('primary',legacy)==old
    original=bound()
    for change in ({'blank_count':3},{'target_unit':'answer_slot'},{'unit_complete':False},
                   {'target_context':{**original['target_context'],'row':'other'}},
                   {'answer_slots':[{'index':1,'answer_text':'a^2'}]}, {'source_asset_id':'other'}):
        assert reconciliation.source_id('primary',{**original,**change})!=reconciliation.source_id('primary',original)


def test_review_cannot_select_complete_source_for_a_partial_or_different_target():
    a=bound()
    b={**a,'blank_count':1}
    outcome=reconciliation.evaluate_review(review(source=a),a,b)
    assert outcome['resolved'] is False and outcome['rejection_code']=='target_unit_mismatch'
    visual={**a,'target_context':{**a['target_context'],'row':'Peter'}}
    outcome=reconciliation.evaluate_review(review(source=visual,selected_source='visual',resolution='visual_resolution'),a,a,visual)
    assert outcome['resolved'] is False and outcome['rejection_code']=='target_unit_mismatch'


def test_review_cannot_hide_missing_second_answer_with_clear_flag():
    a=bound(question(answer_text='第1空：a^2'))
    outcome=reconciliation.evaluate_review(review(source=a),a,a)
    assert outcome['resolved'] is False and outcome['rejection_code']=='incomplete_question_unit'


def test_legacy_recheck_uses_old_unit_even_when_default_is_question_and_preserves_clients():
    q=correct('3(1)#2')
    judge=FakeClient('strong-judge',{})
    pipe=pipeline([q],[q],judgment_client=judge)
    context=check_context()
    context['question']['label']='3(1)#2'
    context['attempt']['answer_text']='4'
    result=pipe.recheck(**context)
    assert result.data['verdict']=='correct'
    assert pipe.target_unit=='question' and pipe.judgment_client is judge
    assert '每个独立空格单独一项' in pipe.client.calls[0]['instructions']
    # A unique same stem with a wrong slot or a whole-question label is rejected.
    for label in ('3(1)#1','3(1)'):
        pipe.client.output=pipe.review_client.output={'items':[correct(label)]}
        assert pipe.recheck(**context).data['verdict']=='unable_to_judge'


@pytest.mark.parametrize('value',['bad','',None,True])
def test_invalid_unit_configuration_fails_before_constructing_any_client(monkeypatch,value):
    def forbidden(*args,**kwargs):
        pytest.fail('must fail before a client is constructed')
    monkeypatch.setattr(two_model,'BoundedChatClient',forbidden)
    with pytest.raises(ProviderError,match='invalid_configuration'):
        two_model.TwoModelWholePagePipeline.from_env({'CORRECTION_TARGET_UNIT':value})


def test_factory_defaults_legacy_and_explicit_question_retains_new_judgment_client():
    with two_model.TwoModelWholePagePipeline.from_env({}) as pipe:
        assert pipe.target_unit=='answer_slot'
    with two_model.TwoModelWholePagePipeline.from_env({'CORRECTION_TARGET_UNIT':'question'}) as pipe:
        assert pipe.target_unit=='question' and pipe.judgment_client is pipe.visual_client


def test_worker_new_question_persists_unit_count_and_rephoto_uses_original_unit(flow):
    pipe=pipeline()
    flow.worker.pipeline=pipe
    case=flow.ready()
    with Session(flow.database.engine) as session:
        q=session.get(Entity,case['question_id'])
        assert q.document['blank_count']==2
        assert q.annotations['target_unit']=='question'
        assert q.annotations['target_context']==question()['target_context']
    # Service default can change; the saved question dictates the correction unit.
    pipe.target_unit='answer_slot'
    _,job=submit_correction(flow,case)
    claim=flow.worker.claim()
    kind,context=flow.worker._load(*claim)
    assert kind=='recognize_attempt' and context['target_unit']=='question'
    result=pipe.recognize(**context)
    assert flow.worker.finish(*claim,result=result)
    latest=current_case(flow,case['id'])
    attempt=flow.client.get('/v1/attempts/'+latest['current_attempt_id']).json()['record']
    assert attempt['answer_text']==question()['answer_text']
    assert latest['current_decision']['verdict']=='correct'
    assert pipe.target_unit=='answer_slot'


def test_worker_legacy_no_annotation_keeps_slot_and_rejects_whole_question_even_unique_stem(flow):
    flow.pipeline.label='3(1)#2'
    case=flow.ready()
    with Session(flow.database.engine) as session:
        q=session.get(Entity,case['question_id'])
        q.annotations={k:v for k,v in q.annotations.items() if k!='target_unit'}
        session.commit()
    q=correct('3(1)',stem_text='1 + 1 = ?',answer_text='2')
    pipe=pipeline([q],[q])
    flow.worker.pipeline=pipe
    _,job=submit_correction(flow,case)
    claim=flow.worker.claim()
    _,context=flow.worker._load(*claim)
    assert context['target_unit']=='answer_slot'
    result=pipe.recognize(**context)
    assert flow.worker.finish(*claim,result=result)
    latest=current_case(flow,case['id'])
    attempt=flow.client.get('/v1/attempts/'+latest['current_attempt_id']).json()['record']
    assert attempt['answer_text'] is None and attempt['recognition_status']=='needs_revision'
    assert pipe.target_unit=='question'


def test_worker_check_context_includes_persisted_question_unit(flow):
    flow.worker.pipeline=pipeline()
    case=flow.ready()
    flow.start_check(case)
    claim=flow.worker.claim()
    kind,context=flow.worker._load(*claim)
    assert kind=='check'
    assert context['question']['target_unit']=='question'
    assert context['question']['target_context']==question()['target_context']


def test_different_short_anchor_lengths_do_not_block_identical_complete_question():
    a=question()
    b=question(target_context={**a['target_context'],'anchor':'正方形的边长为a'})
    row=pipeline([a],[b]).recognize(images=(picture(),)).data['items'][0]
    assert row['assessment']['verdict']=='correct'
    a,b=bound(a),bound(question('三(一)',target_context=b['target_context']))
    result=semantic_alignment.evaluate_alignment(response(pair(a,b)),[a],[b],target_unit='question')
    assert len(result['pairs'])==1
