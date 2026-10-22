"""Actual authenticated HTTP + migrated databases; no external provider calls."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

import pytest
from sqlalchemy import event, select, update, text
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_layout_repair import repair, region
from xueban_backend.correction_models import Entity, Task
from xueban_backend.models import Homework, StudentGrant
from xueban_backend import homework_detail


def get(flow):
    return flow.client.get(f'/v1/homeworks/{flow.homework_id}/detail')


def change(flow, identifier, mutate):
    with Session(flow.database.engine) as session:
        row = session.get(Entity, identifier); value = deepcopy(row.document); mutate(value)
        row.document = value; session.commit()


def compare_single_reads(flow, bundle):
    assert bundle['protocol']=='homework-detail.v1'
    assert bundle['homework']==flow.homework()
    for key,path,idfield in [('case_records','cases','id'),('questions','questions','question_id'),('attempts','attempts','id'),('checks','checks','id'),('jobs','jobs','id')]:
        for record in bundle[key]:
            value=record.get('record',record)
            assert record==flow.client.get(f'/v1/{path}/{value[idfield]}').json()


def test_empty_and_normal_bundle_are_same_public_documents_and_no_writes(flow):
    empty=get(flow);assert empty.status_code==200,empty.text
    compare_single_reads(flow,empty.json());assert empty.json()['case_records']==[]
    c=flow.ready();job=flow.start_check(c);assert flow.worker.run_once()
    writes=[]
    def inspect(conn,cursor,statement,params,context,many):
        if statement.split()[0].lower() in {'insert','update','delete'}:writes.append(statement.split()[0])
    engine=flow.client.app.state.engine;event.listen(engine,'before_cursor_execute',inspect)
    calls=list(flow.pipeline.calls)
    try:response=get(flow)
    finally:event.remove(engine,'before_cursor_execute',inspect)
    assert response.status_code==200,response.text
    compare_single_reads(flow,response.json());assert writes==[] and flow.pipeline.calls==calls
    body=response.text
    for private in ('"audit"','"inputs"','upload_token','provider_response','data:image'):
        assert private not in body
    assert response.headers['cache-control']=='no-store'


@pytest.mark.parametrize('count',[1,31])
def test_query_count_is_bounded_independent_of_questions(flow,count):
    original=flow.pipeline.recognize
    def recognize(**kw):
        result=original(**kw);first=result.data['items'][0]
        result.data['items']=[{**deepcopy(first),'label':str(i+1)} for i in range(count)]
        return result
    flow.pipeline.recognize=recognize;flow.ready()
    statements=[]
    def inspect(conn,cursor,statement,params,context,many):
        if statement.lstrip().lower().startswith('select'):statements.append(statement)
    engine=flow.client.app.state.engine;event.listen(engine,'before_cursor_execute',inspect)
    try:response=get(flow)
    finally:event.remove(engine,'before_cursor_execute',inspect)
    assert response.status_code==200,response.text
    assert len(response.json()['case_records'])==count
    assert len(statements)==8  # 3 auth + Homework + grant + Entity/Asset/Task.
    assert sum('FROM correction_entities' in s for s in statements)==1


def test_current_and_historical_wrappers_keep_subject_override(flow):
    c=flow.ready();q=flow.client.get('/v1/questions/'+c['question_id']).json()
    with Session(flow.database.engine) as session:
        row=session.get(Entity,c['question_id']);row.annotations={**(row.annotations or {}),'subject_override':'数学'};session.commit()
    response=get(flow);assert response.status_code==200,response.text
    assert response.json()['questions'][0]['record']['subject']=='数学'
    compare_single_reads(flow,response.json())
    # A historical query must not retroactively use today's override.
    historical=flow.client.get('/v1/questions/'+c['question_id'],params={'revision':q['current_revision']})
    assert historical.status_code==200 and historical.json()['record'].get('subject')==q['record'].get('subject')


def test_replacement_records_are_complete_but_retired_rows_not_displayed(flow):
    c=flow.ready();q=flow.client.get('/v1/questions/'+c['question_id']).json()['record'];teacher(flow)
    response=repair(flow,[{'question_id':c['question_id'],'expected_revision':q['revision']}],[{'regions':[region(q['regions'][0]['asset_id'],0,0)],'stem_text':'替代题'}]);assert response.status_code==202
    assert flow.worker.run_once()
    response=get(flow);assert response.status_code==200,response.text;d=response.json();compare_single_reads(flow,d)
    assert len(d['case_records'])==2 and len(d['questions'])==len(d['attempts'])==1
    assert next(r for r in d['case_records'] if r['id']==c['id'])['lifecycle']=='superseded'


@pytest.mark.parametrize('mutation',[
 lambda d:d.update(scope={**d['scope'],'student_id':'student_b'}),
 lambda d:d.update(homework_id='homework_foreign'),
 lambda d:d.update(current_attempt_id='attempt_missing'),
 lambda d:d.update(question_id='question_missing'),
 lambda d:d.update(current_question_revision=d['current_question_revision']+1),
 lambda d:d.update(lifecycle='superseded',replacement_case_ids=[d['id']]),
 lambda d:d.update(lifecycle='superseded',replacement_case_ids=['case_missing']),
 lambda d:d.update(attempt_ids=d['attempt_ids']*2),
 lambda d:d.update(check_ids=['check_missing']),
])
def test_bad_references_are_stale_not_capacity_and_never_partial(flow,mutation):
    c=flow.ready();change(flow,c['id'],mutation)
    response=get(flow);assert response.status_code==409,response.text
    assert response.json()['code']=='stale_input' and 'case_records' not in response.json()


@pytest.mark.parametrize('model,field',[(Entity,'case_id'),(Entity,'question_id')])
def test_current_attempt_foreign_owner_or_question_rejected(flow,model,field):
    c=flow.ready();change(flow,c['current_attempt_id'],lambda d:d.update({field:'case_other' if field=='case_id' else 'question_other'}))
    response=get(flow);assert response.status_code==409 and response.json()['code']=='stale_input'


def test_foreign_tenant_and_revoked_grant_denied_before_bundle(flow,monkeypatch):
    flow.ready()
    def forbidden(*a,**k):raise AssertionError('bundle must not load after auth rejection')
    monkeypatch.setattr(homework_detail,'detail_response',forbidden)
    response=flow.client.get(f'/v1/homeworks/{flow.homework_id}/detail',headers={'Authorization':'Bearer '+flow.signing.token('subject-b')})
    assert response.status_code==404
    with Session(flow.database.engine) as session:
        session.execute(update(StudentGrant).where(StudentGrant.student_id=='student_a').values(active=False));session.commit()
    assert get(flow).status_code==404
    flow.client.headers.pop('Authorization');assert get(flow).status_code==401


@pytest.mark.parametrize('budget',['MAX_CASES','MAX_ROWS','MAX_BYTES'])
def test_exact_capacity_signal_never_truncates(flow,monkeypatch,budget):
    flow.ready();monkeypatch.setattr(homework_detail,budget,0)
    response=get(flow);assert response.status_code==409,response.text
    assert response.json()['code']=='detail_bundle_limit' and 'case_records' not in response.json()


def test_utf8_size_is_exact_response_bytes(flow,monkeypatch):
    flow.ready();response=get(flow);assert response.status_code==200
    size=len(response.content);monkeypatch.setattr(homework_detail,'MAX_BYTES',size)
    assert get(flow).content==response.content
    monkeypatch.setattr(homework_detail,'MAX_BYTES',size-1)
    assert get(flow).json()['code']=='detail_bundle_limit'


def test_read_only_enforced_and_pool_returns_to_writable(flow,monkeypatch):
    old=flow.homework()['title']
    def illegal(session,contracts,homework):
        session.execute(update(Homework).where(Homework.id==homework.id).values(title='must not persist'))
        session.flush();return None
    monkeypatch.setattr(homework_detail,'detail_response',illegal)
    assert get(flow).status_code==503
    assert flow.homework()['title']==old
    with Session(flow.client.app.state.engine) as session:
        session.execute(update(Homework).where(Homework.id==flow.homework_id).values(title='ordinary later write'));session.commit()
    assert flow.homework()['title']=='ordinary later write'


def test_pg_snapshot_is_set_before_first_auth_query_and_cannot_mix_later_commit(flow):
    if flow.database.dialect!='postgresql':pytest.skip('PG production snapshot verification')
    c=flow.ready();job=flow.start_check(c);assert flow.worker.run_once()
    before=get(flow).json();seen=[];paused=Event();release=Event()
    def inspect(conn,cursor,statement,params,context,many):
        if not statement.lstrip().lower().startswith('select'):return
        if not seen:
            assert 'FROM principals' in statement
            cursor.execute('SHOW transaction_isolation');isolation=cursor.fetchone()[0]
            cursor.execute('SHOW transaction_read_only');readonly=cursor.fetchone()[0]
            seen.append((isolation,readonly))
    def after(conn,cursor,statement,params,context,many):
        if 'FROM correction_entities' in statement and not paused.is_set():
            paused.set();assert release.wait(5)
    engine=flow.client.app.state.engine;event.listen(engine,'before_cursor_execute',inspect);event.listen(engine,'after_cursor_execute',after)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(get,flow);assert paused.wait(5)
            with Session(flow.database.engine) as session:
                # Homework revision deliberately unchanged. A check Job is read
                # after the Entity SELECT; READ COMMITTED would mix this version.
                row=session.get(Task,job['id']);row.revision+=1;row.document={**row.document,'revision':row.revision};session.commit()
            release.set();response=pending.result(timeout=5)
    finally:
        release.set();event.remove(engine,'before_cursor_execute',inspect);event.remove(engine,'after_cursor_execute',after)
    assert seen==[('repeatable read','on')]
    assert response.status_code==200,response.text
    assert response.json()==before
    fresh=get(flow).json()
    old_revision=next(j for j in before['jobs'] if j['id']==job['id'])['revision']
    assert next(j for j in fresh['jobs'] if j['id']==job['id'])['revision']==old_revision+1



def test_current_attempt_root_identity_must_match_case(flow):
    c=flow.ready()
    change(flow,c['current_attempt_id'],lambda d:d.update(root_attempt_id='attempt_wrong_root'))
    response=get(flow)
    assert response.status_code==409 and response.json()['code']=='stale_input'


def test_current_embedded_decision_must_equal_its_persisted_record(flow):
    c=flow.ready();flow.start_check(c);assert flow.worker.run_once()
    c=flow.client.get('/v1/cases/'+c['id']).json()
    assert get(flow).status_code==200
    change(flow,c['id'],lambda d:d['current_decision'].update(verdict='correct'))
    response=get(flow)
    assert response.status_code==409 and response.json()['code']=='stale_input'


def test_real_staff_review_without_revision_keeps_history_and_current_document(flow):
    c=flow.ready();flow.start_check(c);assert flow.worker.run_once();teacher(flow)
    c=flow.client.get('/v1/cases/'+c['id']).json();d=c['current_decision']
    a=flow.client.get('/v1/attempts/'+c['current_attempt_id']).json()['record']
    response=flow.post('/v1/cases/'+c['id']+'/reviews',{
        'expected_revision':c['revision'],'target_decision_id':d['id'],
        'target_decision_revision':d['revision'],'action':'confirm','verdict':d['verdict'],
        'reason':'合成坐班复核','basis':[{'kind':'staff_observation','summary':'合成核对',
            'source_ref':None,'regions':[]}], 'attempt_id':a['id'],
        'question_revision':c['current_question_revision'],
        'attempt_recognition_revision':a['recognition_revision']})
    assert response.status_code==201,response.text
    assert 'revision' not in response.json()  # Review uses immutable revision 1 in SQL.
    response=get(flow);assert response.status_code==200,response.text
    bundle=response.json();compare_single_reads(flow,bundle)
    assert bundle['case_records'][0]['current_decision']['origin']=='staff'
    assert len(bundle['checks'])==1  # Prior model check remains visible history.
    assert flow.client.get('/v1/decisions/'+d['id']).json()['record']['validity']=='stale'


def test_question_revision_keeps_old_captured_attempt_and_historical_check(flow):
    c=flow.ready();flow.start_check(c);assert flow.worker.run_once();teacher(flow)
    q=flow.client.get('/v1/questions/'+c['question_id']).json()['record']
    response=flow.post('/v1/questions/'+c['question_id']+'/revisions',{
        'expected_revision':q['revision'],'stem_text':'2 + 1 = ?',
        'regions':q['regions'],'reason':'合成题面核对'})
    assert response.status_code==201,response.text
    response=get(flow);assert response.status_code==200,response.text
    bundle=response.json();compare_single_reads(flow,bundle)
    assert bundle['questions'][0]['record']['revision']==2
    assert bundle['attempts'][0]['record']['captured_question_revision']==1
    assert bundle['checks'][0]['input_versions']['question_revision']==1
    assert bundle['case_records'][0]['current_decision'] is None


def test_neighbor_case_check_job_cannot_replace_its_own_job(flow):
    flow.pipeline.extra_item_label='2';flow.ready()
    cases=[flow.client.get('/v1/cases/'+cid).json() for cid in flow.homework()['case_ids']]
    own=flow.start_check(cases[0]);neighbor=flow.start_check(cases[1])
    with Session(flow.database.engine) as session:
        check=session.scalar(select(Entity).where(Entity.kind=='check',Entity.homework_id==flow.homework_id).order_by(Entity.id))
        for row in session.scalars(select(Entity).where(Entity.kind=='check',Entity.homework_id==flow.homework_id)):
            if row.document['job_id']==own['id']:
                row.document={**row.document,'job_id':neighbor['id']}
        session.commit()
    response=get(flow)
    assert response.status_code==409 and response.json()['code']=='stale_input'
