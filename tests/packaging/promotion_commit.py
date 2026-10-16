#!/usr/bin/env python3
"""Deterministic promotion-boundary races using real Core operations and SQLite WAL."""
import argparse,contextlib,importlib.util,json,shutil,sqlite3,sys,tempfile,time
from pathlib import Path
P=argparse.ArgumentParser();P.add_argument('--installed',action='store_true');P.add_argument('--scenario',default='all');A=P.parse_args()
ROOT=Path(__file__).resolve().parents[2]
if not A.installed:sys.path.insert(0,str(ROOT))
import agent_workflow
import agent_workflow.quality as quality
from agent_workflow.continuation import digest,encoded,sha
from agent_workflow.hostbridge import HostBridge
from agent_workflow.core import Engine
SUBJECT=Path(agent_workflow.__file__).parent.parent
spec=importlib.util.spec_from_file_location('commit_quality_fixture',ROOT/'tests/portable/fixtures/research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def main():
 results=[]
 with tempfile.TemporaryDirectory(prefix='iterail-commit-fence-') as d:
  cases=m.Cases(SUBJECT,Path(d).resolve())
  def setup(name):
   f=cases.fixture(name,4);proof=cases.q.baseline(f);tid=cases.q.task(f,'first-work');cases.q.assessment(f,proof,[cases.q.candidate(tid)]);c=f['c'];b=HostBridge(c);ticket=b.reserve();b.begin(ticket['request_id']);v=c.status()['active_version'];run=c._core('claim',{'worker':ticket['actor'],'task_id':tid},v);args=cases.q.owned(run);c.bind_run(ticket['request_id'],run['id']);c._core('change',args,v);assert c._core('verify',args,v)['passed'];c._core('transition',{**args,'target':'ready_review'},v);b.reconcile(ticket['request_id']);review=b.reserve();b.begin(review['request_id']);cases.q.assessment(f,run['id']);c._core('transition',{**args,'target':'accepted'},v)
   target=f['root']/'runtime/accepted';shutil.copytree(f['repo']/'agent_workflow',target/'agent_workflow');receipt={'schema_version':1,'approved':True,'reviewer':m.helper.REVIEWER,'run_id':run['id'],'core_state':f['p']['core_state'],'source_root':f['p']['repo'],'source_sha256':digest(f['repo']),'controller_package_sha256':run['workflow']};rp=f['root']/'receipt.json';rp.write_bytes(encoded(receipt));f.update(b=b,run=run,args=args,v=v,review=review,target=target,rp=rp,rh=sha(rp.read_bytes()),base=b.status());return f
  names=['blocked-runtime','stale-runtime','deadline-runtime','natural-freshness','after-quality','event-quality','event-deadline','event-source','event-runtime','claim-at-transaction']
  if A.scenario!='all':names=[A.scenario]
  for name in names:
   f=setup(name);c=f['c'];b=f['b'];hit=[];claim_result=[];actual_runtime=c._runtime;actual_event=c._event;actual_guard=quality.guard_accept;actual_tx=c._tx;armed=[False];restore={}
   before_assessment=f['assessment_path'].read_bytes()
   if name=='natural-freshness':
    # Real wall time only; no file mutation after validation or clock patch.
    cases.q.assessment(f,f['run']['id'],created_at=time.time()-290)
    before_assessment=f['assessment_path'].read_bytes();expires=json.loads(before_assessment)['created_at']+300
   if name=='claim-at-transaction':
    tid=cases.q.task(f,'concurrent-work');cases.q.assessment(f,f['run']['id'],[cases.q.candidate(tid,finding='b'*64)])
    peer=Engine(f['root']/'state',f['config']);peer.db.execute('PRAGMA busy_timeout=25')
   def mutate():
    if name in {'blocked-runtime','after-quality','event-quality'}:cases.q.assessment(f,f['run']['id'],changes={'safety':'block'})
    elif name=='stale-runtime':cases.q.assessment(f,f['run']['id'],created_at=time.time()-301)
    elif name in {'deadline-runtime','event-deadline'}:f['clock'][0]=f['base']['deadline']+1
    elif name=='natural-freshness':time.sleep(max(0,expires-time.time())+.05)
    elif name=='event-source':
     p=f['repo']/'value.txt';restore[p]=p.read_bytes();p.write_bytes(restore[p]+b'changed\n')
    elif name=='event-runtime':
     p=f['target']/'agent_workflow/__init__.py';restore[p]=p.read_bytes();p.write_bytes(restore[p]+b'\n')
   def runtime(version):
    result=actual_runtime(version)
    if version['controller_path']==str(f['target']) and not armed[0]:
     armed[0]=True
     if name in {'blocked-runtime','stale-runtime','deadline-runtime','natural-freshness'}:hit.append(name);mutate()
    return result
   def guard(*args,**kwargs):
    result=actual_guard(*args,**kwargs)
    if name=='after-quality' and not hit:hit.append(name);mutate()
    return result
   def event(kind,body):
    result=actual_event(kind,body)
    if kind=='version_promoted' and name.startswith('event-') and not hit:hit.append(name);mutate()
    return result
   @contextlib.contextmanager
   def tx(*args,**kwargs):
    if name=='claim-at-transaction' and armed[0] and not hit:
     hit.append(name)
     try:claim_result.append({'claimed_run_id':peer.claim('real-competing-core-worker',tid)['id']})
     except sqlite3.OperationalError as e:
      assert 'locked' in str(e);claim_result.append({'blocked':True})
    with actual_tx(*args,**kwargs):yield
   c._runtime=runtime;c._event=event;c._tx=tx;quality.guard_accept=guard
   completed=False;error=None
   try:completed=b.finish(f['review']['request_id'],str(f['rp']),f['rh'],str(f['target']),f['v']['id'])['phase']=='completed'
   except ValueError as e:error=str(e)
   finally:c._runtime=actual_runtime;c._event=actual_event;c._tx=actual_tx;quality.guard_accept=actual_guard
   assert hit,'fault was not reached: '+name
   if name=='claim-at-transaction':
    active=f['e'].db.execute("SELECT count(*) FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review') AND lease_until>?",(time.time(),)).fetchone()[0]
    assert not (completed and active),'promotion completed concurrently with a real live Core claim'
    assert completed and claim_result==[{'blocked':True}],claim_result
    promoted=json.loads(c.db.execute("SELECT body FROM events WHERE kind='version_promoted'").fetchone()[0])
    assert promoted['quality_assessment_sha256']==sha(f['assessment_path'].read_bytes())
    peer.import_task('lock-released','Prove reservation released without a new running attempt');peer.db.close()
   else:
    assert not completed,'unsafe promotion completed: '+name
    assert c.status()['active_version']['id']==f['v']['id']
    assert c.db.execute("SELECT count(*) FROM events WHERE kind='version_promoted'").fetchone()[0]==0
    entry=next(x for x in b.status()['entries'] if x['request_id']==f['review']['request_id']);assert entry['phase']!='completed'
    assert c.db.execute("SELECT count(*) FROM events WHERE kind IN ('host_finish_completed','host_promotion_recovered')").fetchone()[0]==0
    if name=='natural-freshness':assert f['assessment_path'].read_bytes()==before_assessment and time.time()>expires
   for p,raw in restore.items():p.write_bytes(raw)
   probe=sqlite3.connect('file:'+str(f['root']/'state/tasks.sqlite')+'?mode=rw',uri=True,timeout=.05,isolation_level=None)
   try:probe.execute('BEGIN IMMEDIATE');probe.execute('PRAGMA query_only=ON');probe.execute('ROLLBACK')
   finally:probe.close()
   status=b.status();assert all(status[k]==f['base'][k] for k in ['started','deadline','requests_spent'])
   c.stop('commit race fixture finished');results.append({'scenario':name,'completed':completed,'rejection':error,'budget_unchanged':True,'real_Core_claim':bool(claim_result),'fault_reached':True})
 print(json.dumps({'passed':True,'results':results,'deterministic_interleavings':True,'natural_freshness_uses_real_time':True,'assessments':'owner fixtures, not AI reviews','installed':A.installed,'model_dispatches':0}))

if __name__=='__main__':main()
