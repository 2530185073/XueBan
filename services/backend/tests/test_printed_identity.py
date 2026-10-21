"""Counterexamples for full printed identity; fixtures are not math accuracy gold."""
from copy import deepcopy
import pytest

from test_reconciliation import review
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers import reconciliation as rec, review_evidence
from xueban_backend.providers.printed_identity import full_printed_presentation_rule as rule
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def choice_pair():
    a = correct('5', question_type='single_choice', stem_text='下列各式结果相等的是____', answer_text='A',
                options=[{'label': 'A', 'text': '-2^2与(-2)^2'},
                         {'label': 'B', 'text': '-1^2026与(-1)^2025'},
                         {'label': 'C', 'text': '(2/3)^2与2^2/3'},
                         {'label': 'D', 'text': '-(-3)与-|-3|'}])
    v = {key: deepcopy(a[key]) for key in ['label', 'stem_text', 'options', 'answer_text', 'answer_status', 'uncertainties']}
    v.update(stem_text='下列各式结果相等的是（　）。', answer_text='A\n下方另写：改：B')
    return a, v


def power_pair():
    a = correct('8(2)#1', question_type='fill_blank', stem_text='若n为正整数，则-1^(2n)=____。', answer_text='1', options=[])
    v = {key: deepcopy(a[key]) for key in ['label', 'stem_text', 'options', 'answer_text', 'answer_status', 'uncertainties']}
    v['stem_text'] = '若n为正整数，则－1²ⁿ＝______。'
    return a, v


@pytest.mark.parametrize('make,expected', [(choice_pair, 'single_choice_terminal_slot_v1'),
                                         (power_pair, 'explicit_superscript_scope_v1')])
def test_full_printed_source_link_changes_no_observation_or_hash(make, expected):
    a, v = make(); before = deepcopy((a, v)); ids = rec.source_id('primary', a), rec.source_id('visual', v)
    assert rule(a, v) == expected
    link = review_evidence.target_link({}, a, a, v, rec.source_id)
    assert link['clear'] and link['presentation_rule'] == expected
    assert link['original_source_id'] == ids[0] and link['selected_source_id'] == ids[1]
    assert link['original_observation']['stem_text'] == a['stem_text']
    assert link['selected_observation']['options'] == v['options']
    assert (a, v) == before and ids == (rec.source_id('primary', a), rec.source_id('visual', v))


@pytest.mark.parametrize('stem', [
    '下列各式结果不相等的是（　）。', '下列各式结果相等的是（A）。',
    '下列各式结果相等的是（　），限正整数。', '下列各式结果相等的是（　）（　）。',
    '下列____各式结果相等的是（　）。', '下列各式结果相等的是____（　）。',
    '下列各式结果相等的是____，至少选2项。', '下列各式结果相等的是（　）..',
    '下列各式结果相等的是f（　）。', '下列各式结果相等的是（　）+1',
])
def test_choice_nonterminal_populated_multiple_and_condition_drift_rejected(stem):
    a, v = choice_pair(); v['stem_text'] = stem
    assert rule(a, v) is None


@pytest.mark.parametrize('change', [
    lambda a, v: a.update(question_type='unknown'),
    lambda a, v: a.update(question_type='multiple_choice'),
    lambda a, v: v.update(question_type='multiple_choice'),
    lambda a, v: v.update(options=[]),
    lambda a, v: v['options'].pop(),
    lambda a, v: v['options'].reverse(),
    lambda a, v: v['options'][2].update(text='(2/3)^2与2^3/3'),
    lambda a, v: v['options'][0].update(text='(-2)^2与(-2)^2'),
    lambda a, v: v['options'][1].update(label='A'),
    lambda a, v: v.update(label='6'),
    lambda a, v: v.update(label='5#1'),
    lambda a, v: v.update(target_unit='question'),
    lambda a, v: [a.update(source_asset_id='a'), v.update(source_asset_id='b')],
    lambda a, v: [a.update(visible_locator={'printed_label':'5','page_zone':'middle'}), v.update(visible_locator={'printed_label':'6','page_zone':'middle'})],
])
def test_choice_full_options_and_target_identity_are_required(change):
    a, v = choice_pair(); change(a, v)
    assert rule(a, v) is None


@pytest.mark.parametrize('body', [
    '-1^2n', '(-1)^(2n)', '-1^(2*n+1)', '-1^2*n', '-1^(2n+1)', '-1^(n2)',
    '-1^(n+n)', '-1^2', '(-1)²ⁿ', '-1²ⁿ⁺¹', '-1²*ⁿ', '-1² ⁿ', '-1²ⁿᵐ',
    '-1^(2n)ᵐ', '-1^(2n)₂', '-1^²ⁿ', '-1^(2n)/2', '2^n', '2n',
])
def test_power_scope_and_operator_drift_never_link(body):
    a, v = power_pair(); a['stem_text'] = '若n为正整数，则' + body + '=____。'
    assert rule(a, v) is None


@pytest.mark.parametrize('change', [
    lambda a, v: a.update(stem_text='若n为负整数，则-1^(2n)=____。'),
    lambda a, v: v.update(stem_text='若n为正整数，则－1²ⁿ＝______，n>2。'),
    lambda a, v: a.update(question_type='single_choice'),
    lambda a, v: v.update(label='8(2)#2'),
    lambda a, v: v.update(options=[{'label':'A','text':'1'}]),
])
def test_power_entire_condition_question_and_slot_stay_bound(change):
    a, v = power_pair(); change(a, v)
    assert rule(a, v) is None


def test_explicit_secondary_is_used_bare_primary_remains_ambiguous():
    secondary, visual = power_pair(); primary = {**secondary, 'stem_text': '若n为正整数，则-1^2n=____', 'answer_text': '-1'}
    assert rule(primary, visual) is None
    before = deepcopy((primary, secondary, visual))
    proposal = review(source=visual, label=visual['label'], selected_source='visual', resolution='visual_resolution',
                      verdict='incorrect', correct_answer='-1', correction_method='按原题的幂范围检查符号。')
    out = rec.evaluate_review(proposal, primary, secondary, visual=visual)
    assert out['resolved'] and out['requires_visual_support']
    assert out['audit']['target_identity']['original_source_id'] == rec.source_id('secondary', secondary)
    assert out['source']['answer_text'] == '1' and out['verdict'] == 'incorrect'
    assert (primary, secondary, visual) == before and 'target_identity' not in proposal


@pytest.mark.parametrize('change', [
    lambda r: r.update(source_id='visual:forged'),
    lambda r: r.update(answer_text='-1'),
    lambda r: r.update(judgment_basis=None),
    lambda r: r['judgment_basis'].update(condition_quotes=[]),
    lambda r: r.update(same_question=False),
    lambda r: r.update(observation_basis='semantic_only'),
    lambda r: r.update(remaining_uncertainties=['未能核实']),
])
def test_printed_identity_does_not_replace_any_review_evidence_gate(change):
    a, v = power_pair()
    proposal = review(source=v, label=v['label'], selected_source='visual', resolution='visual_resolution')
    change(proposal)
    assert rec.evaluate_review(proposal, a, a, visual=v)['resolved'] is False


def test_missing_review_still_pending_and_no_model_judgment_is_invented():
    a, v = power_pair(); b = {**a, 'stem_text': v['stem_text'], 'answer_text':'-1'}
    flow = TwoModelWholePagePipeline(FakeClient('a', {'items':[a]}), FakeClient('b', {'items':[b]}))
    result = flow.recognize(images=(picture(),))
    assert result.data['items'][0]['recognition_status'] == 'needs_revision'
    assert len(result.metadata.attempts) == 2


def test_actual_visual_call_and_review_are_both_required_to_apply():
    a, v = power_pair(); b = {**a, 'answer_text':'-1'}
    flow = TwoModelWholePagePipeline(FakeClient('a', {'items':[a]}), FakeClient('b', {'items':[b]}))
    row = flow.recognize(images=(picture(),)).data['items'][0]
    proposal = review(source=v, label=v['label'], selected_source='visual', resolution='visual_resolution',
                      verdict='incorrect', correct_answer='-1', correction_method='按原题的幂范围检查符号。')
    call = {'stage':'visual_adjudication','code':'ok','model':'judge','items':{v['label']:proposal}}
    missing = deepcopy(row)
    assert flow._apply_review(missing, deepcopy(call), a, b)['resolved'] is False
    visual_call = {'stage':'visual_readback','code':'ok','model':'reader','items':{v['label']:v}}
    outcome = flow._apply_review(row, call, a, b, visual_call)
    assert outcome['resolved'] and row['recognition_status'] == 'ready'
    assert row['answer_text'] == '1' and row['assessment']['verdict'] == 'incorrect'
    assert row['assessment']['knowledge_points'] == []


@pytest.mark.parametrize('stem', [
    '若n为正整数，则-1^2n=____',
    '若n为正整数，则(-1)^(2n)=____',
    '若n为正整数，则-1^2*n=____',
    '若n为正整数，则-1^(2*n+1)=____',
])
def test_no_explicit_matching_original_means_no_identity_release(stem):
    a, v = power_pair(); a['stem_text'] = stem
    proposal = review(source=v, label=v['label'], selected_source='visual', resolution='visual_resolution',
                      verdict='incorrect', correct_answer='-1', correction_method='检查符号。')
    out = rec.evaluate_review(proposal, a, deepcopy(a), visual=v)
    assert out['resolved'] is False and out['rejection_code'] == 'target_identity_unconfirmed'
