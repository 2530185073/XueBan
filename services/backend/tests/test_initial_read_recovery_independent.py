"""Independent real migrated DB + BoundedChat MockTransport probes; zero model calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import json
from threading import Barrier
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from conftest import database, settings, signing, payload
from test_correction_flow import flow
from test_initial_read_recovery import prepare, ledger, expire, local_read
from test_two_model_pipeline import correct
from test_two_model_reconciliation import different_stem
from test_reconciliation import review, acknowledge_notes
from xueban_backend import recognition_checkpoint as cp
from xueban_backend.correction_models import Entity, Task
from xueban_backend.providers.ark import ArkSettings, ProviderError
from xueban_backend.providers.bounded_chat import BoundedChatClient
from xueban_backend.providers.read_checkpoint import digest
from xueban_backend.providers.two_model import TwoModelWholePagePipeline
from xueban_backend.worker import CorrectionWorker


def actual_client(name, respond, calls):
    def serve(request):
        body = json.loads(request.content)
        content = body['messages'][-1]['content']
        text = next(c['text'] for c in content if c['type'] == 'text') if isinstance(content, list) else content
        task = json.loads(text)
        calls.append((name, body))
        output = respond(task)
        if isinstance(output, int):
            return httpx.Response(output, json={'error': {'message': 'synthetic fixed error'}})
        return httpx.Response(200, json={'id':'synthetic-response','model':name,
            'usage':{'prompt_tokens':11,'completion_tokens':23,'total_tokens':34},
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output,ensure_ascii=False)}}]})
    return BoundedChatClient(ArkSettings(base_url='https://synthetic.invalid/v1',api_key='synthetic-key-'+name,
        model=name,max_attempts=1),transport=httpx.MockTransport(serve))


def stable_result(result):
    out = deepcopy(result.data)
    # Private timings are measurements of two offline executions, not semantic differences.
    def remove_timing(obj):
        if isinstance(obj, dict):
            return {k:remove_timing(v) for k,v in obj.items() if k not in {'latency_ms','transport'}}
        if isinstance(obj,list): return [remove_timing(v) for v in obj]
        return obj
    return remove_timing(out)


@pytest.mark.parametrize('done', [0, 1])
def test_two_real_workers_compete_for_expired_task_only_remaining_calls_dispatch(flow, done):
    p,a,b,asset,job,identifier,token,ctx=prepare(flow)
    try:
        if done: assert local_read(p,ctx,'primary')['code']=='ok'
        original=deepcopy(ledger(flow,identifier)[cp.KEY]['slots'])
        expire(flow,identifier)
        workers=[CorrectionWorker(flow.database.engine,flow.cfg,p) for _ in range(2)]
        gate=Barrier(2)
        def run(worker):
            gate.wait(timeout=10)
            try: return worker.run_once()
            except OperationalError as error:
                if flow.database.dialect != 'sqlite' or 'database is locked' not in str(error.orig):
                    raise
                return 'existing_sqlite_claim_lock_error'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(run,workers))
        assert sum(x is True for x in outcomes)==1
        assert all(x is True or x is False or x=='existing_sqlite_claim_lock_error' for x in outcomes)
        assert (len(a),len(b))==(1,1)
        public=flow.client.get('/v1/jobs/'+identifier).json()
        assert public['status']=='succeeded'
        after=ledger(flow,identifier)
        assert len(after['executions'])==2
        assert len(after['executions'][-1]['metadata']['attempts'])==2-done
        assert after['executions'][-1]['metadata']['usage']['total_tokens']==34*(2-done)
        assert len(flow.homework()['case_ids'])==1
        if done: assert after[cp.KEY]['slots'][asset['id']]['primary']==original[asset['id']]['primary']
        assert sum(s['source']['metadata']['usage']['total_tokens'] for s in after[cp.KEY]['slots'][asset['id']].values())==68
    finally:p.close()


@pytest.mark.parametrize('journal', [False,True])
def test_two_page_failed_neighbor_keeps_real_downstream_request_payload_and_outputs(flow,journal):
    assets=[flow.uploaded(),flow.uploaded()]
    response=flow.post('/v1/homeworks/'+flow.homework_id+'/recognitions',{
        'expected_revision':flow.homework()['revision'],'asset_ids':[a['id'] for a in assets]})
    assert response.status_code==202
    calls=[]
    first=correct()
    primary=actual_client('independent-primary',lambda task:{'items':[deepcopy(first)]},calls)
    secondary=actual_client('independent-secondary',lambda task:503 if task['asset_id']==assets[0]['id'] else {'items':[different_stem()]},calls)
    def visual(task):
        assert task['asset_id']==assets[1]['id']
        return {'items':[{k:deepcopy(v) for k,v in first.items() if k in {'label','stem_text','answer_text','answer_status','uncertainties','options'}}]}
    def reviewed(task):
        assert task['asset_id']==assets[1]['id']
        item=review()
        item['observation_issue_reviews']=acknowledge_notes(task['task_input']['items'][0])
        return {'items':[item]}
    visual_client=actual_client('independent-visual',visual,calls)
    reviewer=actual_client('independent-review',reviewed,calls)
    p=TwoModelWholePagePipeline(primary,secondary,visual_client=visual_client,reconciliation_client=reviewer,
        read_checkpoint_enabled=True,initial_read_recovery_enabled=True,host_call_journal_enabled=journal)
    flow.worker.pipeline=p
    try:
        identifier,token=flow.worker.claim()
        _,ctx=flow.worker._load(identifier,token)
        baseline=p.recognize(images=ctx['images'])
        before_calls=deepcopy(calls);calls.clear()
        candidate=p.recognize(**ctx)
        assert sorted((n,json.dumps(b,sort_keys=True)) for n,b in calls)==sorted((n,json.dumps(b,sort_keys=True)) for n,b in before_calls)
        assert len(calls)==6
        assert stable_result(candidate)==stable_result(baseline)
        assert [x['assessment']['verdict'] for x in candidate.data['items']]==['unable_to_judge','correct']
        assert len(candidate.metadata.attempts)==len(baseline.metadata.attempts)==6
        assert candidate.metadata.usage==baseline.metadata.usage
        rec=ledger(flow,identifier)[cp.KEY]
        assert rec['slots'][assets[0]['id']]['secondary']['state']=='failed'
        assert rec['slots'][assets[1]['id']]['secondary']['state']=='completed'
        assert rec['downstream_started'] is True
        flow.worker.finish(identifier,token,result=candidate)
        assert flow.client.get('/v1/jobs/'+identifier).json()['status']=='succeeded'
        assert len(flow.homework()['case_ids'])==2
    finally:p.close()


@pytest.mark.parametrize('callback', ['complete_recovery','fail_recovery'])
def test_worker_persistence_exception_no_auto_reissue_no_exception_text(flow,callback):
    p,a,b,asset,job,identifier,token,ctx=prepare(flow)
    # Start a new owner from the real saved available v2 state; callbacks are loaded by worker.
    expire(flow,identifier)
    if callback=='fail_recovery':
        old=p.client
        a=[]
        p.client=actual_client('initial-a',lambda task:503,a)
        old.close()
        # Same adapter/settings, so exact original contract remains unchanged.
    try:
        with patch.object(cp,callback,side_effect=RuntimeError('private-body-sentinel')):
            assert flow.worker.run_once()
        saved=ledger(flow,identifier)
        counts=(len(a),len(b))
        assert counts[0]==1
        assert flow.client.get('/v1/jobs/'+identifier).json()['status']=='failed'
        assert flow.client.get('/v1/jobs/'+identifier).json()['failure']['retryable'] is False
        assert not flow.worker.run_once()
        assert (len(a),len(b))==counts
        assert 'private-body-sentinel' not in json.dumps(saved)
        assert not flow.homework()['case_ids']
        assert saved[cp.KEY]['slots'][asset['id']]['primary']['state']=='reserved'
        assert saved[cp.KEY]['slots'][asset['id']]['primary']['charge_status']=='unknown'
        assert saved['executions'][-1]['outcome']=='failed'
    finally:p.close()


def test_reused_source_failing_current_validator_never_dispatches_again(flow):
    p,a,b,asset,job,identifier,token,ctx=prepare(flow)
    try:
        for stage in ('primary','secondary'): assert local_read(p,ctx,stage)['code']=='ok'
        old=deepcopy(ledger(flow,identifier)[cp.KEY]['slots'])
        expire(flow,identifier)
        with patch('xueban_backend.providers.two_model._validate',side_effect=ProviderError('invalid_output')):
            assert flow.worker.run_once()
        assert (len(a),len(b))==(1,1)
        saved=ledger(flow,identifier)
        assert saved[cp.KEY]['slots']==old
        assert flow.client.get('/v1/jobs/'+identifier).json()['status']=='failed'
        assert not flow.homework()['case_ids']
        assert saved['executions'][-1]['attempts']==[]
        assert len(saved[cp.KEY]['reuse_events'])==2
        assert not flow.worker.run_once()
    finally:p.close()


def test_unknown_reservation_is_public_failure_not_blank_answer_or_retry(flow):
    p,a,b,asset,job,identifier,token,ctx=prepare(flow)
    try:
        contract=p.initial_read_contracts(ctx['images'],identifier)[asset['id']]['primary']
        ctx['read_checkpoint'].before_generate(ctx['images'][0],'primary',contract)
        before=deepcopy(ledger(flow,identifier)[cp.KEY]['slots'][asset['id']]['primary'])
        expire(flow,identifier);assert flow.worker.run_once()
        public=flow.client.get('/v1/jobs/'+identifier).json()
        progress=flow.client.get('/v1/jobs/'+identifier+'/recognition-progress').json()
        assert public['status']=='failed' and public['failure']['retryable'] is False
        assert progress['pages']==[]
        after=ledger(flow,identifier)
        slot=after[cp.KEY]['slots'][asset['id']]['primary']
        assert slot['state']=='unknown' and slot['receipt_id']==before['receipt_id']
        assert slot['dispatched_owner_sha256']==before['dispatched_owner_sha256']
        assert slot['charge_status']=='unknown'
        assert after['executions'][-1]['outcome']=='failed' and after['executions'][-1]['attempts']==[]
        assert not a and not b and not flow.homework()['case_ids']
        with Session(flow.database.engine) as session:
            assert not session.scalars(select(Entity).where(Entity.homework_id==flow.homework_id)).all()
    finally:p.close()
