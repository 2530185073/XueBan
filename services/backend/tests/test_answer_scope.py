"""No model requests: explicit counterexamples for a narrow coverage veto."""
from copy import deepcopy
import json

import pytest

from test_reconciliation import review, identity_evidence
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from test_visual_readback import item as visual_item
from xueban_backend.providers import answer_scope
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers.reconciliation import evaluate_review, source_id
from xueban_backend.providers.two_model import TwoModelWholePagePipeline
from xueban_backend.staff import needs_staff, case_queue_reason
from xueban_backend.learning_rules import model_decision_usable

STEM = '(-24)÷6=____(24÷6)=____'


def incomplete(**changes):
    return correct('1(3)', stem_text=STEM, answer_text='4', correct_answer=None,
                   correction_method=None, reason_code=None,
                   visible_locator={'printed_label': '1(3)', 'page_zone': 'top_left'}, **changes)


def pipeline(first=None, second=None, visual=None, reviewer=False):
    a, b = first or incomplete(), second or incomplete()
    def forbidden(_):
        pytest.fail('scope-incomplete targets must not dispatch paid adjudication')
    return TwoModelWholePagePipeline(
        FakeClient('primary-distinct', {'items': [a]}),
        FakeClient('secondary-distinct', {'items': [b]}),
        reconciliation_client=FakeClient('reconciler', forbidden) if reviewer else None,
        visual_client=FakeClient('visual-distinct', visual) if visual is not None else None,
    )


@pytest.mark.parametrize('answer', ['4', '-4', '4.0', '2/3', '(-4)', '  －４  '])
def test_numeric_single_answer_does_not_cover_two_explicit_positions(answer):
    source = incomplete()
    source['answer_text'] = answer
    before = deepcopy(source)
    identity = source_id('primary', source)
    assert answer_scope.candidate_positions(source) == 2 and answer_scope.scope_gap(source)
    assert source == before and source_id('primary', source) == identity


@pytest.mark.parametrize('changes', [
    {'label': '1(3)#1'}, {'label': '1(3)＃２'},
    {'stem_text': '若边长为a，则面积____，周长____'},
    {'stem_text': '证明：(-24)÷6=____(24÷6)=____'},
    {'stem_text': 'x÷6=____(x÷6)=____'},
    {'stem_text': '(-24)÷6=____ ____=4'},
    {'stem_text': '(-24)÷6=____\n____=4'},
    {'stem_text': '(-24)÷6=____'},
    {'stem_text': '(-24)÷6=____(24÷6)=____('},
    {'answer_text': '-；-4'}, {'answer_text': '第1空：-；第2空：-4'},
    {'answer_text': '(1)- (2)-4'}, {'answer_text': '-\n-4'},
    {'answer_status': 'blank', 'answer_text': None},
    {'target_unit': 'question', 'blank_count': 2, 'unit_complete': True,
     'answer_text': '第1空：-；第2空：-4'},
])
def test_unknown_or_already_explicit_scopes_gain_no_new_restriction(changes):
    source = incomplete(); source.update(changes)
    assert not answer_scope.scope_gap(source)


def test_same_label_double_agreement_no_longer_means_complete_or_correct():
    source = incomplete(); before = deepcopy(source)
    result = pipeline(source, deepcopy(source)).recognize(images=(picture(),))
    row = result.data['items'][0]
    assert row['recognition_status'] == 'needs_revision'
    assert row['assessment']['verdict'] == 'unable_to_judge'
    assert row['assessment']['verification']['reason_codes'] == [answer_scope.REASON]
    assert row['assessment']['knowledge_points'] == []
    assert row['assessment']['correct_answer'] is row['assessment']['correction_method'] is None
    assert row['answer_text'] == '4' and row['answer_status'] == 'present'
    assert row['assessment']['basis_summary'] == answer_scope.SUMMARY
    assert len(result.metadata.attempts) == 2
    calls = result.data['multimodel_audit']['calls']
    assert len(calls) == 2
    for call in calls:
        assert call['items'] == [before]
        assert source_id(call['stage'], call['items'][0]) == source_id(call['stage'], before)
    assert source == before


@pytest.mark.parametrize('observed', [
    {'answer_text': '4'},
    {'answer_text': '第1空：-；第2空：-4'},
    {'answer_text': '-；-4'},
    {'answer_text': '[不清]', 'answer_status': 'unclear', 'uncertainties': ['位置未全记录']},
    ProviderError('deadline_exceeded'),
])
def test_scope_only_targets_do_not_dispatch_unusable_rescue_calls(observed):
    source = incomplete()
    visual = (observed if isinstance(observed, ProviderError)
              else {'items': [visual_item('1(3)', stem_text=STEM, **observed)]})
    flow = pipeline(source, deepcopy(source), visual, reviewer=True)
    result = flow.recognize(images=(picture(),))
    row = result.data['items'][0]
    assert row['recognition_status'] == 'needs_revision'
    assert row['assessment']['verdict'] == 'unable_to_judge'
    assert 'visual_unreadable' not in row['assessment']['verification']['reason_codes']
    assert row['assessment']['basis_summary'] == answer_scope.SUMMARY
    assert not flow.visual_client.calls and not flow.reconciliation_client.calls
    assert len(result.metadata.attempts) == 2
    audit = result.data['multimodel_audit']
    assert [call['stage'] for call in audit['calls']] == ['primary', 'secondary']
    dispatch = audit['scope_dispatches'][0]
    assert dispatch['decision'] == 'not_dispatched'
    assert dispatch['reason'] == 'scope_resolution_unavailable'
    assert dispatch['automatic_recovery'] is False
    assert dispatch['source_ids'] == [source_id('primary', source), source_id('secondary', source)]
    assert 'usage' not in dispatch and 'attempts' not in dispatch


def test_disabled_reader_does_not_start_pointless_equivalence_review():
    flow = pipeline(reviewer=True)
    result = flow.recognize(images=(picture(),))
    assert result.data['items'][0]['recognition_status'] == 'needs_revision'
    assert not flow.reconciliation_client.calls and len(result.metadata.attempts) == 2


@pytest.mark.parametrize('resolution', ['equivalent', 'non_material', 'visual_resolution'])
@pytest.mark.parametrize('selected', ['primary', 'secondary', 'visual'])
def test_review_claims_cannot_clear_known_scope_gap(resolution, selected):
    source = incomplete()
    visual = deepcopy(source); visual['answer_text'] = '第1空：-；第2空：-4'
    proposal = review(source=visual if selected == 'visual' else source, label='1(3)',
                      selected_source=selected, resolution=resolution)
    if selected == 'visual':
        proposal['target_identity'] = identity_evidence(source, visual)
    result = evaluate_review(proposal, source, deepcopy(source), visual=visual)
    assert result['resolved'] is False and result['rejection_code'] == answer_scope.REASON


def test_scope_gap_is_ai_pending_without_learning_or_mandatory_staff_task():
    row = pipeline().recognize(images=(picture(),)).data['items'][0]
    decision = {'origin': 'model', 'validity': 'needs_review', 'verdict': 'unable_to_judge',
                'reason_code': 'verification_conflict', 'verification': row['assessment']['verification']}
    attempt = {'recognition_status': row['recognition_status'], 'answer_status': row['answer_status'],
               'recognition_uncertainties': row['uncertainties']}
    case = {'lifecycle': 'active', 'progress': 'pending', 'current_decision': decision, 'check_ids': []}
    assert not model_decision_usable(decision)
    assert needs_staff(case, attempt) is None
    assert case_queue_reason(case, attempt) == 'ai_pending'


def test_unaffected_single_slot_and_complete_multi_answer_rows_keep_existing_release():
    sources = [incomplete(), correct('2', stem_text='(-24)÷6=____', answer_text='-4'),
               correct('3#1', stem_text=STEM, answer_text='-'),
               correct('4', stem_text=STEM, answer_text='-；-4'),
               correct('5', stem_text=STEM, answer_text='第1空：-；第2空：-4')]
    flow = TwoModelWholePagePipeline(FakeClient('primary', {'items': deepcopy(sources)}),
                                    FakeClient('secondary', {'items': deepcopy(sources)}))
    result = flow.recognize(images=(picture(),))
    assert [row['recognition_status'] for row in result.data['items']] == ['needs_revision'] + ['ready']*4
    assert len(result.metadata.attempts) == 2


def test_final_r1_apply_gate_rechecks_raw_scope_even_if_a_legacy_provider_claims_ready():
    from xueban_backend.recognition_recheck import ready
    source = incomplete()
    item = {**source, 'recognition_status': 'ready', 'assessment': {
        'verdict': 'correct', 'uncertainties': [], 'verification': {
            'mode': 'two_model', 'status': 'matched', 'independent_readback': True, 'reason_codes': []}}}
    assert ready(item) is False
    item['label'] = '1(3)#2'
    assert ready(item) is True


def test_targeted_original_recheck_keeps_known_scope_gap_pending_without_extra_calls():
    from test_original_recheck_provider import context
    flow = pipeline(reviewer=True, visual={'items': []})
    request = context(); request['question']['label'] = '1(3)'
    result = flow.rerecognize_original(**request)
    assert result.data['items'][0]['recognition_status'] == 'needs_revision'
    assert answer_scope.REASON in result.data['items'][0]['assessment']['verification']['reason_codes']
    assert len(result.metadata.attempts) == 2
    assert not flow.visual_client.calls and not flow.reconciliation_client.calls
    assert result.prompt_version.endswith('.original-recheck-r1')


def test_cross_label_scope_only_alignment_is_not_dispatched_or_counted_as_a_call():
    a, b = incomplete(), incomplete()
    b['label'] = '1(4)'; b['visible_locator']['printed_label'] = '1(4)'
    flow = pipeline(a, b, visual={'items': []}, reviewer=True)
    result = flow.recognize(images=(picture(),))
    assert len(result.data['items']) == 2
    assert all(row['recognition_status'] == 'needs_revision' for row in result.data['items'])
    assert not flow.reconciliation_client.calls and not flow.visual_client.calls
    assert len(result.metadata.attempts) == 2
    audit = result.data['multimodel_audit']
    assert [call['stage'] for call in audit['calls']] == ['primary', 'secondary']
    dispatch = next(row for row in audit['scope_dispatches'] if row.get('stage') == 'semantic_alignment')
    assert dispatch['decision'] == 'not_dispatched' and dispatch['reason'] == 'scope_resolution_unavailable'
    assert dispatch['candidate_edges'] == 1 and dispatch['source_ids'] == [source_id('primary', a), source_id('secondary', b)]


def test_one_normal_pair_retains_the_entire_alignment_request_including_scope_rows():
    from xueban_backend.providers.semantic_alignment import build_alignment_input
    a, b = incomplete(), incomplete()
    b['label'] = '1(4)'; b['visible_locator']['printed_label'] = '1(4)'
    c = correct('二、1', stem_text='根据要求计算完整算式24÷6的值', answer_text='4')
    d = correct('二、2', stem_text='根据要求计算完整算式24÷6的值', answer_text='4')
    first, second = [a, c], [b, d]
    def align(request):
        task = json.loads(request['text'])['task_input']
        assert task == build_alignment_input(first, second)
        assert len(task['primary']) == len(task['secondary']) == 2
        return {'pairs': [], 'split_required': [],
                'unmatched_source_ids': [row['source_id'] for role in ('primary', 'secondary') for row in task[role]]}
    flow = TwoModelWholePagePipeline(FakeClient('primary-distinct', {'items': first}),
        FakeClient('secondary-distinct', {'items': second}), reconciliation_client=FakeClient('reviewer', align))
    result = flow.recognize(images=(picture(),))
    assert len(flow.reconciliation_client.calls) == 1 and len(result.metadata.attempts) == 3
    assert any(call['stage'] == 'semantic_alignment' for call in result.data['multimodel_audit']['calls'])


def test_late_review_application_cannot_restore_a_known_incomplete_scope():
    source = incomplete(); before = deepcopy(source)
    flow = pipeline()
    row = flow.recognize(images=(picture(),)).data['items'][0]
    proposal = review(source=source, label='1(3)', resolution='equivalent')
    call = {'code': 'ok', 'model': 'late-reviewer', 'items': {'1(3)': proposal}}
    outcome = flow._apply_review(row, call, source, deepcopy(source))
    assert not outcome['resolved'] and outcome['rejection_code'] == answer_scope.REASON
    assert row['recognition_status'] == 'needs_revision'
    assert row['assessment']['verdict'] == 'unable_to_judge'
    assert row['answer_text'] == '4' and row['answer_status'] == 'present'
    assert row['assessment']['knowledge_points'] == [] and source == before
    assert call['applications'][0]['resolved'] is False
