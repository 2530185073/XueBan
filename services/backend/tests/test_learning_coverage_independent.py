"""Independent coverage boundaries through real migrated DB and HTTP, no model I/O."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import event,select
from sqlalchemy.orm import Session

from conftest import database,settings,signing,payload
from test_correction_flow import flow,teacher
from test_learning_coverage import wrong,coverage,no_match
from test_learning import learning,link,review
from test_learning_mapping_batches import BatchClient
from test_curriculum_binding import publish_book,choose
from xueban_backend.correction_models import Entity
from xueban_backend.learning_coverage import mapping_coverage
from xueban_backend.learning_models import LearningOutbox,QuestionKnowledgeLink,LearningEvidence
from xueban_backend.learning_reports import build_snapshot,window
from xueban_backend.models import Homework,Student,StudentGrant


def test_staff_confirmed_error_keeps_staff_authority_and_never_reuses_old_model_no_match(flow):
    case=wrong(flow);client=BatchClient(no_match);learning(flow,client).drain()
    assert coverage(flow)['items'][0]['state']=='no_direct_match'
    teacher(flow);case=review(flow,case)
    result=coverage(flow)
    assert (result['total'],result['staff_count'],result['model_count'])==(1,1,0)
    assert result['items'][0]['source_role']=='staff' and result['items'][0]['state']=='not_linked'
    link(flow,case)
    result=coverage(flow)
    assert (result['linked'],result['unlinked'],result['staff_count'])==(1,0,1)
    assert result['items']==[] and len(client.calls)==1


def test_selecting_new_book_cannot_revive_old_primary_or_no_match(flow):
    case=wrong(flow);learning(flow,BatchClient(no_match)).drain()
    assert coverage(flow)['items'][0]['state']=='no_direct_match'
    teacher(flow);link(flow,case)
    assert coverage(flow)['linked']==1
    publish_book(flow);choose(flow,'test-pep-book-v1')
    assert coverage(flow)['total']==0  # old homework belongs to BNUP, not selected PEP.
    with Session(flow.database.engine) as session,session.begin():
        hw=session.get(Homework,flow.homework_id)
        hw.curriculum={**hw.curriculum,'publisher_code':'PEP','edition':'isbn:9787107382468'}
    result=coverage(flow)
    assert result['graph_version']=='test-pep-book-v1'
    assert (result['total'],result['linked'],result['unlinked'])==(1,0,1)
    assert result['items'][0]['state']=='not_linked'


def test_shanghai_current_attempt_boundary_not_homework_or_decision_date(flow):
    case=wrong(flow)
    local_midnight=datetime(2026,10,20,16,tzinfo=timezone.utc) # Oct 21 00:00 Shanghai.
    with Session(flow.database.engine) as session,session.begin():
        a=session.get(Entity,case['current_attempt_id'])
        a.document={**a.document,'occurred_at':local_midnight.isoformat()}
        session.get(Homework,flow.homework_id).occurred_at=local_midnight-timedelta(days=2)
    start,end=window(local_midnight.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date(),
                     (local_midnight+timedelta(days=1)).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date())
    with Session(flow.database.engine) as session:
        student=session.get(Student,'student_a')
        yes=mapping_coverage(session,student,start=start,end=end)
        no=mapping_coverage(session,student,start=start-timedelta(days=1),end=start)
        assert yes['total']==1 and no['total']==0
        assert yes['time_basis']=='current_attempt_occurred_at'
        report=build_snapshot(session,student,start.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date(),
                              end.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date())
        assert report['mapping_coverage']['total']==1
        assert report['school_tasks']['questions']==0 # the two denominators are deliberately explicit.
        assert 'state' not in report['mapping_coverage']['items'][0]


@pytest.mark.parametrize('change',['grant_revoked','inactive_student'])
def test_authorization_changes_hide_coverage_without_reclassifying_errors(flow,change):
    wrong(flow);assert coverage(flow)['unlinked']==1
    with Session(flow.database.engine) as session,session.begin():
        if change=='grant_revoked':
            row=session.scalar(select(StudentGrant).where(StudentGrant.principal_id=='actor_a',StudentGrant.student_id=='student_a'))
            row.active=False
        else:session.get(Student,'student_a').active=False
    response=flow.client.get('/v1/students/student_a/knowledge')
    assert response.status_code==404
    assert 'mapping_coverage' not in response.text and '18 - 7' not in response.text


def test_repeated_reads_and_snapshots_do_not_write_or_call_mapper(flow):
    wrong(flow);client=BatchClient(no_match);learning(flow,client).drain()
    baseline=coverage(flow);writes=[]
    def sql(_conn,_cursor,statement,_parameters,_context,_many):
        if statement.lstrip().split(None,1)[0].upper() in {'INSERT','UPDATE','DELETE'}:writes.append(statement.split(None,1)[0])
    with Session(flow.database.engine) as session:
        before={cls.__tablename__:len(session.scalars(select(cls)).all()) for cls in (Entity,LearningOutbox,LearningEvidence,QuestionKnowledgeLink)}
    engines={flow.database.engine,flow.client.app.state.engine}
    for engine in engines:event.listen(engine,'before_cursor_execute',sql)
    try:
        with patch.object(client,'generate',side_effect=AssertionError('read_must_not_call_provider')):
            assert coverage(flow)==baseline and coverage(flow)==baseline
            with Session(flow.database.engine) as session:
                student=session.get(Student,'student_a')
                build_snapshot(session,student,datetime(2026,10,16).date(),datetime(2026,11,1).date())
                after={cls.__tablename__:len(session.scalars(select(cls)).all()) for cls in (Entity,LearningOutbox,LearningEvidence,QuestionKnowledgeLink)}
    finally:
        for engine in engines:event.remove(engine,'before_cursor_execute',sql)
    assert writes==[] and before==after and len(client.calls)==1


@pytest.mark.parametrize('change,expected',[('partial',1),('unanswered',0),('service_failed',0),('unclear',0),('stale_pointer',0)])
def test_reliable_current_error_denominator_does_not_guess_other_states(flow,change,expected):
    case=wrong(flow)
    with Session(flow.database.engine) as session,session.begin():
        c=session.get(Entity,case['id']);d=session.get(Entity,case['current_decision']['id'])
        a=session.get(Entity,case['current_attempt_id']);doc=deepcopy(d.document)
        if change=='partial':doc['verdict']='partially_correct'
        elif change=='unanswered':doc['verdict']='unanswered'
        elif change=='service_failed':doc['verification']={**doc['verification'],'status':'degraded','reason_codes':['provider_unavailable']}
        elif change=='unclear':a.document={**a.document,'answer_status':'unclear'}
        elif change=='stale_pointer':doc['validity']='stale'
        d.document=doc;c.document={**c.document,'current_decision':doc}
    result=coverage(flow)
    assert result['total']==result['unlinked']==expected and result['linked']==0


def test_same_student_secondary_only_and_newer_model_cannot_override_staff_rejection(flow):
    case=wrong(flow);teacher(flow)
    first=link(flow,case,role='secondary')
    assert coverage(flow)['linked']==0
    rejected=link(flow,case,status='rejected',expected=first['id'])
    client=BatchClient();learning(flow,client).drain()
    assert coverage(flow)['unlinked']==1
    with Session(flow.database.engine) as session:
        assert session.get(QuestionKnowledgeLink,rejected['id']).status=='rejected'
        assert not [e for e in session.scalars(select(LearningEvidence).where(LearningEvidence.validity=='valid')) if e.document['result'] in {'incorrect','partial'}]


def test_helper_query_count_is_constant_for_one_and_six_current_cases(flow):
    wrong(flow)
    def measured():
        statements=[]
        def seen(_conn,_cursor,statement,_params,_context,_many):
            if statement.lstrip().upper().startswith('SELECT'):statements.append(statement)
        with Session(flow.database.engine) as session:
            student=session.get(Student,'student_a')
            event.listen(flow.database.engine,'before_cursor_execute',seen)
            try:result=mapping_coverage(session,student)
            finally:event.remove(flow.database.engine,'before_cursor_execute',seen)
        return result,len(statements)
    single,one=measured()
    for _ in range(5):flow.ready()
    multi,six=measured()
    assert single['total']==1 and multi['total']==6
    assert one==six
    print('INDEPENDENT_COVERAGE_QUERY_COUNT',flow.database.dialect,one,six)


@pytest.mark.parametrize('field,value',[('protocol','old-protocol'),('graph_version','old-graph'),('status','stale_input')])
def test_no_match_requires_matching_successful_outcome_not_only_job_input(flow,field,value):
    wrong(flow);learning(flow,BatchClient(no_match)).drain()
    assert coverage(flow)['items'][0]['state']=='no_direct_match'
    with Session(flow.database.engine) as session,session.begin():
        job=session.scalar(select(LearningOutbox).where(LearningOutbox.kind=='map_model_batch'))
        saved=deepcopy(job.payload);saved['outcome'][field]=value;job.payload=saved
    assert coverage(flow)['items'][0]['state']=='not_linked'


@pytest.mark.parametrize('field,value',[('attempt_id','attempt_unrelated'),('attempt_recognition_revision',99),('decision_id','decision_unrelated')])
def test_machine_primary_must_belong_to_exact_current_observation(flow,field,value):
    wrong(flow);learning(flow,BatchClient()).drain()
    assert coverage(flow)['linked']==1
    with Session(flow.database.engine) as session,session.begin():
        row=session.scalar(select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.source=='model'))
        bindings=deepcopy(row.observation_bindings);bindings[0][field]=value;row.observation_bindings=bindings
    result=coverage(flow)
    assert result['linked']==0 and result['unlinked']==1
    assert result['items'][0]['state']=='not_linked'
