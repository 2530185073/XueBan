"""Offline first-source checkpoint; HTTP/DB and real pipeline, no models."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from threading import Event
from unittest.mock import patch
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_correction_flow import flow
from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend import recognition_checkpoint as checkpoint
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Asset, Entity, Task
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers.read_checkpoint import ReadCheckpoint, ReadCheckpointSink
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def pipeline(first=None, second=None, enabled=True):
    return TwoModelWholePagePipeline(FakeClient('one', first or {'items': [correct()]}),
        FakeClient('two', second or {'items': [correct()]}), read_checkpoint_enabled=enabled)


def test_request_final_and_count_equivalent_and_private():
    a, b = pipeline(enabled=False), pipeline(); events = []
    old = a.recognize(images=(picture(),))
    new = b.recognize(images=(picture(),), read_checkpoint=ReadCheckpointSink(events.append))
    assert old.data == new.data
    assert a.client.calls == b.client.calls and a.review_client.calls == b.review_client.calls
    assert {e.stage for e in events} == {'primary', 'secondary'}
    assert 'stem_text' not in repr(events[0]) and not hasattr(b, '_read_checkpoint')


def test_callback_failure_cannot_fail_or_repeat_request():
    p = pipeline(); diagnostics = []
    def fail(event): raise RuntimeError('private-body')
    result = p.recognize(images=(picture(),), read_checkpoint=ReadCheckpointSink(fail, lambda: diagnostics.append('checkpoint_write_failed')))
    assert result.data['items'][0]['assessment']['verdict'] == 'correct'
    assert len(p.client.calls) == len(p.review_client.calls) == 1
    assert diagnostics == ['checkpoint_write_failed'] * 2


@pytest.mark.parametrize('bad', [None, 0, 1, '1', [], {}])
def test_flag_strict_bool(bad):
    with pytest.raises(ProviderError, match='invalid_configuration'): pipeline(enabled=bad)


@pytest.mark.parametrize('bad', ['true', 'false', '', '2', True, 1])
def test_env_flag_strict(bad):
    with pytest.raises(ProviderError, match='invalid_configuration'):
        TwoModelWholePagePipeline.from_env({'READ_CHECKPOINT_ENABLED': bad})


def test_secondary_published_while_primary_waits():
    release, seen = Event(), Event()
    def slow(_):
        assert release.wait(5)
        return {'items': [correct()]}
    p = pipeline(first=slow); events = []
    def publish(event): events.append(event); seen.set()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(p.recognize, images=(picture(),), read_checkpoint=ReadCheckpointSink(publish))
        try:
            assert seen.wait(5) and not future.done()
            assert [e.stage for e in events] == ['secondary']
        finally: release.set()
        result = future.result(timeout=5)
    assert [c['stage'] for c in result.data['multimodel_audit']['calls']] == ['primary', 'secondary']


def test_credentials_never_published():
    p = pipeline(); p.client.output = {'items': [correct(answer_text=p.review_client.settings.api_key)]}
    events = []; p.recognize(images=(picture(),), read_checkpoint=ReadCheckpointSink(events.append))
    assert [e.stage for e in events] == ['secondary']
    assert p.review_client.settings.api_key not in repr(events)


def prepare(flow):
    asset, job = flow.recognize(); flow.worker.pipeline = pipeline()
    task_id, token = flow.worker.claim(); kind, context = flow.worker._load(task_id, token)
    assert kind == 'recognize_homework'
    return asset, job, task_id, token, context


def get_progress(flow, job):
    response = flow.client.get('/v1/jobs/' + job['id'] + '/recognition-progress')
    assert response.status_code == 200, response.text
    return response.json()


def test_success_then_timeout_refresh_and_final_replacement(flow):
    release, committed = Event(), Event()
    def timeout(_):
        assert release.wait(10)
        raise ProviderError('upstream_timeout')
    raw = correct(answer_text='  学生答案：4  ')
    p = pipeline(first={'items': [raw]}, second=timeout); flow.worker.pipeline = p
    asset, job = flow.recognize(); original = checkpoint.persist
    def observe(*args, **kwargs):
        value = original(*args, **kwargs)
        if value: committed.set()
        return value
    with patch.object(checkpoint, 'persist', observe), ThreadPoolExecutor() as pool:
        future = pool.submit(flow.worker.run_once)
        try:
            assert committed.wait(10) and not future.done()
            value = get_progress(flow, job)
            assert value['state'] == 'reading'
            assert value['pages'][0]['asset_id'] == asset['id']
            assert value['pages'][0]['draft'][0]['answer_text'] == raw['answer_text']
            assert value == get_progress(flow, job)
            assert set(value['pages'][0]['draft'][0]) == {'label', 'stem_text', 'answer_text'}
            with Session(flow.database.engine) as session:
                assert session.scalar(select(func.count()).select_from(Entity)) == 0
                assert checkpoint.KEY in session.get(Task, job['id']).audit
            assert checkpoint.KEY not in json.dumps(flow.client.get('/v1/jobs/' + job['id']).json())
        finally: release.set()
        assert future.result(timeout=10)
    assert get_progress(flow, job) == {**value, 'state': 'final_available', 'pages': []}
    assert len(p.client.calls) == len(p.review_client.calls) == 1
    with Session(flow.database.engine) as session:
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == 'attempt')) == 1
        assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == 'question')) == 1


@pytest.mark.parametrize('mutation', ['owner', 'lease', 'terminal', 'input', 'asset', 'other_asset'])
def test_late_or_wrong_input_cannot_persist(flow, mutation):
    asset, job, task_id, token, ctx = prepare(flow)
    event = ReadCheckpoint.completed(ctx['images'][0], 'primary', {'items': [correct()]})
    if mutation == 'other_asset': event = replace(event, asset_id='asset_other')
    with Session(flow.database.engine) as session:
        task = session.get(Task, task_id)
        if mutation == 'owner': task.lease_token = 'different-owner'
        if mutation == 'lease': task.lease_expires_at = now() - timedelta(seconds=1)
        if mutation == 'terminal': task.status = 'succeeded'
        if mutation == 'input': task.inputs = {**task.inputs, 'tampered': True}
        if mutation == 'asset': session.get(Asset, asset['id']).revision += 1
        session.commit()
    ctx['read_checkpoint']._publish(event)
    with Session(flow.database.engine) as session:
        assert checkpoint.KEY not in (session.get(Task, task_id).audit or {})
    assert not get_progress(flow, job)['pages']


def test_new_owner_hides_old_draft_and_source_immutable(flow):
    _, job, task_id, token, ctx = prepare(flow)
    ctx['read_checkpoint'].completed(ctx['images'][0], 'primary', {'items': [correct(answer_text='original')]})
    old = get_progress(flow, job)
    ctx['read_checkpoint'].completed(ctx['images'][0], 'primary', {'items': [correct(answer_text='overwrite')]})
    assert get_progress(flow, job) == old
    with Session(flow.database.engine) as session:
        task = session.get(Task, task_id); task.lease_token = 'next-owner'; session.commit()
    assert not get_progress(flow, job)['pages']


def test_foreign_actor_denied(flow):
    _, job, _, _, ctx = prepare(flow)
    ctx['read_checkpoint'].completed(ctx['images'][0], 'primary', {'items': [correct()]})
    response = flow.client.get('/v1/jobs/' + job['id'] + '/recognition-progress', headers={'Authorization': 'Bearer ' + flow.signing.token(subject='subject-b')})
    assert response.status_code in {401, 403, 404}


def test_parallel_invocations_keep_per_page_sinks_and_cannot_mutate_source():
    from threading import Barrier
    barrier = Barrier(4)
    p = pipeline(); p.client.barrier = p.review_client.barrier = barrier
    events = [[], []]
    with ThreadPoolExecutor() as pool:
        futures = [pool.submit(p.recognize, images=(picture('asset_' + str(i)),),
            read_checkpoint=ReadCheckpointSink(events[i].append)) for i in range(2)]
        [f.result(timeout=5) for f in futures]
    assert [{e.asset_id for e in group} for group in events] == [{'asset_0'}, {'asset_1'}]
    rows = events[0][0].transcript(); rows[0]['answer_text'] = 'changed'
    assert events[0][0].transcript()[0]['answer_text'] != 'changed'


def test_candidate_checkpoint_field_cannot_create_host_draft(flow):
    _, job = flow.recognize()
    original = flow.pipeline.recognize
    def forged(**context):
        result = original(**context)
        result.data[checkpoint.KEY] = {'transcript': [{'answer_text': 'forged'}]}
        return result
    flow.pipeline.recognize = forged
    assert flow.worker.run_once()
    with Session(flow.database.engine) as session:
        assert checkpoint.KEY not in session.get(Task, job['id']).audit
    assert not get_progress(flow, job)['pages']


def test_persistence_exception_is_fixed_diagnostic_not_retry(flow):
    _, job = flow.recognize(); p = pipeline(); flow.worker.pipeline = p
    original = checkpoint.persist; count = 0
    def fail_once(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1: raise RuntimeError('private-db-message')
        return original(*args, **kwargs)
    with patch.object(checkpoint, 'persist', fail_once):
        assert flow.worker.run_once()
    with Session(flow.database.engine) as session:
        task = session.get(Task, job['id'])
        assert task.status == 'succeeded'
        assert task.audit[checkpoint.KEY]['diagnostics'] == ['checkpoint_write_failed']
        assert 'private-db-message' not in json.dumps(task.audit)
    assert len(p.client.calls) == len(p.review_client.calls) == 1


def test_nullable_transcript_and_readable_neighbor_keep_original_values(flow):
    _, job, _, _, ctx = prepare(flow)
    raw = [correct('1', stem_text=None, answer_text=None, answer_status='blank', verdict='unanswered'), correct('2')]
    flow.worker.pipeline.client.output = flow.worker.pipeline.review_client.output = {'items': raw}
    flow.worker.pipeline.recognize(**ctx)
    value = get_progress(flow, job)
    assert value['state'] == 'checking'
    assert value['pages'][0]['draft'] == [{k: r[k] for k in ('label', 'stem_text', 'answer_text')} for r in raw]
    # Frozen real endpoint fixture is consumed by frontend guard/render tests.
    from pathlib import Path
    fixture = Path(__file__).resolve().parents[3] / 'apps/student-web/src/core/recognitionProgress.fixture.json'
    expected = json.loads(fixture.read_text())
    value.update(job_id='job_one', homework_id='hw_one')
    value['pages'][0]['asset_id'] = 'asset_one'
    assert value == expected
