#!/usr/bin/env python3
"""Real Core quality recovery contracts; assessments are owner fixtures, not AI reviews."""
import argparse,concurrent.futures,hashlib,importlib.util,json,shutil,sys,tempfile,time
from pathlib import Path
P=argparse.ArgumentParser();P.add_argument('--installed',action='store_true');P.add_argument('--case',choices=['all','expiry','promotion'],default='all');A=P.parse_args()
ROOT=Path(__file__).resolve().parents[2]
if not A.installed:sys.path.insert(0,str(ROOT))
import agent_workflow
from agent_workflow.hostbridge import HostBridge
from agent_workflow.continuation import Continuation,digest,encoded,sha
SUBJECT=Path(agent_workflow.__file__).parent.parent
spec=importlib.util.spec_from_file_location('quality_recovery_fixture',ROOT/'tests/portable/fixtures/research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def denied(fn,reason=None):
 try:fn()
 except ValueError as e:
  if reason is not None:assert reason in str(e),str(e)
  return str(e)
 raise AssertionError('operation admitted despite '+str(reason))

def main():
 checks=[];calls=[]
 with tempfile.TemporaryDirectory(prefix='iterail-quality-recovery-') as d:
  cases=m.Cases(SUBJECT,Path(d).resolve())
  def setup(name,slots=4,lease=60,attempts=2,run_seconds=120):
   # Test-owner authority is fixed before construction; no initialized policy edits.
   m.helper.BUDGET={'lease_seconds':lease,'run_seconds':run_seconds,'test_seconds':5,'max_attempts':attempts}
   f=cases.fixture(name,slots);f['proof']=cases.q.baseline(f);f['tid']=cases.q.task(f,'reviewed-feature');cases.q.assessment(f,f['proof'],[cases.q.candidate(f['tid'])]);f['b']=HostBridge(f['c']);t=f['b'].reserve();f['ticket']=t;f['base']=f['b'].status();f['v']=f['c'].status()['active_version'];f['b'].begin(t['request_id'])
   run=f['c']._core('claim',{'worker':t['actor'],'task_id':t['task_id']},f['v']);f['run']=run;f['args']=cases.q.owned(run);f['c'].bind_run(t['request_id'],run['id']);f['b'].reconcile(t['request_id']);return f
  def core(f,action,args):
   result=f['c']._core(action,args,f['v']);calls.append({'action':action,'succeeded':True});return result
  def resources(f,spent):
   s=f['b'].status();assert s['requests_spent']==spent and s['deadline']==f['base']['deadline'] and s['started']==f['base']['started']
  def expire(f):
   delay=f['run']['lease_until']-time.time()
   if delay>0:time.sleep(delay+.05)
   assert time.time()>=f['run']['lease_until']
   assert f['b'].reconcile(f['ticket']['request_id'])['phase']=='expired_pending'
  def ready(f):
   # An actual source change that preserves the machine check.
   (f['repo']/'quality-change.txt').write_text('reviewed local change\n')
   cases.git(f['repo'],'add','.');cases.git(f['repo'],'-c','user.name=Quality recovery fixture','-c','user.email=fixture@example.invalid','commit','-qm','Real source candidate')
   core(f,'change',f['args']);assert core(f,'verify',f['args'])['passed'];core(f,'transition',{**f['args'],'target':'ready_review'})
   f['b'].reconcile(f['ticket']['request_id']);r=f['b'].reserve();assert r['kind']=='review';f['review']=f['b'].begin(r['request_id'])
  def assessment(f,**kwargs):return cases.q.assessment(f,f['run']['id'],**kwargs)
  def receipt(f):
   value={'schema_version':1,'approved':True,'reviewer':m.helper.REVIEWER,'run_id':f['run']['id'],'core_state':f['p']['core_state'],'source_root':f['p']['repo'],'source_sha256':digest(f['repo']),'controller_package_sha256':f['run']['workflow']};p=f['root']/'independent-receipt.json';p.write_bytes(encoded(value));target=f['root']/'runtime/accepted';shutil.copytree(f['repo']/'agent_workflow',target/'agent_workflow');return p,sha(p.read_bytes()),target
  def finish(f,receipt):
   p,h,target=receipt;return f['b'].finish(f['review']['request_id'],str(p),h,str(target),f['v']['id'])
  if A.case in {'all','expiry'}:
   f=setup('expiry',lease=12)
   assert not core(f,'next_step',{'run_id':f['run']['id']})['quality']['claim_allowed']
   expire(f)
   # Exact fresh independent baseline assessment still admits this same task.
   cases.q.assessment(f,f['proof'],[cases.q.candidate(f['tid'])]);q=core(f,'next_step',{'run_id':f['run']['id']})
   assert q['next_action']=='claim' and q['quality']['claim_allowed'],q
   fresh=f['b'].retry_expired(f['ticket']['request_id']);assert fresh['retry_of']==f['ticket']['request_id'];resources(f,4)
   assert f['b'].retry_expired(f['ticket']['request_id'])==fresh
   f['b'].begin(fresh['request_id']);old=f['run'];rr=core(f,'claim',{'worker':fresh['actor'],'task_id':f['tid']});f['c'].bind_run(fresh['request_id'],rr['id']);f['run']=rr;f['args']=cases.q.owned(rr);f['ticket']=fresh
   assert core(f,'status',{'run_id':old['id']})['state']=='expired'
   denied(lambda:core(f,'progress',{**cases.q.owned(old),'note':'stale token'}))
   ready(f)
   assessment(f,created_at=time.time()-301);denied(lambda:core(f,'transition',{**f['args'],'target':'accepted'}))
   assessment(f,reviewer=rr['worker']);denied(lambda:core(f,'transition',{**f['args'],'target':'accepted'}))
   assessment(f,reviewer='untrusted-reviewer');denied(lambda:core(f,'transition',{**f['args'],'target':'accepted'}))
   assessment(f,changes={'safety':'block'});denied(lambda:core(f,'transition',{**f['args'],'target':'accepted'}))
   assessment(f);core(f,'transition',{**f['args'],'target':'accepted'});assert finish(f,receipt(f))['phase']=='completed';resources(f,4)
   f['c'].stop('fixture complete');assert f['c'].resume()['stopped'];checks.append('fresh quality expiry admission, charged replacement, real change/verify, stale/self/untrusted/regressed acceptance refusals, valid independent fixture receipt and promotion')
   # Owner ceiling and stale semantic admission must refuse before any fresh ticket.
   for name,slots in [('ceiling',2),('stale',4),('attempts',4),('stop',4),('deadline',4)]:
    f=setup(name,slots=slots,lease=12,attempts=1 if name=='attempts' else 2)
    if name in {'ceiling','stale','attempts'}:expire(f)
    if name=='stale':cases.q.assessment(f,f['proof'],[cases.q.candidate(f['tid'])],created_at=time.time()-301)
    if name=='stop':f['c'].stop('owner fixture stop')
    if name=='deadline':f['clock'][0]=f['base']['deadline']+1
    denied(lambda:f['b'].retry_expired(f['ticket']['request_id']))
    resources(f,2);assert len(f['b'].status()['entries'])==1
    f['c'].stop('fixture complete')
   # Valid Core configurations and heartbeat clamp lease_until to the run
   # deadline. Preserve that invariant rather than injecting impossible DB rows.
   denied(lambda:setup('deadline-before-lease',lease=12,run_seconds=3),'lease/test budget exceeds run budget')
   checks.append('stale assessment, Core attempts, original pair ceiling, active lease, invalid lease/run geometry, stop and simulated owner deadline refuse without spending or policy reset')
  if A.case in {'all','promotion'}:
   for name,change in [('stale-after-accept',{'created_at':time.time()-301}),('regressed-after-accept',{'changes':{'architecture':'block'}}),('wrong-proof-after-accept',{})]:
    f=setup(name);ready(f);assessment(f);core(f,'transition',{**f['args'],'target':'accepted'});rp=receipt(f)
    if name=='wrong-proof-after-accept':change={'proof_run_id':f['proof']}
    assessment(f,**change)
    denied(lambda:finish(f,rp),'fresh independent quality assessment')
    assert f['c'].status()['active_version']['id']==f['v']['id'];resources(f,2)
    assessment(f);assert finish(f,rp)['phase']=='completed';resources(f,2)
    f['c'].stop('fixture complete')
   checks.append('stale, regressed or wrong-run independent assessment AFTER Core acceptance cannot promote; restoring current assessment permits original receipt without new spend')
 print(json.dumps({'passed':True,'checks':checks,'real_Core_operations':len(calls),'owner_assessments':'deterministic fixtures, not independent AI reviews','real_lease_expiry':A.case!='promotion','owner_deadline_simulated':True,'installed':A.installed,'model_dispatches':0,'network_calls':0}))

if __name__=='__main__':main()
