#!/usr/bin/env python3
"""Independent native R5 regression, real Core processes, no model/network."""
import argparse,importlib.util,json,os,shutil,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]

def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve();sys.path.insert(0,str(repo));checks=[]
 spec=importlib.util.spec_from_file_location('late_binding_fixture',ROOT/'research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 try:
  with tempfile.TemporaryDirectory(prefix='late-binding-') as d:
   c=m.Cases(repo,Path(d).resolve())
   def setup(label,worker='late-native-worker',second=False):
    f=c.fixture(label);proof=c.q.baseline(f);tid=c.q.task(f,'feature');candidates=[c.q.candidate(tid)]
    if second:
     other=c.q.task(f,'other-feature');candidates.append(c.q.candidate(other,finding='b'*64));f['other']=other
    c.q.assessment(f,proof,candidates);s=f['c'].tick();assert s['phase']=='worker_requested';v=s['active_version'];f.update(s=s,v=v,tid=tid,proof=proof)
    return f
   def claim(f,task=None,version=None):
    args={'worker':'late-native-worker'}
    if task:args['task_id']=task
    r=f['c']._core('claim',args,version or f['v']);f['run']=r;f['args']=c.q.owned(r);return r
   def change(f,bad=False):
    (f['repo']/'extra.txt').write_text('real changed candidate\n')
    if bad:(f['repo']/'value.txt').write_text('bad\n')
    c.git(f['repo'],'add','.');c.git(f['repo'],'-c','user.name=Native contract','-c','user.email=native@example.invalid','commit','-qm','actual late-binding worker change')
    f['c']._core('change',f['args'],f['v'])
   def unchanged(f):
    now=f['c'].status();c.same(f['s'],now);assert now['request']['id']==f['s']['request']['id'];assert now['request']['source_sha256']==f['s']['request']['source_sha256'];return now
   def deny(f,request=None,run=None):
    before=f['c'].status();n=f['c']._core_read('SELECT count(*) AS n FROM runs')[0]['n'];c.denied(lambda:f['c'].bind_run(request or f['s']['request']['id'],run or f['run']['id']));after=unchanged(f);assert before['request']==after['request'];assert n==f['c']._core_read('SELECT count(*) AS n FROM runs')[0]['n']
   f=setup('verified-after-change');caps=f['c']._core('capabilities',{},f['v']);assert len(caps['interfaces']['mcp']['tools'])==27 and not caps['host']['autonomous_model_dispatch'];claim(f);change(f);assert f['c']._core('verify',f['args'],f['v'])['passed'];guidance=f['c']._core('next_step',{'run_id':f['run']['id']},f['v']);assert guidance['evidence_valid'] and guidance['source_match'];assert f['run']['baseline']!=c.q.digest(f['repo'])
   c.reopen(f);before=f['c'].tick();assert before['request']['run_id'] is None and before['phase']=='worker_requested';bound=f['c'].bind_run(f['s']['request']['id'],f['run']['id']);assert bound['request']['run_id']==f['run']['id'];unchanged(f);deny(f,run=f['proof'])
   n=f['c']._core_read('SELECT count(*) AS n FROM runs')[0]['n'];c.reopen(f);again=f['c'].bind_run(f['s']['request']['id'],f['run']['id']);assert again['request']==bound['request'];assert n==f['c']._core_read('SELECT count(*) AS n FROM runs')[0]['n'];assert f['c'].tick()['phase']=='in_flight';checks.append('actual changed verified Core run recovered explicitly after lost binding and restart without another run or budget')
   f['c']._core('transition',{**f['args'],'target':'ready_review'},f['v']);review=f['c'].tick();assert review['phase']=='review_requested';c.q.assessment(f,f['run']['id']);f['c']._core('transition',{**f['args'],'target':'accepted'},f['v'])
   receipt={'schema_version':1,'approved':True,'reviewer':'independent-native-late-bind-reviewer','run_id':f['run']['id'],'core_state':f['p']['core_state'],'source_root':f['p']['repo'],'source_sha256':c.q.digest(f['repo']),'controller_package_sha256':f['run']['workflow']};rp=f['root']/'receipt.json';m.helper.write_json(rp,receipt);successor=f['root']/'runtime/v2';successor.mkdir();shutil.copytree(f['repo']/'agent_workflow',successor/'agent_workflow');promoted=f['c'].promote(review['request']['id'],str(rp),m.helper.sha(rp.read_bytes()),str(successor),f['v']['id']);c.same(f['s'],promoted);assert promoted['request'] is None and promoted['active_version']['source_sha256']==c.q.digest(f['repo']);checks.append('late-bound run follows unchanged independent review acceptance receipt and CAS promotion')
   f=setup('ready-review');claim(f);change(f);assert f['c']._core('verify',f['args'],f['v'])['passed'];f['c']._core('transition',{**f['args'],'target':'ready_review'},f['v']);f['c'].bind_run(f['s']['request']['id'],f['run']['id']);unchanged(f);assert f['c'].tick()['phase']=='review_requested';checks.append('ready_review late binding retains original baseline and advances review')
   f=setup('accepted-unbound');claim(f);change(f);assert f['c']._core('verify',f['args'],f['v'])['passed'];f['c']._core('transition',{**f['args'],'target':'ready_review'},f['v']);c.q.assessment(f,f['run']['id']);f['c']._core('transition',{**f['args'],'target':'accepted'},f['v']);deny(f);checks.append('accepted unbound run is explicitly outside this narrow recovery increment')
   f=setup('legacy-running');claim(f);f['c'].bind_run(f['s']['request']['id'],f['run']['id']);unchanged(f);checks.append('unchanged-source running binding remains compatible')
   f=setup('unverified');claim(f);change(f);deny(f);checks.append('changed unverified source cannot be late-bound')
   f=setup('failed-proof');claim(f);change(f,True);assert f['c']._core('verify',f['args'],f['v'])['passed'] is False;deny(f);f['c']._core('fail',{**f['args'],'reason':'actual failed verifier negative'},f['v']);deny(f);checks.append('real failing verifier and failed run remain denied')
   f=setup('source-drift');claim(f);change(f);assert f['c']._core('verify',f['args'],f['v'])['passed'];(f['repo']/'extra.txt').write_text('post-verification drift\n');deny(f);checks.append('post-verification candidate drift rejects old passing proof')
   f=setup('wrong-identities',second=True);claim(f,task=f['other']);deny(f);deny(f,request='request-unknown');deny(f,run='run-unknown');checks.append('wrong requested task request or missing run denied without picking another run')
   f=setup('policy-drift');claim(f);change(f);assert f['c']._core('verify',f['args'],f['v'])['passed'];raw=f['config_path'].read_bytes();f['config_path'].write_bytes(raw+b'\n');before=f['c'].status();c.denied(lambda:f['c'].bind_run(f['s']['request']['id'],f['run']['id']));assert f['c'].status()['request']==before['request'];f['config_path'].write_bytes(raw);unchanged(f);checks.append('frozen Core policy drift denies late binding')
   f=setup('wrong-workflow');alternate=f['root']/'runtime/other';alternate.mkdir();shutil.copytree(Path(f['v']['controller_path'])/'agent_workflow',alternate/'agent_workflow');ip=alternate/'agent_workflow/__init__.py';ip.write_bytes(ip.read_bytes()+b'\n');alt={**f['v'],'controller_path':str(alternate),'package_sha256':c.q.digest(alternate/'agent_workflow')};claim(f,version=alt);assert f['run']['workflow']!=f['v']['package_sha256'];deny(f);checks.append('actual distinct physical controller workflow cannot bind original request')
   f=setup('owner-deadline');claim(f);change(f);assert f['c']._core('verify',f['args'],f['v'])['passed'];f['clock'][0]=f['s']['deadline']+1;c.reopen(f);deny(f);checks.append('simulated elapsed original continuation deadline rejects without renewal')
   # Actual Core lease expiration, short owner-created fixture budget, no database edits.
   original=dict(m.helper.BUDGET);m.helper.BUDGET={**original,'lease_seconds':2}
   try:f=setup('actual-lease-expiry')
   finally:m.helper.BUDGET=original
   claim(f);change(f);assert f['c']._core('verify',f['args'],f['v'])['passed'];time.sleep(max(0,f['run']['lease_until']-time.time())+.05);deny(f);checks.append('actual expired original Core lease rejects despite old passing verification')
  print(json.dumps({'passed':True,'checks':checks,'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 0
 except Exception as ex:
  print(json.dumps({'passed':False,'checks':checks,'error_type':type(ex).__name__,'error':str(ex),'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 1
if __name__=='__main__':sys.exit(main())
