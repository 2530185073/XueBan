"""Student notebook integration: original error survives a complete correction journey."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_practice import clock  # noqa: F401
from test_student_variants import ready, start
from xueban_backend.learning_models import PracticeResponse, StudentKnowledgeState
from xueban_backend.practice_generation import _near_copy_stem


def test_repeated_ocr_source_cannot_bypass_number_swap_filter_with_minor_wording():
    original = '已知两数的商是-7/2，被除数是7/2，求除数。\n1. 已知两数的商是-7/2，被除数是7/2，求除数。'
    assert _near_copy_stem('已知两个数的商是 -5/3，被除数是 10/3，求除数。', original)
    assert not _near_copy_stem('计算 8÷(-2)=____', '计算 6÷(-3)=____')
    assert not _near_copy_stem('在算式 (-8)÷□=2/3 中，方框里应填什么数？写出计算过程。', original)


def card(flow, case_id):
    response = flow.client.get('/v1/students/student_a/variant-notebook')
    assert response.status_code == 200, response.text
    return next(item for item in response.json()['items'] if item['case_id'] == case_id)


def test_wrong_retry_correct_keeps_original_card_and_both_attempts(flow, clock):
    case, task, _ = ready(flow)
    assert card(flow, case['id'])['status'] == 'uncorrected'
    initial = start(flow, task).json()
    item_id = initial['items'][0]['id']
    assert initial['no_expiry'] is True
    wrong = flow.post(f"/v1/practice-sessions/{initial['id']}/responses", {
        'item_id': item_id, 'answers': [{'field_id': 'blank1', 'answer_text': '8'}],
    })
    assert wrong.status_code == 201, wrong.text
    assert wrong.json()['result'] == 'incorrect'
    assert card(flow, case['id'])['status'] == 'needs_practice'
    retried = flow.post(f"/v1/practice-sessions/{initial['id']}/retry", {}, key='journey-retry-one')
    assert retried.status_code in (200, 201), retried.text
    next_session = retried.json()
    assert next_session['id'] != initial['id']
    assert next_session['items'][0]['id'] == item_id
    replay = flow.post(f"/v1/practice-sessions/{initial['id']}/retry", {}, key='journey-retry-one')
    assert replay.json()['id'] == next_session['id']
    correct = flow.post(f"/v1/practice-sessions/{next_session['id']}/responses", {
        'item_id': item_id, 'answers': [{'field_id': 'blank1', 'answer_text': '7'}],
    })
    assert correct.status_code == 201, correct.text
    assert correct.json()['result'] == 'correct'
    kept = card(flow, case['id'])
    assert kept['status'] == 'correct' and kept['practiced'] is True
    assert kept['session_id'] == next_session['id']
    assert kept['question_id'] == case['question_id']
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(PracticeResponse).where(
            PracticeResponse.session_id.in_([initial['id'], next_session['id']]))) == 2


def test_optional_answer_reveal_keeps_notebook_uncorrected_until_submission(flow, clock):
    case, task, model = ready(flow)
    activity = start(flow, task).json()
    item = activity['items'][0]
    assert 'solution_text' not in item and 'answer_values' not in item
    endpoint = f"/v1/practice-sessions/{activity['id']}/items/{item['id']}/solution"
    solution = flow.client.post(endpoint, json={})
    assert solution.status_code == 200, solution.text
    assert solution.json()['item_id'] == item['id']
    assert '7' in solution.json()['answer_text']
    assert solution.json()['solution_text'] == '3+4=7。'
    assert flow.client.post(endpoint, json={}).json() == solution.json()
    untouched = card(flow, case['id'])
    assert untouched['status'] == 'uncorrected' and untouched['practiced'] is False
    answer = flow.client.post(f"/v1/practice-sessions/{activity['id']}/responses", json={
        'item_id': item['id'], 'answers': [{'field_id': 'blank1', 'answer_text': '7'}],
    })
    assert answer.status_code == 201, answer.text
    assert answer.json()['result'] == 'correct'
    completed = flow.client.get(f"/v1/practice-sessions/{activity['id']}").json()
    assert completed['items'][0]['response']['hint_used'] is True
    assert card(flow, case['id'])['status'] == 'correct'
    assert len(model.calls) == 2  # Read persisted references, never generate again.
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(StudentKnowledgeState)) == 0
