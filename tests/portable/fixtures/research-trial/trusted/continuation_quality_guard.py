#!/usr/bin/env python3
"""Native44 draft; local real Core operations, owner-authored simulated assessments.
No provider/worker dispatch. Freeze only after R1 accepted baseline and independent review.
"""
import argparse, copy, importlib.util, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
HERE=Path(__file__).resolve()
HELPER=HERE.parents[2]/'automation-trial/trusted/quality_guard.py'
spec=importlib.util.spec_from_file_location('trusted_quality_fixture',HELPER)
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

class Cases:
 def __init__(self,repo,root):
  self.q=helper.Contract(repo,root);self.repo=repo;self.results=[];self.n=0
  from agent_workflow.continuation import Continuation,ContinuationError
  self.C,self.Error=Continuation,ContinuationError
 def git(self,repo,*args):
  r=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True);return r.stdout.strip()
 def fixture(self,label,slots=8):
  f=self.q.fixture(label);f['e'].db.close();shutil.rmtree(f['root']/'state')
  shutil.copytree(self.repo/'agent_workflow',f['repo']/'agent_workflow',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
  f['policy']['bootstrap']['source_sha256']=self.q.digest(f['repo'])
  f['policy_path'].chmod(0o644);helper.write_json(f['policy_path'],f['policy']);f['policy_path'].chmod(0o444)
  f['config']['quality_policy']['sha256']=helper.sha(f['policy_path'].read_bytes())
  helper.write_json(f['config_path'],f['config']);f['e']=self.q.Engine(f['root']/'state',f['config'])
  self.git(f['repo'],'add','.');self.git(f['repo'],'-c','user.name=Native contract','-c','user.email=native@example.invalid','commit','-qm','native fixture')
  runtime=f['root']/'runtime';runtime.mkdir();controller=runtime/'v1';controller.mkdir();shutil.copytree(f['repo']/'agent_workflow',controller/'agent_workflow')
  self.now=time.time();f['clock']=[self.now]
  p={'schema_version':1,'repo':str(f['repo']),'core_state':str(f['root']/'state'),
     'core_policy':{'path':str(f['config_path']),'sha256':helper.sha(f['config_path'].read_bytes())},
     'runtime_root':str(runtime),'bootstrap':{'controller_path':str(controller),
       'package_sha256':self.q.digest(controller/'agent_workflow'),'source_sha256':self.q.digest(f['repo']),'head':self.git(f['repo'],'rev-parse','HEAD')},
     'cadence':{'interval_seconds':5,'backoff_seconds':2,'max_backoff_seconds':8},
     'budget':{'wall_seconds':120,'max_requests':slots},'adapter':'conversation_pull','external_provider_authorized':False}
  f['p']=p;f['continuation']=f['root']/'continuation';f['c']=self.C(str(f['continuation']),p,clock=lambda:f['clock'][0]);return f
 def reopen(self,f):
  f['c'].db.close();f['c']=self.C(str(f['continuation']),clock=lambda:f['clock'][0]);return f['c']
 def denied(self,fn):
  try:fn()
  except (self.Error,ValueError,TypeError):return
  raise AssertionError('unexpected admission')
 def current(self,f):return f['c'].status()
 def review(self,f):
  proof=self.q.baseline(f);s=f['c'].tick();assert s['phase']=='quality_review_requested',s
  r=s['request'];assert r['kind']=='quality_review' and r['run_id'] is None
  assert s['requests_spent']==1;return proof,s
 def same(self,a,b):
  for k in ('started','deadline','requests_spent'):assert a[k]==b[k],k
 def run(self):
  # 1: quality-only constructor/schema. Legacy coverage inherits unchanged Core42 continuation gate.
  f=self.fixture('schema');assert f['c'].status()['requests_spent']==0
  p=copy.deepcopy(f['p']);p['budget']['new_counter']=1
  self.denied(lambda:self.C(str(f['root']/'bad'),p));self.results.append('1 quality-only constructor/schema; unchanged legacy gate inherited')
  f=self.fixture('empty-anchor');s=f['c'].tick();assert s['phase']=='blocked' and s['request'] is None and s['requests_spent']==0
  assert f['e'].db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0 and f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]==0
  # 2: true physical Core default claim/bind/review/accept/promotion, no agent fiction.
  f=self.fixture('value');proof=self.q.baseline(f);tid=self.q.task(f,'feature');self.q.assessment(f,proof,[self.q.candidate(tid)])
  s=f['c'].tick();assert s['phase']=='worker_requested' and s['request']['task_id']==tid and s['requests_spent']==2,s
  v=s['active_version'];run=f['c']._core('claim',{'worker':'native-fixture-worker'},v);assert run['task_id']==tid
  f['c'].bind_run(s['request']['id'],run['id']);args=self.q.owned(run)
  f['c']._core('change',args,v);assert f['c']._core('verify',args,v)['passed']
  f['c']._core('transition',{**args,'target':'ready_review'},v);review=f['c'].tick();assert review['phase']=='review_requested'
  self.q.assessment(f,run['id']);f['c']._core('transition',{**args,'target':'accepted'},v)
  receipt={'schema_version':1,'approved':True,'reviewer':'independent-native-reviewer','run_id':run['id'],
   'core_state':f['p']['core_state'],'source_root':f['p']['repo'],'source_sha256':self.q.digest(f['repo']),
   'controller_package_sha256':run['workflow']}
  path=f['root']/'receipt.json';helper.write_json(path,receipt)
  successor=f['root']/'runtime/v2';successor.mkdir();shutil.copytree(f['repo']/'agent_workflow',successor/'agent_workflow')
  promoted=f['c'].promote(review['request']['id'],str(path),helper.sha(path.read_bytes()),str(successor),v['id'])
  assert promoted['request'] is None and promoted['active_version']['id']!=v['id'];assert promoted['requests_spent']==2
  self.results.append('2 native default Core claim/bind/verify/review/accept/CAS promotion')
  # 3: stable nonpromotable semantic review, missing report costs one original slot.
  f=self.fixture('pending');proof,s=self.review(f);rid=s['request']['id'];self.reopen(f);again=f['c'].tick()
  assert again['request']['id']==rid;self.same(s,again)
  self.denied(lambda:f['c'].bind_run(rid,proof));self.denied(lambda:f['c'].promote(rid,'missing','0'*64,'missing',s['active_version']['id']))
  assert f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]==1
  self.results.append('3 stable cost1 review/restart/nonpromotable/no new Run')
  live=self.fixture('live-review');base=self.q.baseline(live);tid=self.q.task(live,'inflight');self.q.assessment(live,base,[self.q.candidate(tid)])
  rr=live['e'].claim('native-live-worker',tid);aa=self.q.owned(rr);live['e'].change(**aa);assert live['e'].verify(**aa)['passed']
  for state in ('verified','ready_review'):
   if state=='ready_review':live['e'].transition(**aa,target=state)
   ss=live['c'].tick();assert ss['phase']=='blocked' and ss['request'] is None and ss['requests_spent']==0,ss
  assert live['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]==2
  # 4: resolution requires fresh independent assessment, exact CLI input.
  self.denied(lambda:f['c'].resolve_quality_review('request-invalid'))
  self.denied(lambda:f['c'].resolve_quality_review(rid))
  self.q.assessment(f,proof,created_at=time.time()-301);self.denied(lambda:f['c'].resolve_quality_review(rid))
  self.q.assessment(f,proof,reviewer='worker');self.denied(lambda:f['c'].resolve_quality_review(rid))
  self.q.assessment(f,proof);resolved=f['c'].resolve_quality_review(rid);assert resolved['request'] is None;self.same(s,resolved)
  cmd=[sys.executable,'-B','-m','agent_workflow.continuation','--state',str(f['continuation']),'resolve_quality_review','--args',json.dumps({'request_id':rid,'approved':True})]
  env=dict(os.environ,PYTHONPATH=str(self.repo));r=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=10)
  assert r.returncode!=0 and json.loads(r.stdout)['ok'] is False
  self.results.append('4 stale/self/missing review reject; fresh resolve; CLI extra bool rejected')
  # 5: source, policy and controller drift never replaces pending identity/authority.
  f=self.fixture('drift');proof,s=self.review(f);rid=s['request']['id'];self.q.assessment(f,proof)
  targets=[f['repo']/'value.txt',f['config_path'],Path(s['active_version']['controller_path'])/'agent_workflow/__init__.py',f['policy_path']]
  for path in targets:
   raw=path.read_bytes();path.chmod(0o644);path.write_bytes(raw+b'\n')
   b=f['c'].tick();assert b['phase']=='blocked' and b['request']['id']==rid;self.same(s,b)
   self.denied(lambda:f['c'].resolve_quality_review(rid));path.write_bytes(raw)
  assert f['c'].resolve_quality_review(rid)['request'] is None
  self.results.append('5 pending source/Corepolicy/controller/quality-policy drift blocked/restored')
  # 6: original resource ceilings immutable across restart and new assessment.
  f=self.fixture('limits',1);proof,s=self.review(f);tid=self.q.task(f,'value');self.q.assessment(f,proof,[self.q.candidate(tid)])
  f['c'].resolve_quality_review(s['request']['id']);b=f['c'].tick();assert b['phase']=='blocked' and b['request'] is None and b['requests_spent']==1
  self.reopen(f);b=f['c'].tick();assert b['requests_spent']==1 and b['deadline']==s['deadline']
  f=self.fixture('deadline');proof,s=self.review(f);self.q.assessment(f,proof);f['clock'][0]=s['deadline']+1
  self.reopen(f);b=f['c'].tick();assert b['phase']=='blocked' and b['request']['id']==s['request']['id'];self.same(s,b)
  self.denied(lambda:f['c'].resolve_quality_review(s['request']['id']));self.results.append('6 exhausted slots/wall deadline no renewal')
  # 7: pause reopens for new finding, stale allows review only; explicit user stop hard.
  f=self.fixture('pause');proof=self.q.baseline(f);self.q.assessment(f,proof);s=f['c'].tick();assert s['phase']=='stop_no_value' and not s['stopped'] and s['request'] is None
  self.reopen(f);f['clock'][0]+=6;assert f['c'].tick()['phase']=='stop_no_value'
  self.q.assessment(f,proof,created_at=time.time()-301);f['clock'][0]+=6;s=f['c'].tick();assert s['phase']=='quality_review_requested' and s['requests_spent']==1
  tid=self.q.task(f,'novel');self.q.assessment(f,proof,[self.q.candidate(tid)]);f['c'].resolve_quality_review(s['request']['id']);f['clock'][0]+=6
  n=f['c'].tick();assert n['phase']=='worker_requested' and n['requests_spent']==3 and n['deadline']==s['deadline']
  f['c'].stop('explicit user stop');self.reopen(f);assert f['c'].tick()['stopped'];assert f['c'].resume()['stopped'];f['c'].clear_stop('explicit owner resume');assert not f['c'].status()['stopped']
  self.results.append('7 no-value pause/stale semantic review/new value/user hard stop')
  # 8: durable due and advanced-clock long restore, native simulation only.
  f=self.fixture('due');proof=self.q.baseline(f);self.q.assessment(f,proof);s=f['c'].tick();assert s['phase']=='stop_no_value'
  calls=[];actual_core=f['c']._core
  def logged_core(action,args,version):
   calls.append(action);return actual_core(action,args,version)
  f['c']._core=logged_core;n=f['c'].tick();assert n['phase']=='not_due' and calls==[],(n,calls)
  self.reopen(f);f['clock'][0]+=6;assert f['c'].tick()['phase']=='stop_no_value'
  f['clock'][0]=s['deadline']+10000;self.reopen(f);n=f['c'].tick();assert n['phase']=='blocked' and n['deadline']==s['deadline'] and n['requests_spent']==s['requests_spent']
  f=self.fixture('transport-fault');proof=self.q.baseline(f);self.q.assessment(f,proof)
  actual=f['c']._core
  def injected_timeout(action,args,version):
   if action=='next_step':raise subprocess.TimeoutExpired('native injected transport timeout',0)
   return actual(action,args,version)
  f['c']._core=injected_timeout;b=f['c'].tick();assert b['phase']=='backoff' and b['request'] is None
  due=b['next_due'];self.reopen(f);assert f['c'].tick()['phase']=='not_due'
  assert f['c'].status()['next_due']==due;f['clock'][0]=due+1;assert f['c'].tick()['phase']=='stop_no_value'
  self.results.append('8 native advanced-clock due/long restore; injected transport fault durable backoff')

def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();cases=None
 try:
  with tempfile.TemporaryDirectory(prefix='native44-draft-') as d:
   cases=Cases(a.repo.resolve(),Path(d).resolve());cases.run()
  print(json.dumps({'passed':True,'cases':cases.results,'native_simulation':True,'worker_dispatches':0,'network_calls':0,'model_calls':0}));return 0
 except Exception as e:
  print(json.dumps({'passed':False,'completed_cases':cases.results if cases else [],'error_type':type(e).__name__,'error':str(e),'native_simulation':True,'worker_dispatches':0,'network_calls':0,'model_calls':0}));return 1
if __name__=='__main__':sys.exit(main())
