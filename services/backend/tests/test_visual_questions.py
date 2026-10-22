"""Whole-page recognition, graph evidence and billable-attempt regressions."""
import base64
from copy import deepcopy
import io

import httpx
from PIL import Image
import pytest

from test_correction_pipeline import checked, context, envelope, picture, pipeline, recognition, region
from xueban_backend.providers import ProviderError
from xueban_backend.providers.correction_pipeline import PipelineImage, _include_visual_solution
from xueban_backend.providers.visual_questions import complete_layout_regions, crop_question, reading_order
from xueban_backend.providers.page_detail import detailed_page


def page(width=200, height=100):
    image = Image.new('RGB', (width, height), 'red')
    image.paste('blue', (width // 2, 0, width, height))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return PipelineImage('asset_original', 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode(), width, height)


def diagram(**changes):
    return {'kind': 'bar_model', 'printed_text': '看图列式。',
            'description': '整体120页，左侧占3/5，求剩余页数。',
            'quantities': [{'value': '120', 'unit': '页', 'refers_to': '全段'},
                           {'value': '3/5', 'unit': None, 'refers_to': '左侧部分占整体'}],
            'relationships': ['右侧为剩余部分'], 'target': '剩余页数',
            'sufficient': True, 'uncertainties': [], **changes}


def payload(request):
    import json
    return json.loads(request['messages'][1]['content'][0]['text'])['task_input']


def visual_evidence():
    return {'brackets': [{'label': '120页', 'position': '最下方', 'covers': '整体'}],
            'equal_parts': '整体5份，剩余2份', 'unit_whole': '总页数', 'whole_is_known': True,
            'equation': '120*(1-3/5)', 'result': '48页'}


@pytest.mark.parametrize(('answer', 'equation', 'result'), [
    ('1. 120页；2. 已知；列式：120×（1－3/5）＝48（页）。',
     '120×(1-3/5)=48（页）', '48页'),
    ('正确答案：客车42辆；列式35÷(1－1/6)＝42（辆）。',
     '35÷(1-1/6)=42（辆）', '客车有42辆'),
    ('答案：梨树32棵；列式24÷(3/4)=32（棵）。',
     '24÷(3/4)', '梨树有32棵'),
])
def test_visual_solution_is_not_duplicated_when_model_uses_equivalent_format(answer, equation, result):
    raw = {'correct_answer': answer,
           'visual_evidence': {'equation': equation, 'result': result}}
    assert _include_visual_solution(raw) is False
    assert raw['correct_answer'] == answer


def test_visual_solution_is_added_once_when_model_omits_the_working():
    raw = {'correct_answer': '1. 梨树；2. 未知；3. 除',
           'visual_evidence': {'equation': '24÷(3/4)=32', 'result': '32棵'}}
    assert _include_visual_solution(raw) is True
    assert _include_visual_solution(raw) is False
    assert raw['correct_answer'].count('列式：') == 1


def test_crop_keeps_only_target_pixels_with_bounded_margin_and_original_regions():
    bounds = [region(x=10, y=10, width=60, height=60)]
    original = deepcopy(bounds)
    url, width, height = crop_question(page(), bounds)
    with Image.open(io.BytesIO(base64.b64decode(url.split(',')[1]))) as crop:
        assert crop.size == (width, height) == (152, 152)
        assert crop.getextrema() == ((255, 255), (0, 0), (0, 0))
    assert bounds == original


def test_dual_column_tilt_does_not_swap_question_order_or_merge_different_pages():
    items = [
        {'page': 1, 'label': '2', 'region': {'x': 200, 'y': 90, 'width': 100, 'height': 120}},
        {'page': 1, 'label': '1', 'region': {'x': 0, 'y': 100, 'width': 100, 'height': 120}},
        {'page': 1, 'label': '3', 'region': {'x': 0, 'y': 230, 'width': 100, 'height': 120}},
        {'page': 2, 'label': '4', 'region': {'x': 0, 'y': 0, 'width': 100, 'height': 120}},
    ]
    assert [item['label'] for item in reading_order(items)] == ['1', '2', '3', '4']


def test_tight_layout_protects_right_fraction_and_bottom_answers_without_adjacent_question():
    image = page(810, 1440)
    boxes = [
        {'page': 1, 'label': '1', 'region': region(x=55, y=290, width=290, height=300)},
        {'page': 1, 'label': '2', 'region': region(x=415, y=290, width=340, height=290)},
        {'page': 1, 'label': '3', 'region': region(x=55, y=595, width=300, height=270)},
        {'page': 1, 'label': '4', 'region': region(x=415, y=595, width=340, height=300)},
        {'page': 1, 'label': '5', 'region': region(x=55, y=890, width=300, height=290)},
        {'page': 1, 'label': '6', 'region': region(x=415, y=890, width=340, height=290)},
    ]
    expanded = complete_layout_regions(boxes, {image.asset_id: image})
    third, fourth, fifth = [expanded[i]['region'] for i in (2, 3, 4)]
    assert 390 < third['x'] + third['width'] < 415
    assert fourth['x'] + fourth['width'] == 810
    assert fifth['x'] == 0 and fifth['y'] + fifth['height'] > 1260
    assert third['y'] + third['height'] < 890
    assert boxes[4]['region']['x'] == 55  # Keep original model evidence intact.


def test_whole_page_is_unchanged_and_more_than_four_questions_need_only_one_call():
    source = page(400, 400)
    items = [{**recognition()['items'][0], 'label': str(i+1), 'answer_text': str(i)} for i in range(8)]
    requests = []
    with pipeline([{'items': items}], requests) as flow:
        result = flow.recognize(images=(source,))
    assert len(requests) == len(result.metadata.attempts) == 1
    sent = requests[0]['messages'][1]['content']
    assert len(sent) == 2
    assert sent[1]['image_url']['url'] == detailed_page(source).data_url
    assert sent[1]['image_url']['detail'] == 'high'
    assert [i['answer_text'] for i in result.data['items']] == [str(i) for i in range(8)]
    assert all(i['regions'] == [region(width=400, height=400)] for i in result.data['items'])
    assert 'layout_units' not in payload(requests[0])


def test_diagram_and_student_answer_are_read_together_without_extra_call():
    data = recognition()
    stem = '单位“1”是____；单位“1”____；用____法。'
    answer = '16÷4/8−3/8=128；3/5÷45=75'
    data['items'][0].update(has_diagram=True, diagram=diagram(), stem_text=stem,
                            answer_text=answer, blank_count=0,
                            answer_slots=[{'index': 1, 'prompt': '完整作答', 'answer_text': answer, 'answer_status': 'present'}])
    requests = []
    with pipeline([data], requests) as flow:
        result = flow.recognize(images=(picture(),))
    item = result.data['items'][0]
    assert item['stem_text'].startswith(stem)
    assert diagram()['description'] in item['stem_text']
    assert item['answer_text'] == answer
    assert item['diagram'] == diagram()
    assert len(result.metadata.attempts) == len(requests) == 1


def test_multi_page_output_keeps_page_order_and_order_within_each_page():
    data = {'items': [{**recognition()['items'][0], 'page': p, 'label': label}
                      for p, label in [(2, '9'), (1, '3'), (1, '2'), (2, '1')]]}
    with pipeline([data]) as flow:
        result = flow.recognize(images=(picture(), picture('asset_next')))
    assert [i['label'] for i in result.data['items']] == ['3', '2', '9', '1']
    assert [i['asset_id'] for i in result.data['items']] == ['asset_original']*2 + ['asset_next']*2


@pytest.mark.parametrize('verified', [True, False])
def test_check_requires_graph_verification_and_preserves_answer(verified):
    ctx = context()
    ctx['question']['diagram'] = diagram()
    result_data = checked(correct_answer='48页', correction_method='计算120×(1−3/5)。', visual_evidence=visual_evidence())
    if verified is not None:
        result_data['diagram_verified'] = verified
    requests = []
    with pipeline([result_data], requests) as flow:
        result = flow.check(**ctx)
    assert result.data['readback']['answer_text'] == '-4'
    assert 'diagram_observation' not in payload(requests[0])
    assert 'stem_text' not in payload(requests[0])['provisional_transcription']
    if verified:
        assert result.data['verdict'] == 'incorrect'
        assert '48页' in result.data['correct_answer']
        assert '列式：120*(1-3/5)=48页' in result.data['correct_answer']
    else:
        assert result.data['verdict'] == 'unable_to_judge'
        assert result.data['correct_answer'] is None
        assert result.data['correction_method'] is None


def test_legacy_graph_recheck_reads_full_pages_and_binds_only_current_answer():
    ctx = context()
    source = page()
    ctx['question']['stem_text'] = '如图，求剩余页数。'
    ctx['attempt'].update(asset_ids=['asset_new'], answer_regions=[])
    ctx['images'] = (source, picture('asset_new'), picture('asset_unrelated'))
    requests = []
    with pipeline([checked(diagram_verified=True, visual_evidence=visual_evidence())], requests) as flow:
        result = flow.recheck(**ctx)
    assert len(requests) == 1
    sent = requests[0]['messages'][1]['content']
    assert [item['image_url']['url'] for item in sent[1:]] == [detailed_page(source).data_url, picture('asset_new').data_url]
    assert payload(requests[0])['attempt_asset_ids'] == ['asset_new']
    assert payload(requests[0])['question_asset_ids'] == ['asset_original']
    assert 'diagram_observation' not in payload(requests[0])
    assert result.data['readback']['answer_text'] == '-4'


def test_whole_page_failure_keeps_the_single_billable_attempt():
    with pipeline([httpx.Response(200, json=envelope({'items': 'invalid'}))]) as flow, pytest.raises(ProviderError) as error:
        flow.recognize(images=(picture(),))
    assert len(error.value.attempts) == 1
    assert error.value.attempts[0].usage.total_tokens == 130


def test_truncated_graph_check_does_not_downgrade_to_an_unverified_answer():
    ctx = context()
    ctx['question']['diagram'] = diagram()
    requests = []
    truncated = httpx.Response(200, json=envelope(checked(), finish='length'))
    with pipeline([truncated], requests) as flow, pytest.raises(ProviderError) as error:
        flow.check(**ctx)
    assert len(requests) == len(error.value.attempts) == 1
    assert error.value.code == 'incomplete_output'
    assert error.value.attempts[0].usage.total_tokens == 130


def test_truncated_graph_fallback_failure_does_not_loop_or_lose_audit():
    ctx = context()
    ctx['question']['diagram'] = diagram()
    truncated = httpx.Response(200, json=envelope(checked(), finish='length'))
    with pipeline([truncated, truncated]) as flow, pytest.raises(ProviderError) as error:
        flow.check(**ctx)
    assert error.value.code == 'incomplete_output'
    assert len(error.value.attempts) == 1


def test_missing_graph_verification_is_rejected_with_paid_audit():
    ctx = context()
    ctx['question']['diagram'] = diagram()
    with pipeline([checked()]) as flow, pytest.raises(ProviderError) as error:
        flow.check(**ctx)
    assert error.value.code == 'invalid_output'
    assert error.value.attempts[0].usage.total_tokens == 130


def test_whole_page_enhancement_keeps_aspect_ratio_and_both_sides():
    source = page(400, 800)
    enhanced = detailed_page(source)
    assert (enhanced.width_px, enhanced.height_px) == (800, 1600)
    assert enhanced.asset_id == source.asset_id
    with Image.open(io.BytesIO(base64.b64decode(enhanced.data_url.split(',')[1]))) as result:
        assert result.getpixel((50, 100))[0] > 240
        assert result.getpixel((750, 100))[2] > 240


def slot_context():
    ctx = context()
    ctx['attempt']['answer_slots'] = [
        {'index': 1, 'prompt': '单位1是____', 'answer_text': '总量', 'answer_status': 'present'},
        {'index': 2, 'prompt': '用____法', 'answer_text': '加', 'answer_status': 'present'},
    ]
    return ctx


def slot_checks():
    return [
        {'index': 1, 'prompt': '单位1是____', 'student_answer': '总量', 'verdict': 'correct', 'correct_answer': '总量', 'explanation': '对应整体。'},
        {'index': 2, 'prompt': '用____法', 'student_answer': '加', 'verdict': 'incorrect', 'correct_answer': '除', 'explanation': '用对应量除以分率。'},
    ]


def test_readable_slots_override_contradictory_aggregate_blank_without_inventing_answers():
    raw = recognition()
    raw['items'][0].update(answer_text=None, answer_status='blank', blank_count=0,
                           answer_slots=slot_context()['attempt']['answer_slots'])
    review = {'readings': [
        {'target': 0, 'answer_text': '总量', 'answer_status': 'present', 'uncertainties': []},
        {'target': 1, 'answer_text': '加', 'answer_status': 'present', 'uncertainties': []},
    ]}
    with pipeline([raw, review]) as flow:
        item = flow.recognize(images=(picture(),)).data['items'][0]
    assert item['answer_status'] == 'present'
    assert item['recognition_status'] == 'ready'
    assert item['blank_count'] == 2
    assert item['answer_text'] == '1. 总量\n2. 加'


def test_only_unclear_handwriting_gets_one_full_original_page_review_with_audit():
    raw = recognition()
    raw['items'][0].update(answer_slots=deepcopy(slot_context()['attempt']['answer_slots']))
    raw['items'][0]['answer_slots'][1].update(answer_text=None, answer_status='unclear')
    requests = []
    review = {'readings': [
        {'target': 0, 'answer_text': '总量', 'answer_status': 'present', 'uncertainties': []},
        {'target': 1, 'answer_text': '减', 'answer_status': 'present', 'uncertainties': []},
    ]}
    with pipeline([raw, review], requests) as flow:
        result = flow.recognize(images=(picture(),))
    assert result.data['items'][0]['answer_text'] == '1. 总量\n2. 减'
    assert result.data['handwriting_review'][1]['before']['answer_text'] is None
    assert len(result.metadata.attempts) == len(requests) == 2
    assert result.metadata.usage.total_tokens == 260
    assert payload(requests[1])['targets'][1]['printed_prompt'] == '用____法'
    assert 'answer_text' not in payload(requests[1])['targets'][0]
    assert len(requests[1]['messages'][1]['content']) == 2


def test_closed_handwriting_blanks_are_reviewed_even_when_first_pass_sounds_confident():
    raw = recognition()
    raw['items'][0].update(answer_slots=[
        {'index': 1, 'prompt': '单位“1”____（填：已知或未知）', 'answer_text': '已知', 'answer_status': 'present'},
        {'index': 2, 'prompt': '用____法', 'answer_text': '乘', 'answer_status': 'present'},
    ])
    requests = []
    review = {'readings': [
        {'target': 0, 'answer_text': '未知', 'answer_status': 'present', 'uncertainties': []},
        {'target': 1, 'answer_text': '减', 'answer_status': 'present', 'uncertainties': []},
    ]}
    with pipeline([raw, review], requests) as flow:
        result = flow.recognize(images=(picture(),))
    item = result.data['items'][0]
    assert [slot['answer_text'] for slot in item['answer_slots']] == ['未知', '减']
    assert payload(requests[1])['targets'][0]['printed_prompt'].startswith('单位')
    assert payload(requests[1])['targets'][1]['printed_prompt'] == '用____法'
    assert len(result.metadata.attempts) == len(requests) == 2


def test_unclear_review_cannot_replace_another_slot_and_retains_both_paid_attempts():
    raw = recognition()
    raw['items'][0].update(answer_slots=deepcopy(slot_context()['attempt']['answer_slots']))
    raw['items'][0]['answer_slots'][1].update(answer_text=None, answer_status='unclear')
    review = {'readings': [
        {'target': 1, 'answer_text': '减', 'answer_status': 'present', 'uncertainties': []},
    ]}
    with pipeline([raw, review]) as flow, pytest.raises(ProviderError) as error:
        flow.recognize(images=(picture(),))
    assert len(error.value.attempts) == 2


def test_slot_judgments_control_overall_verdict_and_bind_actual_answer():
    requests = []
    with pipeline([checked(verdict='correct', answer_checks=slot_checks())], requests) as flow:
        result = flow.check(**slot_context())
    assert result.data['verdict'] == 'partially_correct'
    assert result.data['answer_checks'] == slot_checks()
    assert payload(requests[0])['answer_slots'] == slot_context()['attempt']['answer_slots']


@pytest.mark.parametrize(('actual', 'expected'), [('乘', '除法'), ('未知', '已知')])
def test_single_word_verdict_cannot_contradict_its_expected_answer(actual, expected):
    ctx = slot_context()
    ctx['attempt']['answer_slots'][1]['answer_text'] = actual
    checks = slot_checks()
    checks[1].update(student_answer=actual, correct_answer=expected, verdict='correct', explanation='学生填写正确。')
    with pipeline([checked(verdict='correct', answer_checks=checks)]) as flow:
        result = flow.check(**ctx)
    assert result.data['verdict'] == 'partially_correct'
    assert result.data['answer_checks'][1]['verdict'] == 'incorrect'
    assert f'应填“{expected}”' in result.data['correction_method']
    assert '学生填写正确' not in result.data['basis'][0]['summary']


def test_closed_answer_check_does_not_guess_equivalent_formulas_or_ambiguous_expected_answers():
    checks = slot_checks()
    checks[1].update(student_answer='加', correct_answer='加或乘', verdict='correct')
    with pipeline([checked(verdict='correct', answer_checks=checks)]) as flow:
        result = flow.check(**slot_context())
    assert result.data['verdict'] == 'correct'


def test_one_unclear_slot_is_not_blank_and_does_not_hide_a_known_error():
    ctx = slot_context()
    ctx['attempt']['answer_slots'][0].update(answer_text=None, answer_status='unclear')
    checks = slot_checks()
    checks[0].update(student_answer=None, verdict='unanswered')
    with pipeline([checked(answer_checks=checks)], []) as flow:
        result = flow.check(**ctx)
    assert result.data['verdict'] == 'incorrect'
    assert result.data['answer_checks'][0]['verdict'] == 'unable_to_judge'
    assert result.data['answer_checks'][1]['verdict'] == 'incorrect'
    assert '不能当作未填写' in result.data['answer_checks'][0]['explanation']
    assert result.data['uncertainties']


@pytest.mark.parametrize(('known', 'actual', 'expected'), [(True, '减', '乘'), (False, '加', '除')])
def test_unit_one_method_blank_uses_the_explicit_exercise_rubric(known, actual, expected):
    ctx = slot_context()
    ctx['question']['has_diagram'] = True
    ctx['attempt']['answer_slots'][0].update(prompt='单位“1”____（填：已知或未知）', answer_text='已知' if known else '未知')
    ctx['attempt']['answer_slots'][1].update(answer_text=actual)
    checks = slot_checks()
    checks[0].update(student_answer='已知' if known else '未知', correct_answer='已知' if known else '未知')
    checks[1].update(student_answer=actual, correct_answer=actual, verdict='correct')
    with pipeline([checked(verdict='correct', diagram_verified=True, visual_evidence={**visual_evidence(), 'whole_is_known': known}, answer_checks=checks)]) as flow:
        result = flow.check(**ctx)
    assert result.data['verdict'] == 'partially_correct'
    assert result.data['answer_checks'][1]['correct_answer'] == expected
    assert result.data['answer_checks'][1]['verdict'] == 'incorrect'
    assert '主要运算' in result.data['correction_method']


def test_unit_one_rubric_does_not_reject_a_complete_equivalent_calculation():
    ctx = slot_context()
    ctx['question']['has_diagram'] = True
    ctx['attempt']['answer_slots'][0].update(prompt='单位“1”____（填：已知或未知）')
    ctx['attempt']['answer_slots'][1].update(answer_text='120-120×3/5=48')
    checks = slot_checks()
    checks[1].update(student_answer='120-120×3/5=48', correct_answer='48', verdict='correct')
    with pipeline([checked(verdict='correct', diagram_verified=True, visual_evidence=visual_evidence(), answer_checks=checks)]) as flow:
        result = flow.check(**ctx)
    assert result.data['verdict'] == 'correct'


def test_unit_one_rubric_applies_when_first_pass_omits_the_known_unknown_label():
    ctx = slot_context()
    ctx['question']['has_diagram'] = True
    ctx['attempt']['answer_slots'] = [
        {'index': 1, 'prompt': '单位“1”是____', 'answer_text': '120页', 'answer_status': 'present'},
        {'index': 2, 'prompt': '（填：已知或未知）', 'answer_text': '已知', 'answer_status': 'present'},
        {'index': 3, 'prompt': '用____法', 'answer_text': '减', 'answer_status': 'present'},
    ]
    checks = slot_checks()
    checks = [
        {'index': 1, 'prompt': '单位“1”是____', 'student_answer': '120页', 'verdict': 'correct', 'correct_answer': '120页', 'explanation': '正确。'},
        {'index': 2, 'prompt': '（填：已知或未知）', 'student_answer': '已知', 'verdict': 'correct', 'correct_answer': '已知', 'explanation': '正确。'},
        {'index': 3, 'prompt': '用____法', 'student_answer': '减', 'verdict': 'correct', 'correct_answer': '减', 'explanation': '正确。'},
    ]
    with pipeline([checked(verdict='correct', diagram_verified=True, visual_evidence=visual_evidence(), answer_checks=checks)]) as flow:
        result = flow.check(**ctx)
    assert result.data['answer_checks'][2]['verdict'] == 'incorrect'
    assert result.data['answer_checks'][2]['correct_answer'] == '乘'


def test_visual_equation_result_is_repaired_only_when_arithmetic_disagrees():
    ctx = context()
    ctx['question']['diagram'] = diagram()
    equation = '180÷(1-4/7-1/3)=180÷(1/21)=3780km'
    evidence = {**visual_evidence(), 'equation': equation, 'result': '3780km'}
    with pipeline([checked(correct_answer='3780km', visual_evidence=evidence, diagram_verified=True)]) as flow:
        result = flow.check(**ctx)
    assert result.data['visual_evidence']['result'] == '1890km'
    assert result.data['visual_evidence']['equation'] == '180÷(1-4/7-1/3)=1890km'
    assert '算术校验' in result.data['uncertainties'][0]
    assert result.data['correct_answer'].startswith('1890km；列式：')


def test_fraction_after_division_sign_is_evaluated_as_the_divisor_fraction():
    ctx = context()
    ctx['question']['diagram'] = diagram()
    evidence = {**visual_evidence(), 'equation': '78÷3/5', 'result': '5.2kg'}
    with pipeline([checked(correct_answer='5.2kg', visual_evidence=evidence, diagram_verified=True)]) as flow:
        result = flow.check(**ctx)
    assert result.data['visual_evidence']['result'] == '130kg'
    assert '按列式计算结果：130kg' in result.data['correct_answer']


def test_unknown_whole_answer_prefers_the_printed_question_mark_label():
    ctx = slot_context()
    ctx['question']['diagram'] = diagram()
    evidence = {**visual_evidence(), 'brackets': [
        {'label': '3/5', 'position': '上方', 'covers': '整体线段的前3/5部分'},
        {'label': '78kg', 'position': '中间', 'covers': '整体线段的前3/5部分'},
        {'label': '?kg', 'position': '下方', 'covers': '整体线段的后2/5部分'},
    ], 'unit_whole': '整条线段表示的总质量', 'whole_is_known': False}
    checks = slot_checks()
    checks[0].update(correct_answer='整条线段表示的总质量（?kg）')
    with pipeline([checked(answer_checks=checks, visual_evidence=evidence, diagram_verified=True)]) as flow:
        result = flow.check(**ctx)
    assert result.data['answer_checks'][0]['correct_answer'] == '?kg（总质量）'


def test_unknown_whole_result_uses_the_consistent_unit_from_graph_labels():
    ctx = context()
    ctx['question']['diagram'] = diagram()
    evidence = {**visual_evidence(), 'brackets': [
        {'label': '?km', 'position': '下方', 'covers': '整体'},
        {'label': '180km', 'position': '右侧', 'covers': '部分'},
        {'label': '4/7', 'position': '左侧', 'covers': '部分'},
        {'label': '1/3', 'position': '中间', 'covers': '部分'},
    ], 'whole_is_known': False,
       'equation': '180÷(1-4/7-1/3)', 'result': '1890'}
    with pipeline([checked(correct_answer='1890', visual_evidence=evidence, diagram_verified=True)]) as flow:
        result = flow.check(**ctx)
    assert result.data['visual_evidence']['result'] == '1890km'
    assert result.data['visual_evidence']['equation'] == '180÷(1-4/7-1/3)=1890km'
    assert '1890km' in result.data['correct_answer']
    assert '列式：180÷(1-4/7-1/3)=1890km' in result.data['correct_answer']


def test_verified_diagram_solution_is_always_included_with_wrong_blank_corrections():
    ctx = context()
    ctx['question']['diagram'] = diagram()
    evidence = {**visual_evidence(), 'equation': '24÷3/4', 'result': '32棵'}
    with pipeline([checked(correct_answer='1. 梨树；2. 未知；3. 除',
                           visual_evidence=evidence, diagram_verified=True)]) as flow:
        result = flow.check(**ctx)
    assert result.data['visual_evidence']['equation'] == '24÷3/4=32棵'
    assert '列式：24÷3/4=32棵；结果：32棵' in result.data['correct_answer']


def test_unknown_route_answer_is_normalized_to_the_question_mark_label_first():
    ctx = slot_context()
    ctx['question']['diagram'] = diagram()
    evidence = {**visual_evidence(), 'brackets': [
        {'label': '?km', 'position': '下方', 'covers': '整体'},
    ], 'unit_whole': '整条线段表示的总路程（?km）', 'whole_is_known': False}
    checks = slot_checks()
    checks[0].update(correct_answer='总路程（或?km）')
    with pipeline([checked(answer_checks=checks, visual_evidence=evidence, diagram_verified=True)]) as flow:
        result = flow.check(**ctx)
    assert result.data['answer_checks'][0]['correct_answer'] == '?km（总路程）'


@pytest.mark.parametrize('mutation', ['swapped', 'rewritten', 'missing'])
def test_mismatched_slot_answers_cannot_be_persisted(mutation):
    checks = slot_checks()
    if mutation == 'swapped':
        checks.reverse()
    elif mutation == 'rewritten':
        checks[1]['student_answer'] = '除'
    else:
        checks.pop()
    with pipeline([checked(answer_checks=checks)]) as flow, pytest.raises(ProviderError) as error:
        flow.check(**slot_context())
    assert error.value.code == 'invalid_output'
    assert error.value.attempts[-1].usage.total_tokens == 130
