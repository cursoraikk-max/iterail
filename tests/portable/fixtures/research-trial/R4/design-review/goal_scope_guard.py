#!/usr/bin/env python3
"""Independent R4 native local contract: no models/network, real Core evidence."""
import argparse,copy,hashlib,importlib.util,json,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]

def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve();checks=[]
 spec=importlib.util.spec_from_file_location('original_quality_fixture',ROOT/'automation-trial/trusted/quality_guard.py');q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
 original_write=q.write_json
 def owner_v2(path,value):
  if 'assessment_path' in value:value={**value,'schema_version':2}
  original_write(path,value)
 q.write_json=owner_v2
 try:
  with tempfile.TemporaryDirectory(prefix='goal-scope-') as d:
   c=q.Contract(repo,Path(d).resolve());f=c.fixture('global-goals');e=f['e'];proof=c.baseline(f);initial_budget=copy.deepcopy(e.config['budget']);initial_permissions=copy.deepcopy(e.config['permissions'])
   def row(goal,status='achieved',task=None):
    return {'goal_id':goal,'status':status,'reason':'Independent goal-specific review: '+goal,'evidence':['owner:goal-specific/'+goal+'/'+status],'task_id':task}
   goals=['overall-quality','quality-alias-goal']
   def assess(rows,candidates=None,**kw):
    return c.assessment(f,proof,candidates or [],schema_version=2,goal_coverage=rows,**kw)
   def observe(reason=None,disposition=None):
    before=e.db.execute('SELECT count(*) FROM events').fetchone()[0];v=e.next_step(task_id=f['query_task']);assert v==e.next_step(task_id=f['query_task']);assert before==e.db.execute('SELECT count(*) FROM events').fetchone()[0]
    z=v['quality'];assert z['scope']=='owner_goals';assert isinstance(z['all_owner_goals_assessed_achieved'],bool);assert 'goal_statuses' in z
    if reason:assert z['reason']==reason,(reason,z)
    if disposition:assert z['disposition']==disposition,(disposition,z)
    assert e.config['budget']==initial_budget and e.config['permissions']==initial_permissions
    raw=json.dumps(v);assert 'Independent goal-specific review' not in raw and 'owner:goal-specific/' not in raw and q.REVIEWER not in raw
    return v
   # Actual audit counterexample: topic achieved, global remaining local work, no task yet.
   assess([row(goals[0]),row(goals[1],'actionable')]);v=observe('goal_planning_required','wait_review');assert not v['quality']['all_owner_goals_assessed_achieved'];assert v['quality']['host_request']['kind']=='reviewer'
   assert e.db.execute('SELECT count(*) FROM tasks').fetchone()[0]==1;checks.append('narrow completed topic plus actionable global gap waits for planning without synthetic task')
   for state,reason,disp in [('needs_research','goal_research_required','wait_review'),('needs_validation','goal_validation_required','wait_review'),('needs_capability','goal_capability_required','wait_owner'),('needs_approval','goal_approval_required','wait_owner')]:
    assess([row(goals[0]),row(goals[1],state)]);v=observe(reason,disp);assert not v['quality']['claim_allowed'];assert not v['quality']['all_owner_goals_assessed_achieved']
    if disp=='wait_owner':assert v['quality']['host_request'] is None
    runs=e.db.execute('SELECT count(*) FROM runs').fetchone()[0];c.denied(lambda:e.claim('unauthorized-worker'));assert runs==e.db.execute('SELECT count(*) FROM runs').fetchone()[0]
   checks.append('research validation capability approval remain explicit; no worker, new permission, attempt or budget')
   # Missing/duplicate/unrecognized goals and empty evidence cannot masquerade as achievement.
   bads=[[row(goals[0])],[row(goals[0]),row(goals[0])],[row(goals[0]),row('unknown')],[row(goals[0]),{**row(goals[1]),'evidence':[]}],[row(goals[0]),{**row(goals[1]),'reason':''}],[row(goals[0]),{**row(goals[1]),'status':'all_tests_passed'}]]
   for bad in bads:
    assess(bad);z=e.next_step(task_id=f['query_task'])['quality'];assert z['disposition']=='wait_owner';assert z['scope']=='owner_goals' and z['all_owner_goals_assessed_achieved'] is False
   c.assessment(f,proof)
   z=e.next_step(task_id=f['query_task'])['quality'];assert z['disposition']=='wait_owner','schema2 policy accepted downgraded schema1 assessment';assert z['scope']=='owner_goals' and z['all_owner_goals_assessed_achieved'] is False
   checks.append('full unique goal coverage and independent goal-specific evidence required; tests green is not goal status')
   # Add actual local work through existing task API, retain exact immutable candidate identity.
   tid=c.task(f,'remaining-local-work');candidate=c.candidate(tid);candidate['goal_id']=goals[1]
   rows=[row(goals[0]),row(goals[1],'actionable',tid)];assess(rows,[candidate]);v=observe('novel_reviewed_value','continue');assert v['quality']['next_task_id']==tid and v['quality']['claim_allowed']
   # Unrelated blocked goal cannot globally stall an eligible actionable task.
   assess([row(goals[0],'needs_approval'),row(goals[1],'actionable',tid)],[candidate]);assert e.next_step(task_id=f['query_task'])['quality']['claim_allowed']
   # An achieved goal cannot sponsor a candidate, nor can a task map to the wrong goal.
   assess([row(goals[0]),row(goals[1])],[candidate]);assert e.next_step(task_id=f['query_task'])['quality']['disposition']=='wait_owner'
   mismatch=copy.deepcopy(candidate);mismatch['goal_id']=goals[0];assess(rows,[mismatch]);assert e.next_step(task_id=f['query_task'])['quality']['disposition']=='wait_owner'
   forged=copy.deepcopy(candidate);forged['task_payload_sha256']='0'*64;assess(rows,[forged]);assert e.next_step(task_id=f['query_task'])['quality']['disposition']=='wait_owner'
   assess(rows,[candidate]);v=e.next_step(task_id=f['query_task'])
   c.transport(f,e.next_step(task_id=f['query_task']))
   restarted=c.Engine(f['root']/'state');e.db.close();e=restarted;f['e']=e;assert e.next_step(task_id=f['query_task'])['quality']==v['quality']
   run=e.claim('real-local-pull-worker');assert run['task_id']==tid;own=c.owned(run);e.change(**own);assert e.verify(**own)['passed'];e.transition(**own,target='ready_review')
   # Incremental task acceptance must remain possible while separate owner goal remains blocked.
   proof=run['id'];assess([row(goals[0],'needs_capability'),row(goals[1])]);e.transition(**own,target='accepted');assert e.status(run_id=run['id'])['state']=='accepted';observe('goal_capability_required','wait_owner')
   checks.append('existing task exact candidate default pull, restart, actual subprocess verify and acceptance preserve other unfinished goals')
   assess(rows,[candidate]);observe('goal_task_unavailable','wait_owner')
   for status in ('achieved','needs_approval','needs_capability','needs_validation','needs_research'):
    assess([row(goals[0]),row(goals[1],status,tid)]);assert e.next_step(task_id=f['query_task'])['quality']['disposition']=='wait_owner','non-actionable row accepted task binding'
   # Current passing proof alone cannot close unverified long-running goal.
   assess([row(goals[0],'needs_validation'),row(goals[1])]);observe('goal_validation_required','wait_review')
   assess([row(goals[0]),row(goals[1])]);v=observe('all_owner_goals_assessed_achieved','stop_no_value');assert v['quality']['all_owner_goals_assessed_achieved'];assert not v['quality']['claim_allowed'];assert len(v['quality']['goal_statuses'])==2
   c.transport(f,e.next_step(task_id=f['query_task']));checks.append('only explicit fresh independent all-owner coverage yields scoped assessed achievement; CLI and stdio MCP agree')
   # Preserve separate source-stale and current-failed-proof negatives, no fabricated DB evidence.
   tid2=c.task(f,'actual-failing-proof');candidate2=c.candidate(tid2,finding='b'*64);assess([row(goals[0],'actionable',tid2),row(goals[1])],[candidate2]);run2=e.claim('real-failing-proof-worker');own2=c.owned(run2)
   (f['repo']/'value.txt').write_text('bad\n');assert e.next_step(task_id=f['query_task'])['quality']['disposition']!='stop_no_value';e.change(**own2);assert e.verify(**own2)['passed'] is False;e.fail(**own2,reason='independent negative fixture actual verifier failure')
   proof=run2['id'];assess([row(goals[0]),row(goals[1])]);z=e.next_step(task_id=f['query_task'])['quality'];assert z['disposition']!='stop_no_value' and not z['claim_allowed'],'current failed proof plus achieved assertions closed scope';assert e.config['budget']==initial_budget;assert e.config['permissions']==initial_permissions
   checks.append('exact source drift and actual current subprocess failed proof both prevent scoped closure despite achieved assertions')
   e.db.close()
  print(json.dumps({'passed':True,'checks':checks,'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 0
 except Exception as ex:
  print(json.dumps({'passed':False,'checks':checks,'error_type':type(ex).__name__,'error':str(ex),'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 1
if __name__=='__main__':sys.exit(main())
