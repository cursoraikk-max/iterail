#!/usr/bin/env python3
"""Real local scope/Core/CLI/MCP process regressions; no model/host scheduler."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

p=argparse.ArgumentParser();p.add_argument('--installed',action='store_true');p.add_argument('--baseline');p.add_argument('--worker',nargs=3);a=p.parse_args()
ROOT=Path(__file__).resolve().parents[2]
if a.baseline:sys.path.insert(0,a.baseline)
elif not a.installed:sys.path.insert(0,str(ROOT))
from agent_workflow.core import Engine,WorkflowError
from agent_workflow.mcp import SCOPE_FIELDS
assert 'scope_preflight' in SCOPE_FIELDS, 'missing shared-scope preflight API: baseline cannot enforce this contract'
from agent_workflow.scope import Scope,initialize


def denied(fn,text):
    try:fn()
    except (ValueError,WorkflowError) as e:assert text in str(e),(text,str(e));return
    raise AssertionError('unexpected admission: '+text)


def setup(root,name,dispatches=4,repairs=2,deadline=None,tests=None,requirements=None):
    home=root/name;home.mkdir();repo=home/'repo';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True,capture_output=True)
    tests=tests or [[sys.executable,'-c','pass']]
    policy={'schema_version':1,'scope_id':name,'repo':str(repo),'ledger':str(home/'owner/scope.sqlite'),
            'started':time.time()-1,'deadline':deadline or time.time()+120,'max_dispatches':dispatches,'max_repairs':repairs,
            'python':sys.executable,'build_requirements':requirements or {},'tests':tests}
    path=home/'owner.json';path.write_text(json.dumps(policy));descriptor={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    initialize(descriptor)
    config={'repo':str(repo),'tests':tests,'sources':['local'],'budget':{'lease_seconds':60,'run_seconds':120,'test_seconds':10,'max_attempts':5},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'network':False,'publish':False},'engineering_scope':descriptor}
    engines=[Engine(home/'state-a',config),Engine(home/'state-b',config)]
    tasks=[e.import_task('same-work','same immutable work') for e in engines]
    return {'home':home,'repo':repo,'p':policy,'d':descriptor,'c':config,'engines':engines,'tasks':tasks}


def state(f):return f['engines'][0].scope_status()
def close(f):
    for e in f['engines']:e.db.close()
def owned(run):return {'run_id':run['id'],'token':run['token']}


def main():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='iterail-scope-contract-') as temp:
        root=Path(temp).resolve()
        f=setup(root,'cross-state',3,1);x,y=f['engines'];tx,ty=f['tasks'];first=x.claim('worker',tx['id'],'request-1');start=state(f)
        unscoped=dict(f['c']);unscoped.pop('engineering_scope')
        denied(lambda:Engine(f['home']/'unscoped',unscoped),'descriptor cannot be omitted')
        assert first['deadline']<=f['p']['deadline'];assert x.claim('worker',tx['id'],'request-1')['id']==first['id']
        denied(lambda:x.claim('other',tx['id'],'request-1'),'payload conflict')
        denied(lambda:y.claim('worker',ty['id'],'request-1'),'original state')
        denied(lambda:y.claim('worker',ty['id'],'request-2'),'active or unknown')
        x.fail(**owned(first),reason='known local worker outcome')
        x.db.close();x=Engine(f['home']/'state-a');f['engines'][0]=x
        second=y.claim('worker',ty['id'],'request-2');now=x.scope_status();assert now['dispatches']==2 and now['repairs']==1
        assert now['started']==start['started'] and now['deadline']==start['deadline']
        denied(lambda:y.scope_repair(**owned(second),repair_id='fix-over-cap',reason='another repair'),'repair budget exhausted')
        y.fail(**owned(second),reason='known terminal')
        denied(lambda:x.claim('worker',tx['id'],'request-3'),'repair budget exhausted');assert state(f)['dispatches']==2
        checks.append('cross-state/restart fixed deadline, replay/collision, one active scope, cumulative retry repair cap');close(f)

        f=setup(root,'manual-repairs',2,1);e=f['engines'][0];run=e.claim('worker',f['tasks'][0]['id'],'request')
        one=e.scope_repair(**owned(run),repair_id='repair-1',reason='fixed regression');two=e.scope_repair(**owned(run),repair_id='repair-1',reason='fixed regression');assert one['repairs']==two['repairs']==1
        denied(lambda:e.scope_repair(**owned(run),repair_id='repair-1',reason='changed payload'),'payload conflict')
        denied(lambda:e.scope_repair(**owned(run),repair_id='repair-2',reason='another fix'),'repair budget exhausted')
        checks.append('within-run repair units idempotent and capped independently of duplicate calls');close(f)

        f=setup(root,'dispatch-cap',1,5);e=f['engines'][0];run=e.claim('worker',f['tasks'][0]['id'],'r1');e.fail(**owned(run),reason='known terminal')
        denied(lambda:f['engines'][1].claim('worker',f['tasks'][1]['id'],'r2'),'dispatch budget exhausted');assert state(f)['dispatches']==1
        checks.append('dispatch exhaustion denies another state without debit');close(f)

        f=setup(root,'parallel',2,2);e=f['engines'][0];task=f['tasks'][0]['id']
        cmd=[sys.executable,'-I','-B',str(__file__),'--worker',str(e.state),task,'parallel-key']
        if not a.installed:cmd[1:1]=[]  # script selects the explicit source root itself
        children=[subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        outputs=[]
        for child in children:
            out,err=child.communicate(timeout=20);assert child.returncode==0,(out,err);outputs.append(json.loads(out))
        assert outputs[0]['id']==outputs[1]['id'] and state(f)['dispatches']==1
        assert e.db.execute('SELECT count(*) FROM runs').fetchone()[0]==1
        checks.append('two real processes same request create exactly one debit and Core run');close(f)

        f=setup(root,'lost-bind',2,2);e=f['engines'][0];actual=e._claim
        def lose(*args,**kwargs):
            actual(*args,**kwargs);raise OSError('fixture: lost reply after local commit')
        with patch.object(e,'_claim',side_effect=lose):denied(lambda:e.claim('worker',f['tasks'][0]['id'],'lost'),'lost reply')
        e.db.close();e=Engine(f['home']/'state-a');f['engines'][0]=e;recovered=e.claim('worker',f['tasks'][0]['id'],'lost')
        assert recovered['id'] and state(f)['dispatches']==1 and e.db.execute('SELECT count(*) FROM runs').fetchone()[0]==1
        checks.append('committed local link recovers lost bind reply after restart without second debit');close(f)

        f=setup(root,'unknown-gap',3,2);e=f['engines'][0]
        with patch.object(e,'_claim',side_effect=OSError('fixture: before local commit')):denied(lambda:e.claim('worker',f['tasks'][0]['id'],'uncertain'),'before local commit')
        denied(lambda:e.claim('worker',f['tasks'][0]['id'],'uncertain'),'outcome unknown')
        denied(lambda:f['engines'][1].claim('worker',f['tasks'][1]['id'],'fresh'),'active or unknown')
        assert state(f)['dispatches']==1 and state(f)['requests'][0]['phase']=='uncertain'
        checks.append('precommitted unknown gap blocks same/different state request, no refund')
        receipt=f['home']/'receipt.json';receipt.write_text(json.dumps({'scope_id':f['p']['scope_id'],'request_id':'uncertain','origin_state':str(e.state),'outcome':'not_started','reason':'owner fixture confirmed fault before local claim'}))
        command=[sys.executable,'-B','-m','agent_workflow.scope','resolve-not-started','--policy',f['d']['path'],'--sha256',f['d']['sha256'],'--receipt',str(receipt),'--receipt-sha256',hashlib.sha256(receipt.read_bytes()).hexdigest()]
        env=dict(os.environ,PYTHONPATH=str(ROOT)) if not a.installed else dict(os.environ)
        result=subprocess.run(command,env=env,text=True,capture_output=True,timeout=10);assert result.returncode==0,(result.stdout,result.stderr)
        assert state(f)['dispatches']==1
        actual=e.claim('worker',f['tasks'][0]['id'],'next-known');assert actual['id'] and state(f)['dispatches']==2
        data=json.loads(receipt.read_text());data['request_id']='next-known';receipt.write_text(json.dumps(data));command[-1]=hashlib.sha256(receipt.read_bytes()).hexdigest()
        result=subprocess.run(command,env=env,text=True,capture_output=True,timeout=10);assert result.returncode==1 and 'actual Core run exists' in result.stdout
        assert state(f)['dispatches']==2
        checks.append('owner not-started receipt preserves spend; actual linked run cannot be dismissed');close(f)

        f=setup(root,'parallel-different',3,2)
        children=[subprocess.Popen([sys.executable,'-I','-B',str(__file__),'--worker',str(e.state),task['id'],'different-'+str(i)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i,(e,task) in enumerate(zip(f['engines'],f['tasks']))]
        results=[child.communicate(timeout=20) for child in children]
        assert sorted(child.returncode for child in children)==[0,1],results
        assert state(f)['dispatches']==1 and sum(e.db.execute('SELECT count(*) FROM runs').fetchone()[0] for e in f['engines'])==1
        assert any('active or unknown' in err for _,err in results)
        checks.append('two different state processes/request IDs serialize to one admission and one fenced denial');close(f)

        f=setup(root,'owner-binding',2,2);e=f['engines'][0];ledger=Path(f['p']['ledger']);saved=ledger.with_suffix('.saved');ledger.rename(saved)
        denied(lambda:initialize(f['d']),'never recreate')
        denied(lambda:e.claim('worker',f['tasks'][0]['id'],'missing-ledger'),'ledger missing');assert not ledger.exists();saved.rename(ledger)
        other=dict(f['p']);other['ledger']=str(f['home']/'new-ledger.sqlite');path=f['home']/'other-owner.json';path.write_text(json.dumps(other));descriptor={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        denied(lambda:initialize(descriptor),'cannot be repinned');assert not Path(other['ledger']).exists()
        marker=f['repo']/'.git/iterail-engineering-scope.json';raw=marker.read_bytes();marker.unlink()
        legacy=dict(f['c']);legacy.pop('engineering_scope');old=Engine(f['home']/'prebinding-state',legacy);task=old.import_task('legacy','prebinding work')
        initialize(f['d']);assert marker.read_bytes()==raw
        denied(lambda:old.claim('worker',task['id']),'descriptor cannot be omitted');old.db.close()
        assert state(f)['dispatches']==0
        checks.append('missing ledger/repin and already-open unscoped Engine fail closed; owner-only orphan marker completion preserves ledger');close(f)

        f=setup(root,'wall',2,2,deadline=time.time()+1.2);e=f['engines'][0];run=e.claim('worker',f['tasks'][0]['id'],'first')
        while time.time()<=f['p']['deadline']+.01:time.sleep(.02)
        assert e.claim('worker',f['tasks'][0]['id'],'first')['id']==run['id']
        denied(lambda:f['engines'][1].claim('worker',f['tasks'][1]['id'],'late'),'absolute deadline')
        assert state(f)['dispatches']==1
        checks.append('actual wall expiry denies fresh work while historical request replays without dispatch');close(f)

        # Native Node capability fixture; actual test argv would leave sentinel.
        node=root/'node-fixture';marker=root/'PROJECT_TEST_EXECUTED'
        def write_node(version):
            node.write_text('#!/bin/sh\ncase "$1" in\n--version) echo v'+version+'.0.0;;\n--help) echo "  --test  run tests";;\n*) touch "'+str(marker)+'";;\nesac\n');node.chmod(0o700)
        write_node('20');f=setup(root,'environment-drift',2,2,tests=[[str(node),'--test']]);e=f['engines'][0]
        write_node('16');denied(lambda:setup(root,'unsupported-node',tests=[[str(node),'--test']]),'preflight blocked')
        node.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\necho v16.0.0\n');node.chmod(0o700)
        check=e.scope_preflight();assert not check['passed'] and not marker.exists(),'changed executable ran a probe before drift refusal'
        denied(lambda:e.claim('worker',f['tasks'][0]['id'],'bad-env'),'preflight blocked');assert state(f)['dispatches']==0
        assert not e.db.execute('SELECT 1 FROM runs').fetchone()
        checks.append('frozen Node capability/executable drift blocks before debit; actual test argv never executes');close(f)

        home=root/'missing-dependency';home.mkdir();repo=home/'repo';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True,capture_output=True)
        p={'schema_version':1,'scope_id':'missing-dependency','repo':str(repo),'ledger':str(home/'scope.sqlite'),'started':time.time()-1,'deadline':time.time()+60,'max_dispatches':1,'max_repairs':0,'python':sys.executable,'build_requirements':{'iterail-nonexistent-build-fixture':'1'},'tests':[[sys.executable,'-c','pass']]}
        path=home/'owner.json';path.write_text(json.dumps(p));d={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        denied(lambda:initialize(d),'preflight blocked');assert not Path(p['ledger']).exists()
        checks.append('selected-interpreter missing distribution blocks owner freeze without ledger or dispatch')

        f=setup(root,'deadline-at-commit',2,2,deadline=time.time()+1.0);e=f['engines'][0]
        from agent_workflow import core as core_module
        actual_digest=core_module.digest
        def slow_digest(path):
            result=actual_digest(path)
            if Path(path)==f['repo']:time.sleep(1.1)
            return result
        with patch.object(core_module,'digest',side_effect=slow_digest):
            denied(lambda:e.claim('worker',f['tasks'][0]['id'],'too-late'),'scope absolute deadline')
        assert not e.db.execute('SELECT 1 FROM runs').fetchone() and state(f)['dispatches']==1
        checks.append('real elapsed digest crosses deadline: no local run, original predebit remains uncertain');close(f)

        f=setup(root,'transports',2,2);e=f['engines'][0];e.db.close();f['engines'][0]=Engine(e.state)
        prefix=[sys.executable,'-I','-B','-m','agent_workflow','--state',str(e.state)]
        # Source commands get exact physical module path, installed commands remain -I.
        if not a.installed:
            prefix=[sys.executable,'-B','-m','agent_workflow','--state',str(e.state)]
        env=dict(os.environ,PYTHONPATH=str(ROOT)) if not a.installed else dict(os.environ)
        cli=subprocess.run(prefix+['scope_status'],env=env,text=True,capture_output=True,check=True);cli_status=json.loads(cli.stdout)['result']
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'scope_status','arguments':{}}}]
        proc=subprocess.run(prefix+['mcp'],env=env,input=''.join(json.dumps(x)+'\n' for x in messages),text=True,capture_output=True,check=True);response=json.loads(proc.stdout.splitlines()[-1]);mcp_status=json.loads(response['result']['content'][0]['text'])
        assert cli_status==mcp_status and cli_status['dispatches']==0
        checks.append('real CLI/MCP stdio scope status share authoritative ledger');close(f)
    print(json.dumps({'passed':True,'checks':checks,'installed':a.installed,'external_model_calls':0,'host_wakeup_verified':False,'Node_check':'native capability fixture; not a real Node16 process','unknown_gap':'explicit fault injection; no external backend'}))


if a.worker:
    state_path,task_id,request=a.worker;e=Engine(state_path);run=e.claim('worker',task_id,request);print(json.dumps({'id':run['id']}));e.db.close()
else:main()
