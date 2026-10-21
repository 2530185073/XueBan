"""Every paid whole-page stage must pass the durable dispatch boundary."""
import ast
from pathlib import Path
from threading import BoundedSemaphore, Lock
import time
import inspect
import pytest
from test_whole_page_pipeline import FakeClient,picture
from test_two_model_pipeline import correct
from xueban_backend.providers.two_model import TwoModelWholePagePipeline
from xueban_backend.providers.ark import ProviderError
from xueban_backend.providers import reconciliation


def test_all_direct_generate_edges_share_one_host_boundary():
    tree=ast.parse(inspect.getsource(TwoModelWholePagePipeline))
    functions={n.name:n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    generate_owners=[]
    for name,node in functions.items():
        for child in ast.walk(node):
            if isinstance(child,ast.Call) and isinstance(child.func,ast.Attribute) and child.func.attr=='generate':
                generate_owners.append(name)
    assert generate_owners and set(generate_owners)=={'_generate_with_journal'}


def rig():
    providers=[FakeClient(label,{'items':[]}) for label in ('primary','secondary','review','visual','judge')]
    p=TwoModelWholePagePipeline(providers[0],providers[1],reconciliation_client=providers[2],visual_client=providers[3],judgment_client=providers[4])
    events=[];lock=Lock()
    def no_send(client,image,stage,**kwargs):
        with lock:events.append((stage,kwargs['role'],image.asset_id))
        raise ProviderError('synthetic_barrier_denied')
    p._generate_with_journal=no_send
    a=correct();b=correct(answer_text='-4')
    target=reconciliation.review_target('1',a,b,['answer_conflict'])
    return p,providers,events,target


@pytest.mark.parametrize('stage',['reconciliation','visual_reconciliation','visual_adjudication'])
def test_each_review_variant_reaches_host_boundary_before_provider(stage):
    p,providers,events,target=rig()
    result=p._bounded_review(picture(),[target],BoundedSemaphore(1),time.monotonic()+5,stage=stage)
    assert events==[(stage,'check','asset_original')]
    assert not any(x.calls for x in providers)
    assert result['code']=='synthetic_barrier_denied'
    assert result['metadata'].attempts==()


def test_critical_read_hidden_parallel_branches_both_reach_boundary():
    p,providers,events,_=rig()
    result=p._read_visual_targets(picture(),['1'],critical_labels=['1'])
    assert set(events)=={('visual_readback','recognition','asset_original'),('critical_readback','recognition','asset_original')}
    assert not any(x.calls for x in providers)
    assert result['code']==result['corroboration']['code']=='synthetic_barrier_denied'


def test_semantic_alignment_is_downstream_even_with_recognition_role():
    p,providers,events,_=rig()
    p._align_page(picture(),{'1':correct('1')},{'2':correct('2')})
    assert events==[('semantic_alignment','recognition','asset_original')]
    assert not any(x.calls for x in providers)
