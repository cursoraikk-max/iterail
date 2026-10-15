#!/usr/bin/env python3
"""Independent budget supplement: local real Core fixtures, no Agent/model dispatch."""
import argparse,importlib.util,json,shutil,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
SEMANTIC='budget: owner semantic request ceiling exhausted'
WORKER='budget: owner request ceiling cannot reserve worker and review'
def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve();sys.path.insert(0,str(repo));checks=[]
 try:
  from agent_workflow.hostbridge import HostBridge
  spec=importlib.util.spec_from_file_location('budget_native_fixture',ROOT/'research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);write=m.helper.write_json
  def schema2(path,value):return write(path,{**value,'schema_version':2} if isinstance(value,dict) and 'assessment_path' in value else value)
  m.helper.write_json=schema2
  with tempfile.TemporaryDirectory(prefix='native54-') as tmp:
   c=m.Cases(repo,Path(tmp).resolve())
   def setup(label,slots=2):
    f=c.fixture(label,slots);f['proof']=c.q.baseline(f);f['base']=f['c'].status();return f
   def assess(f,status='actionable',task=None,proof=None):
    coverage=[{'goal_id':g,'status':'achieved' if i==0 else status,'reason':'Independent bounded native goal '+status,'evidence':['native:goal-specific/'+g+'/'+status],'task_id':task if i==1 else None} for i,g in enumerate(f['policy']['goals'])];candidates=[]
    if task:
     candidate=c.q.candidate(task);candidate['goal_id']=f['policy']['goals'][1];candidates=[candidate]
    return c.q.assessment(f,proof or f['proof'],candidates,schema_version=2,goal_coverage=coverage)
   def same(f,spent):
    s=f['c'].status();assert s['started']==f['base']['started'] and s['deadline']==f['base']['deadline'] and s['requests_spent']==spent;return s
   def counts(f):return tuple(f['e'].db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['tasks','runs','events'])
   def reservations(f):return f['c'].db.execute("SELECT count(*) FROM events WHERE kind IN ('quality_review_reserved','worker_pair_reserved')").fetchone()[0]
   # Actual native Core worker/review pair, independently assessed and promoted.
   f=setup('spent-pair');tid=c.q.task(f,'actual-native-work');assess(f,task=tid);pending=f['c'].tick();assert pending['phase']=='worker_requested' and pending['requests_spent']==2
   version=pending['active_version'];run=f['c']._core('claim',{'worker':'native54-worker'},version);assert run['task_id']==tid;f['c'].bind_run(pending['request']['id'],run['id']);own=c.q.owned(run);f['c']._core('change',own,version);assert f['c']._core('verify',own,version)['passed'];f['c']._core('transition',{**own,'target':'ready_review'},version);review=f['c'].tick();assert review['phase']=='review_requested';same(f,2)
   assess(f,'achieved',proof=run['id']);f['c']._core('transition',{**own,'target':'accepted'},version)
   receipt={'schema_version':1,'approved':True,'reviewer':'independent-native54-reviewer','run_id':run['id'],'core_state':f['p']['core_state'],'source_root':f['p']['repo'],'source_sha256':c.q.digest(f['repo']),'controller_package_sha256':run['workflow']};path=f['root']/'receipt.json';write(path,receipt);successor=f['root']/'runtime/v2';successor.mkdir();shutil.copytree(f['repo']/'agent_workflow',successor/'agent_workflow');f['c'].promote(review['request']['id'],str(path),m.helper.sha(path.read_bytes()),str(successor),version['id']);same(f,2)
   assess(f,'actionable',proof=run['id']);f['clock'][0]+=6;projection=f['c']._quality_projection(f['c'].status()['active_version']);assert projection['disposition']=='wait_review' and projection['reason']=='goal_planning_required' and projection['claim_allowed'] is False and projection['all_owner_goals_assessed_achieved'] is False
   before=counts(f);reserved=reservations(f);state=f['c'].tick();assert state['phase']=='blocked' and state['reason']==SEMANTIC, 'exhausted semantic budget misclassified: '+state['reason'];assert state['request'] is None;same(f,2)
   for i in range(3):
    f['clock'][0]+=6;c.reopen(f);state=f['c'].tick();assert state['phase']=='blocked' and state['reason']==SEMANTIC and state['request'] is None;same(f,2)
    try:HostBridge(f['c']).reserve()
    except (ValueError,TypeError):pass
    else:raise AssertionError('exhausted budget emitted dispatch intent')
   assert counts(f)==before and reservations(f)==reserved;checks.append('real pair spent2/max2 plus fresh unresolved wait_review reports exact budget blocker across ticks/restart/Bridge refusal without new work or reservation')
   # Real integrity checks remain authoritative before the capacity branch.
   for target in [f['config_path'],Path(f['c'].status()['active_version']['controller_path'])/'agent_workflow/__init__.py']:
    original=target.read_bytes();target.chmod(0o644);target.write_bytes(original+b'\n')
    try:
     f['clock'][0]+=6;state=f['c'].tick();assert state['phase']=='blocked' and state['reason'].startswith('integrity:') and state['request'] is None;same(f,2);assert reservations(f)==reserved and counts(f)==before
    finally:target.write_bytes(original)
   f['clock'][0]+=6;assert f['c'].tick()['reason']==SEMANTIC;checks.append('real pinned policy and physical runtime drift stay integrity blocked even when budget exhausted; restoration restores budget reason only')
   # Two-slot reservation cannot be partly paid by the one remaining slot.
   g=setup('one-remaining-slot',2);s=g['c'].tick();assert s['phase']=='quality_review_requested' and s['requests_spent']==1;assess(g,'needs_research');g['c'].resolve_quality_review(s['request']['id']);g['clock'][0]+=6;resolved=g['c'].tick();assert resolved['phase']=='blocked' and resolved['request'] is None and 'goal_research_required' in resolved['reason'] and not resolved['reason'].startswith('budget:');same(g,1);tid=c.q.task(g,'next-worker');assess(g,task=tid);g['clock'][0]+=6;before2=counts(g);r2=reservations(g);state=g['c'].tick();assert state['phase']=='blocked' and state['reason']==WORKER and state['request'] is None;same(g,1);assert counts(g)==before2 and reservations(g)==r2;checks.append('insufficient worker pair capacity is typed budget block and never partly reserves')
   # At exact remaining capacity a semantic request is still reserved once.
   h=setup('capacity-available',1);s=h['c'].tick();assert s['phase']=='quality_review_requested' and s['request']['kind']=='quality_review' and s['requests_spent']==1;request=s['request'];c.reopen(h);again=h['c'].tick();assert again['request']==request and again['phase']=='quality_review_requested';same(h,1);assert reservations(h)==1;checks.append('available single semantic slot reserves normally; already-paid pending request remains observable at exhausted ceiling')
   h['c'].stop('explicit native54 owner stop');s=h['c'].tick();assert s['stopped'] and s['phase']=='stopped' and s['reason']=='explicit native54 owner stop';same(h,1)
   f['clock'][0]=f['base']['deadline']+1;c.reopen(f);s=f['c'].tick();assert s['phase']=='blocked' and s['request'] is None and not s['reason'].startswith('budget:') and not s['reason'].startswith('integrity:');same(f,2);checks.append('owner stop and original expired deadline retain precedence without refund reset or renewal')
  print(json.dumps({'passed':True,'checks':checks,'native_local_fixture':True,'actual_agent_dispatch':False,'db_tampering':False,'original_ledgers_modified':False,'model_calls':0,'network_calls':0}));return 0
 except Exception as error:
  print(json.dumps({'passed':False,'checks':checks,'error_type':type(error).__name__,'error':str(error),'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 1
if __name__=='__main__':raise SystemExit(main())
