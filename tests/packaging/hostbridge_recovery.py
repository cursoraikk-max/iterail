#!/usr/bin/env python3
"""Real Core/SQLite/process recovery regression; no model/agent dispatch."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

P = argparse.ArgumentParser()
P.add_argument('--installed', action='store_true')
A = P.parse_args()
ROOT = Path(__file__).resolve().parents[2]
if not A.installed:
    sys.path.insert(0, str(ROOT))
import agent_workflow
from agent_workflow.continuation import Continuation, digest, sha, encoded
from agent_workflow.hostbridge import HostBridge

PACKAGE = Path(agent_workflow.__file__).parent
CHECK = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_workflow.continuation import digest
r=Path(sys.argv[1]);before=digest(r)
passed=(r/'value.txt').read_text()=='fixed\\n';stable=before==digest(r)
print(json.dumps(dict(id='fix-value',passed=passed,classification='passed' if passed else 'product_defect',source_stable=stable,exactsourceSHA=before)))
sys.exit(0 if passed and stable else 1)
'''


def denied(fn, expected):
    try:
        fn()
    except ValueError as error:
        assert expected in str(error), str(error)
        return
    raise AssertionError('expected refusal: '+expected)


def fixture(root, label, slots=4, attempts=2, bind=True, claim=True):
    home = root/label
    home.mkdir()
    repo = home/'repo'
    repo.mkdir()
    shutil.copytree(PACKAGE, repo/'agent_workflow', ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (repo/'value.txt').write_text('broken\n')
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    for cmd in [['git','init','-q',str(repo)],['git','-C',str(repo),'add','.'],['git','-C',str(repo),'-c','user.name=Recovery fixture','-c','user.email=fixture@example.invalid','commit','-qm','Seed failure']]:
        subprocess.run(cmd, env=env, check=True, capture_output=True)
    check = home/'check.py'
    check.write_text(CHECK)
    producer = {'schema_version':1,'allowed_sources':['failed_log','unresolved_review','owner_check'], 'registry':{'fix-value':{'argv':[sys.executable,'-B',str(check),'{repo}'],'acceptance':{'exit_code':1,'classification':'product_defect','source_stable':True}}}, 'queue':{'max_open':4,'max_new_per_cycle':2,'wip':1,'aging_seconds':60,'repro_seconds':10,'log_bytes':65536}}
    pp = home/'producer.json'
    pp.write_bytes(encoded(producer))
    config = {'repo':str(repo),'tests':[[sys.executable,'-B',str(check),str(repo)]], 'sources':['local'],'budget':{'lease_seconds':2,'run_seconds':60,'test_seconds':20,'max_attempts':attempts}, 'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'publish':False,'network':False},'producer_policy':{'path':str(pp),'sha256':sha(pp.read_bytes())}}
    cp = home/'core.json'
    cp.write_bytes(encoded(config))
    from agent_workflow.core import Engine
    e = Engine(home/'core', config)
    e.db.close()
    runtime = home/'runtime'
    runtime.mkdir()
    shutil.copytree(repo/'agent_workflow',runtime/'bootstrap/agent_workflow')
    policy = {'schema_version':1,'repo':str(repo),'core_state':str(home/'core'),'core_policy':{'path':str(cp),'sha256':sha(cp.read_bytes())},'runtime_root':str(runtime),'bootstrap':{'controller_path':str(runtime/'bootstrap'),'package_sha256':digest(runtime/'bootstrap/agent_workflow'),'source_sha256':digest(repo),'head':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()},'cadence':{'interval_seconds':1,'backoff_seconds':1,'max_backoff_seconds':4},'budget':{'wall_seconds':120,'max_requests':slots},'adapter':'conversation_pull','external_provider_authorized':False}
    c = Continuation(str(home/'continuation'),policy)
    b = HostBridge(c)
    t = b.reserve()
    b.begin(t['request_id'])
    version = c.status()['active_version']
    run = c._core('claim',{'worker':t['actor'],'task_id':t['task_id']},version) if claim else None
    if bind and claim:
        c.bind_run(t['request_id'],run['id'])
        b.reconcile(t['request_id'])
    return dict(c=c,b=b,t=t,v=version,run=run,home=home,repo=repo)


def reopen(f):
    f['c'].db.close()
    f['c'] = Continuation(str(f['home']/'continuation'))
    f['b'] = HostBridge(f['c'])


def resources(f, initial, spent):
    s = f['b'].status()
    assert s['started']==initial['started'] and s['deadline']==initial['deadline']
    assert s['requests_spent']==spent


def main():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='iterail-expiry-') as d:
        root=Path(d).resolve()
        f=fixture(root,'positive')
        initial=f['b'].status()
        time.sleep(2.1)  # Actual lease expiry, not a DB edit or mocked Core time.
        c,b,t=f['c'],f['b'],f['t']
        assert c.tick()['phase']=='blocked'
        observed=b.reconcile(t['request_id'])
        assert observed['phase']=='expired_pending',observed
        assert observed['run_id']==f['run']['id']
        replacement=b.retry_expired(t['request_id'])
        assert replacement['request_id']!=t['request_id'] and replacement['actor']!=t['actor']
        assert replacement['run_id'] is None and replacement['dispatch_started'] is False
        assert replacement['retry_of']==t['request_id']
        resources(f,initial,4)
        assert b.reconcile(t['request_id'])['phase']=='expired'
        assert b.retry_expired(t['request_id'])==replacement
        denied(lambda:c.bind_run(t['request_id'],f['run']['id']),'pending worker request required')
        denied(lambda:b.begin(t['request_id']),'current durable request identity required')
        reopen(f);c,b=f['c'],f['b']
        assert b.retry_expired(t['request_id'])==replacement
        assert b.reserve()==replacement
        # Simultaneous retries only replay one atomically committed replacement.
        def replay():
            local=Continuation(str(f['home']/'continuation'))
            try:return HostBridge(local).retry_expired(t['request_id'])
            finally:local.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda _:replay(),range(2)))==[replacement,replacement]
        b.begin(replacement['request_id'])
        rr=c._core('claim',{'worker':replacement['actor'],'task_id':replacement['task_id']},f['v'])
        assert rr['id']!=f['run']['id']
        c.bind_run(replacement['request_id'],rr['id'])
        args={'run_id':rr['id'],'token':rr['token']}
        (f['repo']/'value.txt').write_text('fixed\n')
        c._core('change',args,f['v'])
        assert c._core('verify',args,f['v'])['passed']
        c._core('transition',{**args,'target':'ready_review'},f['v'])
        review=b.reserve()
        assert review['kind']=='review' and review['run_id']==rr['id']
        assert c._core('status',{'run_id':f['run']['id']},f['v'])['state']=='expired'
        resources(f,initial,4)
        assert rr['token'] not in json.dumps(b.status())
        checks.append('actual lease expiry -> durable fresh charged ticket -> real Core claim/verify/ready_review; replay/reopen/parallel replay fenced')
        # Batch real expiry for negative cases; owner grants are frozen at creation.
        cases={k:fixture(root,k,slots=2 if k=='ceiling' else 4,attempts=1 if k=='attempts' else 2,bind=k!='unbound',claim=k!='unknown') for k in ['ceiling','attempts','drift','stopped','deadline','parallel','foreign','unbound','unknown','policy']}
        time.sleep(2.1)
        for label,reason in [('ceiling','owner request ceiling'),('attempts','Core retry admission'),('drift','unchanged request source'),('stopped','stopped or expired'),('deadline','stopped or expired'),('foreign','existing Core checkout lease')]:
            f=cases[label];before=f['b'].status()
            if label=='drift':(f['repo']/'value.txt').write_text('unreviewed\n')
            if label=='stopped':f['c'].stop('owner stop')
            if label=='deadline':f['c'].clock=lambda:before['deadline']+1  # Explicit owner-clock simulation only.
            if label=='foreign':f['c']._core('claim',{'worker':'outside-ticket','task_id':f['t']['task_id']},f['v'])
            denied(lambda:f['b'].retry_expired(f['t']['request_id']),reason)
            resources(f,before,2)
            assert len(f['b'].status()['entries'])==1
        checks.append('ceiling2, Core attempt exhaustion, source drift, owner stop/deadline and out-of-band active lease all refuse without refund or new ticket')
        f=cases['parallel'];initial=f['b'].status()
        def race():
            local=Continuation(str(f['home']/'continuation'))
            try:return HostBridge(local).retry_expired(f['t']['request_id'])
            finally:local.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:race(),range(2)))
        assert results[0]==results[1]
        resources(f,initial,4)
        assert len(f['b'].status()['entries'])==2
        checks.append('two concurrent initial retries reserve one fresh pair, not two')
        # Lost callback before first bind: match exact original actor, never newest run.
        f=cases['unbound']
        assert f['c'].status()['request']['run_id'] is None
        assert f['b'].reconcile(f['t']['request_id'])['phase']=='expired_pending'
        fresh=f['b'].retry_expired(f['t']['request_id'])
        assert fresh['retry_of']==f['t']['request_id']
        resources(f,f['b'].status(),4)
        f=cases['unknown']
        assert f['b'].reconcile(f['t']['request_id'])['phase']=='uncertain'
        denied(lambda:f['b'].retry_expired(f['t']['request_id']),'reconciled expired worker required')
        assert f['b'].status()['requests_spent']==2
        f=cases['policy'];cp=f['home']/'core.json';original=cp.read_bytes();cp.write_bytes(original+b'\n')
        denied(lambda:f['b'].retry_expired(f['t']['request_id']),'frozen Core policy SHA changed')
        cp.write_bytes(original)
        active=fixture(root,'active')
        denied(lambda:active['b'].retry_expired(active['t']['request_id']),'expired worker outcome required')
        checks.append('lost initial bind reconciles exact expired outcome; unknown outcome, active lease and policy drift refuse without new authority')
        for f in [*cases.values(), active]:
            if not f['c'].status()['stopped']:f['c'].stop('regression fixture complete')
    print(json.dumps({'passed':True,'checks':checks,'actual_core_processes':True,'actual_lease_expiry':True,'owner_deadline_simulated':True,'agent_dispatches':0,'model_calls':0,'installed':A.installed}))


if __name__=='__main__':
    main()
