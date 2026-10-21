"""Durable initial-read resume with real HTTP adapters and isolated databases."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from threading import Barrier
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow
from test_partial_output_persistence import make_client
from test_two_model_pipeline import correct
from test_whole_page_pipeline import picture
from xueban_backend import recognition_checkpoint as cp
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Asset, Entity, Task
from xueban_backend.models import Homework, Student
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers.read_checkpoint import digest
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def pipeline():
    one, a = make_client([correct()], 'initial-a')
    two, b = make_client([correct()], 'initial-b')
    return TwoModelWholePagePipeline(one, two, read_checkpoint_enabled=True,
        initial_read_recovery_enabled=True, host_call_journal_enabled=True), a, b


def prepare(flow):
    p, a, b = pipeline(); flow.worker.pipeline = p
    asset, job = flow.recognize()
    identifier, token = flow.worker.claim()
    _, context = flow.worker._load(identifier, token)
    return p, a, b, asset, job, identifier, token, context


def ledger(flow, identifier):
    with Session(flow.database.engine) as session:
        return deepcopy(session.get(Task, identifier).audit)


def expire(flow, identifier):
    with Session(flow.database.engine) as session:
        session.get(Task, identifier).lease_expires_at = now() - timedelta(seconds=1)
        session.commit()


def local_read(p, ctx, stage):
    local = copy(p); local._read_checkpoint = ctx['read_checkpoint']
    return local._read_and_judge(p.client if stage == 'primary' else p.review_client,
                                 ctx['images'][0], stage)


@pytest.mark.parametrize('done', [0, 1, 2])
def test_restart_reuses_exact_completed_sources_without_new_usage(flow, done):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    for stage in ('primary', 'secondary')[:done]:
        assert local_read(p, ctx, stage)['code'] == 'ok'
    before = ledger(flow, identifier)[cp.KEY]
    expire(flow, identifier)
    assert flow.worker.run_once()
    after = ledger(flow, identifier)
    assert flow.client.get('/v1/jobs/' + identifier).json()['status'] == 'succeeded'
    assert len(a) == len(b) == 1
    record = after[cp.KEY]
    assert all(slot['state'] == 'completed' for slot in record['slots'][asset['id']].values())
    for stage in ('primary', 'secondary')[:done]:
        assert record['slots'][asset['id']][stage] == before['slots'][asset['id']][stage]
    assert len(record['reuse_events']) == done
    metadata = after['executions'][-1]['metadata']
    assert len(metadata['attempts']) == 2 - done
    assert metadata['usage'] == (None if done == 2 else {
        'prompt_tokens': 11*(2-done), 'completion_tokens': 23*(2-done),
        'total_tokens': 34*(2-done), 'cached_tokens': None, 'reasoning_tokens': None})
    receipts = record['slots'][asset['id']].values()
    assert sum(s['source']['metadata']['usage']['total_tokens'] for s in receipts) == 68
    assert len(flow.homework()['case_ids']) == 1
    assert not flow.worker.run_once()
    p.close()


@pytest.mark.parametrize('window', ['reserved', 'after_http', 'failed', 'downstream'])
def test_unknown_or_failed_or_downstream_never_reissues(flow, window):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    sink = ctx['read_checkpoint']; image = ctx['images'][0]
    contract = p.initial_read_contracts(ctx['images'], identifier)[asset['id']]['primary']
    if window in {'reserved', 'after_http'}:
        sink.before_generate(image, 'primary', contract)
        if window == 'after_http':
            p.client.generate(**p.initial_read_request(image, 'primary'))
    elif window == 'failed':
        sink.before_generate(image, 'primary', contract)
        sink.failure(image, 'primary', None)
    else:
        for stage in ('primary', 'secondary'): assert local_read(p, ctx, stage)['code'] == 'ok'
        sink.before_generate(image, 'semantic_alignment', None)
    counts = len(a), len(b)
    before = deepcopy(ledger(flow, identifier)[cp.KEY]['slots'])
    expire(flow, identifier)
    assert flow.worker.run_once()
    assert (len(a), len(b)) == counts
    after = ledger(flow, identifier)[cp.KEY]['slots']
    if window in {'reserved', 'after_http'}:
        changed = after[asset['id']]['primary']
        assert changed['state'] == 'unknown'
        assert {k: v for k, v in changed.items() if k not in {'state', 'unknown_at'}} == {k: v for k, v in before[asset['id']]['primary'].items() if k != 'state'}
    else:
        assert after == before
    failed = flow.client.get('/v1/jobs/' + identifier).json()
    assert failed['status'] == 'failed'
    assert failed['failure']['message'] == cp.RESUME_INTERRUPTED_MESSAGE
    assert failed['failure']['retryable'] is False
    assert not flow.homework()['case_ids']
    p.close()


def test_persist_failure_after_http_is_not_a_second_dispatch(flow):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    with patch.object(cp, 'complete_recovery', side_effect=RuntimeError('private-data-not-log')):
        with pytest.raises(RuntimeError): local_read(p, ctx, 'primary')
    assert len(a) == 1
    expire(flow, identifier); flow.worker.run_once()
    assert (len(a), len(b)) == (1, 0)
    assert 'private-data-not-log' not in json.dumps(ledger(flow, identifier))
    p.close()


@pytest.mark.parametrize('mutation', ['key', 'model', 'thinking', 'inputs', 'image', 'student', 'source', 'case'])
def test_binding_changes_deny_resume_without_calls(flow, mutation):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    assert local_read(p, ctx, 'primary')['code'] == 'ok'
    if mutation in {'key', 'model'}:
        p.client.settings = replace(p.client.settings, **({'api_key': 'rotated-test-key'} if mutation == 'key' else {'model': 'rotated-model'}))
    elif mutation == 'thinking': p.secondary_thinking = not p.secondary_thinking
    else:
        with Session(flow.database.engine) as session:
            task = session.get(Task, identifier)
            if mutation == 'inputs': task.inputs = {**task.inputs, 'tampered': True}
            if mutation == 'image': session.get(Asset, asset['id']).revision += 1
            if mutation == 'student': session.get(Homework, flow.homework_id).student_id = 'student_ungranted'
            if mutation == 'source':
                audit = deepcopy(task.audit)
                audit[cp.KEY]['slots'][asset['id']]['primary']['source']['data']['items'][0]['answer_text'] = 'tampered'
                task.audit = audit
            if mutation == 'case':
                session.add(Entity(id='case_fixture', homework_id=flow.homework_id, kind='case', revision=1, document={}))
            session.commit()
    expire(flow, identifier); flow.worker.run_once()
    assert (len(a), len(b)) == (1, 0)
    with Session(flow.database.engine) as session:
        assert session.get(Task, identifier).status == 'failed'
    if mutation == 'student':
        assert flow.client.get('/v1/jobs/' + identifier).status_code == 404
    p.close()


@pytest.mark.parametrize('legacy', ['missing', 'v1', 'unknown_history'])
def test_old_history_missing_v2_is_not_available(flow, legacy):
    p, a, b = pipeline(); flow.worker.pipeline = p
    asset, job = flow.recognize(); identifier, token = flow.worker.claim()
    with Session(flow.database.engine) as session:
        task = session.get(Task, identifier); audit = deepcopy(task.audit)
        if legacy == 'v1': audit[cp.KEY] = {'protocol': cp.PROTOCOL}
        elif legacy == 'unknown_history': audit['executions'][0]['host_call_journal'] = {'unknown': True}
        task.audit = audit; session.commit()
    if legacy == 'missing': expire(flow, identifier); flow.worker.run_once()
    else:
        with pytest.raises(ProviderError, match='recognition_resume_unavailable'): flow.worker._load(identifier, token)
    assert not a and not b
    p.close()


def test_feature_off_cannot_fall_back_existing_v2(flow):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    local_read(p, ctx, 'primary'); p.initial_read_recovery_enabled = False
    expire(flow, identifier); assert flow.worker.claim() is None
    assert (len(a), len(b)) == (1, 0)
    assert flow.client.get('/v1/jobs/' + identifier).json()['status'] == 'failed'
    p.close()


def test_direct_nonworker_pipeline_retains_old_behavior_with_feature_flag():
    p, a, b = pipeline()
    assert p.recognize(images=[picture()]).data['items'][0]['recognition_status'] == 'ready'
    assert len(a) == len(b) == 1
    p.close()


def test_concurrent_reservation_has_exactly_one_permit(flow):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    record = ledger(flow, identifier)[cp.KEY]
    contract = p.initial_read_contracts(ctx['images'], identifier)[asset['id']]['primary']
    barrier = Barrier(2)
    def reserve():
        barrier.wait()
        try:
            return cp.reserve_or_reuse(flow.database.engine, identifier, token,
                record['binding'], asset['id'], 'primary', contract)
        except ProviderError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert sum(result is not None for result in results) == 1
    assert not a and not b
    p.close()


def test_public_projection_never_exposes_complete_source_or_receipts(flow):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    local_read(p, ctx, 'primary')
    public = flow.client.get('/v1/jobs/' + identifier + '/recognition-progress').json()
    assert set(public['pages'][0]['draft'][0]) == {'label', 'stem_text', 'answer_text'}
    rendered = json.dumps(public)
    assert not any(k in rendered for k in ('metadata', 'slots', 'receipt_id', 'providers', 'source_sha256'))
    assert 'source' in ledger(flow, identifier)[cp.KEY]['slots'][asset['id']]['primary']
    p.close()


def test_new_task_on_homework_with_cases_keeps_legacy_path(flow):
    p, a, b = pipeline(); flow.worker.pipeline = p
    flow.ready()
    _, job = flow.recognize()
    assert flow.worker.run_once()
    assert flow.client.get('/v1/jobs/' + job['id']).json()['status'] == 'succeeded'
    record = ledger(flow, job['id'])[cp.KEY]
    assert record['protocol'] == cp.PROTOCOL and 'slots' not in record
    assert len(a) == len(b) == 2
    p.close()


def test_two_sources_completing_together_persist_both_slots(flow):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fs = [pool.submit(local_read, p, ctx, stage) for stage in ('primary', 'secondary')]
        assert all(f.result(timeout=10)['code'] == 'ok' for f in fs)
    slots = ledger(flow, identifier)[cp.KEY]['slots'][asset['id']]
    assert all(s['state'] == 'completed' for s in slots.values())
    assert len({s['receipt_id'] for s in slots.values()}) == 2
    p.close()


def test_old_owner_cannot_clear_or_complete_new_owner_slots(flow):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    local_read(p, ctx, 'primary'); expire(flow, identifier)
    _, next_token = flow.worker.claim(); _, fresh = flow.worker._load(identifier, next_token)
    assert next_token != token
    before = ledger(flow, identifier)[cp.KEY]
    with pytest.raises(ProviderError):
        ctx['read_checkpoint'].before_generate(ctx['images'][0], 'secondary',
            p.initial_read_contracts(ctx['images'], identifier)[asset['id']]['secondary'])
    assert ledger(flow, identifier)[cp.KEY] == before
    assert local_read(p, fresh, 'secondary')['code'] == 'ok'
    assert (len(a), len(b)) == (1, 1)
    p.close()


@pytest.mark.parametrize('stage', ['semantic_alignment', 'reconciliation', 'visual_readback',
                                  'critical_readback', 'visual_reconciliation', 'visual_adjudication'])
def test_every_downstream_dispatch_persists_barrier_before_http(flow, stage):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    local = copy(p); local._read_checkpoint = ctx['read_checkpoint']
    seen = []
    original = p.client.generate
    def traced(**kwargs):
        seen.append(ledger(flow, identifier)[cp.KEY]['downstream_started'])
        return original(**kwargs)
    with patch.object(p.client, 'generate', traced):
        local._generate_with_journal(p.client, ctx['images'][0], stage,
            **p.initial_read_request(ctx['images'][0], 'primary'))
    assert seen == [True]
    expire(flow, identifier); flow.worker.run_once()
    assert len(a) == 1 and not b
    p.close()


def test_failed_page_does_not_disable_other_page_downstream(flow):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    image = ctx['images'][0]; sink = ctx['read_checkpoint']
    contract = p.initial_read_contracts(ctx['images'], identifier)[asset['id']]['primary']
    sink.before_generate(image, 'primary', contract)
    sink.failure(image, 'primary', None)
    # The barrier records a point of no safe replay, not an all-pages-success gate.
    sink.before_generate(image, 'visual_readback', None)
    assert local_read(p, ctx, 'secondary')['code'] == 'ok'
    assert not a and len(b) == 1
    assert ledger(flow, identifier)[cp.KEY]['downstream_started'] is True
    p.close()


@pytest.mark.parametrize('bad', [None, 0, 1, '1', [], {}])
def test_recovery_flag_is_strict_boolean(bad):
    p, _, _ = pipeline()
    with pytest.raises(ProviderError, match='invalid_configuration'):
        TwoModelWholePagePipeline(p.client, p.review_client, read_checkpoint_enabled=True,
                                 initial_read_recovery_enabled=bad)
    p.close()


@pytest.mark.parametrize('bad', ['true', 'false', '', '2', True, 1])
def test_recovery_env_flag_strict_and_default_off(bad):
    with pytest.raises(ProviderError, match='invalid_configuration'):
        TwoModelWholePagePipeline.from_env({'INITIAL_READ_RECOVERY_ENABLED': bad})


def test_hmac_task_and_role_and_credentials_not_stored_plaintext():
    p, _, _ = pipeline(); image = picture()
    p.review_client.settings = replace(p.review_client.settings, api_key=p.client.settings.api_key)
    one = p.initial_read_contracts([image], 'task-a')[image.asset_id]
    two = p.initial_read_contracts([image], 'task-b')[image.asset_id]
    assert one['primary']['providers'] != two['primary']['providers']
    assert one['primary']['providers']['primary']['credential_scope_sha256'] != one['primary']['providers']['secondary']['credential_scope_sha256']
    assert p.client.settings.api_key not in json.dumps(one)
    p.client.settings = replace(p.client.settings, api_key='')
    with pytest.raises(ProviderError, match='invalid_configuration'): p.initial_read_contracts([image], 'task-a')
    p.close()


def test_original_recheck_with_enabled_flag_keeps_targeted_request():
    from test_original_recheck_provider import context
    p, a, b = pipeline()
    result = p.rerecognize_original(**context())
    assert result.data['items'][0]['recognition_status'] == 'ready'
    assert len(a) == len(b) == 1
    assert 'original_target' in json.dumps(json.loads(a[0].content))
    p.close()


def test_first_execution_flag_off_keeps_normal_flow(flow):
    p, a, b = pipeline(); p.initial_read_recovery_enabled = False
    flow.worker.pipeline = p
    _, job = flow.recognize(); assert flow.worker.run_once()
    assert flow.client.get('/v1/jobs/' + job['id']).json()['status'] == 'succeeded'
    assert ledger(flow, job['id'])[cp.KEY]['protocol'] == cp.PROTOCOL
    assert len(a) == len(b) == 1
    p.close()


def test_flag_off_after_claim_also_refuses_v2_load(flow):
    p, a, b, asset, job, identifier, token, ctx = prepare(flow)
    p.initial_read_recovery_enabled = False
    with pytest.raises(ProviderError, match='recognition_resume_unavailable'):
        flow.worker._load(identifier, token)
    assert not a and not b
    p.close()
