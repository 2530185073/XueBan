"""Synthetic cell identity tests; not handwriting accuracy or image proof."""
from copy import deepcopy
import json

import pytest
from jsonschema import ValidationError, validate
from test_reconciliation import review
from test_review_evidence import raw_visual, identity
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from test_correction_flow import flow, teacher  # noqa: F401 -- isolated HTTP/database fixture
from xueban_backend.providers import reconciliation as rec, review_evidence, question_units as units, visual_readback
from xueban_backend.providers.two_model import PAGE_SCHEMA, PAGE_PROMPT, TwoModelWholePagePipeline
from xueban_backend.providers.whole_page import PAGE_RULES


def slot_sources(*, with_position=False, same_cell=False):
    p = correct(label='3#1', stem_text='填表：乘方6^5的底数是____', answer_text='6', question_type='fill_blank',
                visible_locator={'printed_label': '3', 'page_zone': 'middle_left'})
    s = deepcopy(p); s.update(answer_text='5', verdict='incorrect', correct_answer='6')
    stem = '填表：乘方6^5的底数（保留符号）是____' if same_cell else '填表：乘方6^5的指数是____'
    v = raw_visual(p, stem_text=stem, answer_text='6' if same_cell else '5')
    if with_position:
        p['target_context'] = {'section': '3', 'row': '底数', 'column': '6^5', 'anchor': '6^5的底数'}
        s['target_context'] = deepcopy(p['target_context'])
        v['target_context'] = {'section': '3', 'row': '底数' if same_cell else '指数', 'column': '6^5', 'anchor': stem}
    return p, s, v


def bound_proposal(p, v):
    return review(source=v, label=p['label'], selected_source='visual', resolution='visual_resolution',
                  target_identity=identity(p, v), observation_issue_reviews=[],
                  visual_evidence='原目标来源记录' + p['answer_text'] + '；独立识读记录为' + v['answer_text'] + '，本次合成复核声明它属于同一目标格。')


def slot_flow(p, s, v, neighbour=None):
    def judge(request):
        t = json.loads(request['text'])['task_input']['items'][0]
        return {'items': [bound_proposal(t['primary'], t['visual'])]}
    extra = [neighbour] if neighbour else []
    return TwoModelWholePagePipeline(FakeClient('a', {'items': [p, *extra]}), FakeClient('b', {'items': [s, *extra]}),
        reconciliation_client=FakeClient('format', judge), visual_client=FakeClient('reader', {'items': [v]}),
        judgment_client=FakeClient('strong', judge))


def run(p, s, v):
    pipeline = slot_flow(p, s, v)
    return pipeline.recognize(images=(picture(),)), pipeline


def link(p, s, v):
    return review_evidence.target_link(bound_proposal(p, v), p, s, v, rec.source_id)


def assert_unknown(result, rejection="target_identity_slot_position_unconfirmed"):
    row = result.data['items'][0]
    assert row['recognition_status'] == 'needs_revision' and row['assessment']['verdict'] == 'unable_to_judge'
    assert 'model_disagreement' in row['assessment']['verification']['reason_codes']
    assert 'visual_unreadable' not in row['assessment']['verification']['reason_codes']
    app = result.data['multimodel_audit']['calls'][-1]['applications'][0]
    assert app['rejection_code'] == rejection
    assert len(result.metadata.attempts) == 4
    return row


def test_complete_fake_wrong_cell_fallback_becomes_item_unknown_without_new_call():
    p, s, v = slot_sources(); before = deepcopy((p, s, v))
    result, _ = run(p, s, v); row = assert_unknown(result)
    assert row['stem_text'] == p['stem_text'] and row['assessment']['knowledge_points'] == []
    assert (p, s, v) == before


def test_independent_same_cell_context_and_different_length_quotes_resolve():
    p, s, v = slot_sources(with_position=True, same_cell=True); before = deepcopy((p, s, v))
    result, _ = run(p, s, v); row = result.data['items'][0]
    assert row['recognition_status'] == 'ready' and row['assessment']['verdict'] == 'correct'
    binding = result.data['multimodel_audit']['calls'][-1]['applications'][0]['evidence_binding']['target_identity']
    assert binding['basis'] == 'bound_visible_locator_and_slot_position_review'
    assert binding['original_context'] == p['target_context'] and binding['selected_context'] == v['target_context']
    assert p['target_context']['anchor'] != v['target_context']['anchor']
    assert len(result.metadata.attempts) == 4 and (p, s, v) == before


def test_explicit_different_row_is_not_the_same_table_cell():
    result, _ = run(*slot_sources(with_position=True)); assert_unknown(result, "target_unit_mismatch")


@pytest.mark.parametrize('change', [
    lambda p, s, v: v.update(target_context=None),
    lambda p, s, v: p.pop('target_context'),
    lambda p, s, v: p['target_context'].update(row=None),
    lambda p, s, v: v['target_context'].update(column=None),
    lambda p, s, v: v['target_context'].update(column='6^6'),
    lambda p, s, v: v['target_context'].update(section='4'),
    lambda p, s, v: v['target_context'].update(anchor='学生正确答案为6'),
    lambda p, s, v: v['target_context'].update(anchor='填表'),
    lambda p, s, v: v['target_context'].update(row='指数'),
    lambda p, s, v: v.update(stem_text='填表：乘方6^5的指数是____', target_context={
        'section': '3', 'row': '底数', 'column': '6^5', 'anchor': '6^5'}),
])
def test_absent_unsupported_or_contradictory_independent_position_stays_unknown(change):
    p, s, v = slot_sources(with_position=True, same_cell=True); change(p, s, v)
    assert link(p, s, v) == {'clear': False, 'rejection_code': 'target_identity_slot_position_unconfirmed'}


def test_only_minimal_text_layout_is_normalized_not_numeric_or_header_equivalence():
    p, s, v = slot_sources(with_position=True, same_cell=True)
    v['target_context'].update(row='  底数  ', column=' 6^5 ', section=None)
    assert link(p, s, v)['clear']
    v['target_context']['column'] = '7776'
    assert not link(p, s, v)['clear']


def test_review_cannot_supply_a_missing_original_position_or_mutate_a_bound_source():
    p, s, v = slot_sources(with_position=True, same_cell=True)
    p.pop('target_context'); proposal = bound_proposal(p, v)
    proposal['target_identity']['original_context'] = deepcopy(v['target_context'])
    assert not review_evidence.target_link(proposal, p, s, v, rec.source_id)['clear']
    with pytest.raises(ValidationError): validate({'items': [proposal]}, rec.SCHEMA)
    p, s, v = slot_sources(with_position=True, same_cell=True); proposal = bound_proposal(p, v)
    v['target_context']['column'] = '6^6'
    assert rec.evaluate_review(proposal, p, s, visual=v)['rejection_code'] == 'source_id_mismatch'


def test_position_fields_are_optional_nullable_and_old_absent_hash_is_unchanged():
    p, s, v = slot_sources()
    assert rec.source_id('primary', p) == 'primary:79c6bccaeb91e3098f6f7be74dd4f0614d88425cd45a9ad4febcca2c8a95481a'
    for schema, item in [(PAGE_SCHEMA, p), (visual_readback.SCHEMA, v)]:
        extended = units.schema_for(schema, 'answer_slot')
        assert 'target_context' not in extended['properties']['items']['items']['required']
        validate({'items': [item]}, extended)
        validate({'items': [{**item, 'target_context': None}]}, extended)
    assert 'target_context' not in rec.observation_record(p)
    assert rec.source_id('primary', {**p, 'target_context': None}) != rec.source_id('primary', p)


def test_context_changes_source_digest_and_review_target_preserves_original_context():
    p, s, v = slot_sources(with_position=True, same_cell=True)
    target = rec.review_target('3#1', p, s, [], visual=v)
    assert target['primary']['target_context'] == p['target_context']
    assert target['visual']['target_context'] == v['target_context']
    initial = rec.source_id('visual', v); v['target_context']['anchor'] = '6^5'
    assert rec.source_id('visual', v) != initial


def test_full_printed_cell_still_resolves_without_optional_context():
    p, s, _ = slot_sources(); v = raw_visual(p)
    result, _ = run(p, s, v)
    assert result.data['items'][0]['recognition_status'] == 'ready'
    assert result.data['multimodel_audit']['calls'][-1]['applications'][0]['evidence_binding']['target_identity']['basis'] == 'full_original_printed_observation'


def test_plain_numbered_question_retains_its_existing_changed_print_fallback():
    p, s, v = slot_sources()
    for item in (p, s, v): item['label'] = '3'
    assert link(p, s, v)['basis'] == 'bound_visible_locator_review'


def test_ordinary_multi_blank_changed_print_fallback_also_stays_unknown_without_grid():
    # The restriction is not table-only: an ordinal is not physical proof for
    # a shared multi-blank question. Its unchanged full-text path still works.
    p, s, v = slot_sources()
    p['stem_text'] = s['stem_text'] = '填空：甲数比乙数多3，甲数是____，乙数是____。'
    v['stem_text'] = '填空：甲数比乙数少3，甲数是____，乙数是____。'
    assert link(p, s, v)['rejection_code'] == 'target_identity_slot_position_unconfirmed'
    assert link(p, s, raw_visual(p))['basis'] == 'full_original_printed_observation'


def test_bad_position_is_per_item_and_does_not_block_a_clear_neighbour():
    p, s, v = slot_sources()
    neighbour = correct(label='4', stem_text='计算2+2', answer_text='4')
    pipeline = slot_flow(p, s, v, neighbour)
    result = pipeline.recognize(images=(picture(),)); rows = {r['label']: r for r in result.data['items']}
    assert rows['3#1']['recognition_status'] == 'needs_revision' and rows['4']['recognition_status'] == 'ready'
    assert len(result.metadata.attempts) == 4


def test_legacy_prompts_do_not_allow_review_to_invent_position_or_infer_from_slot_number():
    page = units.page_prompt(PAGE_PROMPT, PAGE_RULES, 'answer_slot')
    visual = units.visual_prompt(visual_readback.PROMPT, 'answer_slot')
    review_prompt = units.review_prompt(rec.PROMPT, 'answer_slot')
    for prompt in (page, visual):
        assert '不能从label的#次序' in prompt and '不证明笔迹识读或答案正确' in prompt
    assert '不能由复核补造' in review_prompt and '不把它说成学生字迹不清' in review_prompt


def test_real_worker_keeps_unconfirmed_cell_as_ai_pending_without_learning_or_teacher_task(flow):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_learning import learning
    from xueban_backend.learning_models import LearningEvidence
    from xueban_backend.correction_models import Task
    pipeline = slot_flow(*slot_sources())
    flow.worker.pipeline = pipeline
    _, job = flow.recognize()
    assert flow.worker.run_once() and not flow.worker.run_once()
    assert flow.client.get('/v1/jobs/' + job['id']).json()['status'] == 'succeeded'
    learning(flow).drain()
    case = flow.client.get('/v1/cases/' + flow.homework()['case_ids'][0]).json()
    decision = case['current_decision']
    assert decision['verdict'] == 'unable_to_judge' and decision['validity'] == 'needs_review'
    assert decision['knowledge_points'] == [] and decision['correct_answer'] is None
    assert 'model_disagreement' in decision['verification']['reason_codes']
    assert 'visual_unreadable' not in decision['verification']['reason_codes']
    assert not case['review_ids'] and not case['help_request_ids']
    with Session(flow.database.engine) as session:
        assert list(session.scalars(select(LearningEvidence))) == []
        task = session.get(Task, job['id'])
        assert len(task.audit['executions'][-1]['metadata']['attempts']) == 4
    teacher(flow)
    assert flow.client.get('/v1/review-queue').json()['items'] == []
    rows = flow.client.get('/v1/review-queue?view=all').json()['items']
    assert next(row for row in rows if row['case_id'] == case['id'])['reason'] == 'ai_pending'


# V2: original sources may share the complete table text yet name different
# cells. This is a synthetic reachable identity risk, not a production incident.
def shared_table_sources(*, stem='填表：乘方2^2、2^3的底数、指数分别填空。'):
    p = correct(label='3#1', stem_text=stem, answer_text='2', question_type='fill_blank',
                visible_locator={'printed_label': '3', 'page_zone': 'middle_left'},
                target_context={'section': '3', 'row': '底数', 'column': '2^2', 'anchor': '乘方2^2'})
    return p, deepcopy(p)


def matched_table(p, s, neighbour=None):
    extra = [neighbour] if neighbour else []
    return TwoModelWholePagePipeline(FakeClient('a', {'items': [p, *extra]}),
                                    FakeClient('b', {'items': [s, *extra]}))


@pytest.mark.parametrize('axis,value', [('row', '指数'), ('column', '2^3')])
def test_complete_matched_sources_with_explicit_different_cells_stay_separate(axis, value):
    p, s = shared_table_sources(); s['target_context'].update({axis: value, 'anchor': s['stem_text']})
    before = deepcopy((p, s)); hashes = [rec.source_id(role, item) for role, item in [('primary', p), ('secondary', s)]]
    assert units.legacy_slot_positions_conflict(p, s)
    pipeline = matched_table(p, s, correct(label='4', stem_text='计算2+2', answer_text='4'))
    result = pipeline.recognize(images=(picture(),))
    pending = [r for r in result.data['items'] if r['recognition_status'] != 'ready']
    assert len(pending) == 2  # Existing conflict route preserves both observations.
    assert len(result.data['items']) == 3 and len(result.metadata.attempts) == 2
    assert all(r['assessment']['verdict'] == 'unable_to_judge' for r in pending)
    assert all('target_identity_conflict' in r['assessment']['verification']['reason_codes'] for r in pending)
    assert all('visual_unreadable' not in r['assessment']['verification']['reason_codes'] for r in pending)
    assert all(r['assessment']['knowledge_points'] == [] for r in pending)
    assert (p, s) == before
    assert hashes == [rec.source_id(role, item) for role, item in [('primary', p), ('secondary', s)]]


@pytest.mark.parametrize('axis,value', [('row', '指数'), ('column', '2^3')])
def test_full_printed_visual_review_cannot_erase_an_explicit_cell_conflict(axis, value):
    p, s = shared_table_sources(); s.update(answer_text='3', verdict='incorrect', correct_answer='2')
    v = raw_visual(p); v['target_context'].update({axis: value, 'anchor': v['stem_text']})
    before = deepcopy((p, s, v))
    assert link(p, s, v) == {'clear': False, 'rejection_code': 'target_identity_scope_conflict'}
    result, _ = run(p, s, v)
    assert_unknown(result, 'target_unit_mismatch')
    assert (p, s, v) == before


@pytest.mark.parametrize('context', [None, {}, {'section': '3', 'row': None, 'column': '2^3', 'anchor': '乘方2^2'}])
def test_incomplete_context_does_not_create_a_negative_identity_claim(context):
    p, s = shared_table_sources(); s['target_context'] = context
    assert units.same_unit_identity(p, s)


@pytest.mark.parametrize('change', [
    lambda s: s.pop('target_context'),
    lambda s: s.update(target_context=None),
    lambda s: s['target_context'].update(row=' 底数 ', column=' 2^2 ', section=None, anchor=s['stem_text']),
])
def test_missing_or_same_cell_context_keeps_existing_matched_behavior(change):
    p, s = shared_table_sources(); change(s); before = deepcopy((p, s))
    result = matched_table(p, s).recognize(images=(picture(),))
    assert result.data['items'][0]['recognition_status'] == 'ready' and len(result.metadata.attempts) == 2
    assert (p, s) == before


@pytest.mark.parametrize('first,second,stem', [
    ('2^2', '2^3', '填表：乘方2^2、2^3的底数分别填空。'),
    ('6^5', '(-5)^4', '填表：乘方6^5与(-5)^4，分别填写底数和指数。'),
    ('(-1/2)^3', '2^4', '底数：乘方(-1/2)^3，2^4。'),
])
def test_only_complete_explicit_numeric_power_list_proves_separate_columns(first, second, stem):
    p, s = shared_table_sources(stem=stem)
    for item, column in ((p, first), (s, second)):
        item['target_context'].update(column=column, anchor=stem)
    assert units.legacy_slot_positions_conflict(p, s)


@pytest.mark.parametrize('first,second,stem', [
    ('2^2', '2^3', '底数说明：先计算2^2，之后在另一句提到2^3。'),
    ('2^2', '2^3', '底数：12^2、2^3。'),
    ('2^2', '2^3', '底数：2^2、2^30。'),
    ('2^2', '2^3', '底数：2^2+2^3。'),
    ('2^2', '2^3', '底数：2^2=2^3。'),
    ('2^2', '2^3', '底数：2^2、2^3+x。'),
    ('2', '3', '底数说明：2、3。'),
    ('n^2', 'n^3', '底数说明：n^2、n^3。'),
    ('2^2', '2^3', '底数：2^2、2^3；另表2^2、2^3。'),
])
def test_different_numbers_or_non_headers_do_not_imply_physical_columns(first, second, stem):
    p, s = shared_table_sources(stem=stem)
    for item, column in ((p, first), (s, second)):
        item['target_context'].update(column=column, anchor=stem)
    assert not units.legacy_slot_positions_conflict(p, s)


@pytest.mark.parametrize('first,second', [('底数', 'base'), ('数', '指数'), ('行甲', '行乙')])
def test_arbitrary_or_substring_row_names_are_not_automatically_opposites(first, second):
    stem = f'填表：乘方2^2的说明含{first}、{second}，标题也可能只是不同措辞。'
    p, s = shared_table_sources(stem=stem)
    for item, row in ((p, first), (s, second)):
        item['target_context'].update(row=row, anchor=stem)
    assert units.same_unit_identity(p, s)


def test_new_negative_gate_only_applies_to_two_legacy_subtargets():
    p, s = shared_table_sources(); s['target_context'].update(row='指数')
    p['label'] = '3'
    assert units.same_unit_identity(p, s)
    p['label'] = '3#1'; s['target_context']['anchor'] = 'unbound'
    assert units.same_unit_identity(p, s)


def test_same_cell_nfc_anchor_and_layout_do_not_create_conflict():
    p, s = shared_table_sources(stem='Cafe\u0301表：乘方2^2的底数。')
    s['target_context'].update(section=' 3 ', row=' 底数 ', column='2^2 ', anchor=s['stem_text'])
    assert units.same_unit_identity(p, s)


def test_real_worker_retains_both_conflicting_sources_as_ai_pending_without_learning(flow):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_learning import learning
    from xueban_backend.learning_models import LearningEvidence
    from xueban_backend.correction_models import Task
    p, s = shared_table_sources(); s['target_context'].update(row='指数', anchor=s['stem_text'])
    flow.worker.pipeline = matched_table(p, s)
    _, job = flow.recognize(); assert flow.worker.run_once()
    learning(flow).drain()
    cases = [flow.client.get('/v1/cases/' + cid).json() for cid in flow.homework()['case_ids']]
    assert len(cases) == 2
    assert all(c['current_decision']['verdict'] == 'unable_to_judge' for c in cases)
    assert all(c['current_decision']['knowledge_points'] == [] for c in cases)
    assert all(not c['review_ids'] and not c['help_request_ids'] for c in cases)
    with Session(flow.database.engine) as session:
        assert list(session.scalars(select(LearningEvidence))) == []
        assert len(session.get(Task, job['id']).audit['executions'][-1]['metadata']['attempts']) == 2
    teacher(flow)
    assert flow.client.get('/v1/review-queue').json()['items'] == []
    assert all(row['reason'] == 'ai_pending' for row in flow.client.get('/v1/review-queue?view=all').json()['items'])


@pytest.mark.parametrize('label', ['３＃１', '第３题＃１', '【3】＃１', ' 3 # 1。'])
def test_label_typography_cannot_hide_an_explicit_cell_conflict(label):
    p, s = shared_table_sources(); p['label'] = s['label'] = label
    s['target_context'].update(row='指数', anchor=s['stem_text'])
    assert units.legacy_slot_positions_conflict(p, s)
    result = matched_table(p, s).recognize(images=(picture(),))
    assert len(result.data['items']) == 2 and len(result.metadata.attempts) == 2
    assert all(row['recognition_status'] == 'needs_revision' for row in result.data['items'])
    v = raw_visual(s)
    assert link(p, p, v)['rejection_code'] == 'target_identity_scope_conflict'
