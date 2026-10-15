#!/usr/bin/env python3
"""Owner frozen regression: actual local Core, zero agent/model dispatch."""
import argparse,importlib.util,json,sys,shutil,tempfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]

def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve();sys.path.insert(0,str(repo));checks=[]
 helper=ROOT/'research-trial/trusted/continuation_quality_guard.py'
 spec=importlib.util.spec_from_file_location('accepted_checkpoint_fixture',helper);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 try:
  with tempfile.TemporaryDirectory(prefix='accepted-checkpoint-') as d:
   c=m.Cases(repo,Path(d).resolve());f=c.fixture('accepted-between-ticks');proof=c.q.baseline(f);tid=c.q.task(f,'actual-repair');c.q.assessment(f,proof,[c.q.candidate(tid)])
   requested=f['c'].tick();assert requested['phase']=='worker_requested';v=requested['active_version'];run=f['c']._core('claim',{'worker':'checkpoint-native-worker'},v);f['c'].bind_run(requested['request']['id'],run['id']);args=c.q.owned(run)
   f['c']._core('change',args,v);assert f['c']._core('verify',args,v)['passed'];f['c']._core('transition',{**args,'target':'ready_review'},v)
   # No continuation poll observes ready_review. Independent owner assessment is a native fixture, not an agent claim.
   c.q.assessment(f,run['id']);f['c']._core('transition',{**args,'target':'accepted'},v);assert f['c']._core('status',{'run_id':run['id']},v)['state']=='accepted';checks.append('actual Core accepted without bridge ready_review poll')
   c.reopen(f);recovered=f['c'].tick();assert recovered['phase'] in {'review_requested','awaiting_promotion'},recovered['phase'];assert recovered['request']['kind']=='review','accepted worker intent was not reconciled to review'
   for key in ('task_id','source_sha256','version','controller_path','package_sha256'):assert recovered['request'][key]==requested['request'][key]
   assert recovered['request']['task_id']==tid and recovered['request']['run_id']==run['id'];c.same(requested,recovered);assert recovered['requests_spent']==2;checks.append('accepted checkpoint recovers reserved review without extra budget')
   c.reopen(f);again=f['c'].tick();assert again['request']==recovered['request'];c.same(recovered,again);checks.append('repeated restart keeps same review request and resources')
   receipt={'schema_version':1,'approved':True,'reviewer':'independent-checkpoint-reviewer','run_id':run['id'],'core_state':f['p']['core_state'],'source_root':f['p']['repo'],'source_sha256':c.q.digest(f['repo']),'controller_package_sha256':run['workflow']}
   rp=f['root']/'receipt.json';m.helper.write_json(rp,receipt);successor=f['root']/'runtime/v2';successor.mkdir();shutil.copytree(f['repo']/'agent_workflow',successor/'agent_workflow');receipt_sha=m.helper.sha(rp.read_bytes())
   c.denied(lambda:f['c'].promote(again['request']['id'],str(rp),'0'*64,str(successor),v['id']));assert f['c'].status()['request']==again['request'];checks.append('bad receipt still fails without consuming reserved intent')
   promoted=f['c'].promote(again['request']['id'],str(rp),receipt_sha,str(successor),v['id']);assert promoted['request'] is None and promoted['active_version']['id']!=v['id'];c.same(requested,promoted)
   # Simulate lost success reply only by discarding result and reopening/querying. No replay claim of exactly-once external execution.
   c.reopen(f);observed=f['c'].status();assert observed['active_version']==promoted['active_version'] and observed['request'] is None;c.same(promoted,observed);assert observed['requests_spent']==2
   events=[dict(x) for x in f['c'].db.execute('SELECT * FROM events')];assert sum(x['kind']=='version_promoted' for x in events)==1;checks.append('actual promotion and lost-reply status recovery keep single event')
   tick=f['c'].tick();assert tick['phase']=='not_due' and tick['request'] is None;c.same(promoted,tick);checks.append('new physical version continues due scheduling without minting task')
  print(json.dumps({'passed':True,'checks':checks,'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 0
 except Exception as e:
  print(json.dumps({'passed':False,'checks':checks,'error_type':type(e).__name__,'error':str(e),'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 1
if __name__=='__main__':sys.exit(main())
