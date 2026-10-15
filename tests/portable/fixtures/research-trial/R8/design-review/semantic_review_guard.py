#!/usr/bin/env python3
"""Independent native52: complete current semantic review, retain genuine typed waits."""
import argparse,copy,importlib.util,json,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]

def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve();sys.path.insert(0,str(repo));checks=[]
 try:
  from agent_workflow.hostbridge import HostBridge
  from agent_workflow.core import digest
  spec=importlib.util.spec_from_file_location('semantic_review_fixture',ROOT/'research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);write=m.helper.write_json
  def schema2(path,value):return write(path,{**value,'schema_version':2} if isinstance(value,dict) and 'assessment_path' in value else value)
  m.helper.write_json=schema2
  with tempfile.TemporaryDirectory(prefix='semantic-review-') as d:
   c=m.Cases(repo,Path(d).resolve())
   def setup(label):
    f=c.fixture(label);f['proof']=c.q.baseline(f);f['b']=HostBridge(f['c']);ticket=f['b'].reserve();assert ticket['kind']=='quality_review';ticket=f['b'].begin(ticket['request_id']);assert ticket['dispatch_started'] is True;f['ticket']=ticket;f['base']=f['b'].status();assert f['base']['requests_spent']==1;return f
   def reopen(f):c.reopen(f);f['b']=HostBridge(f['c']);return f['b']
   def rows(f,status):return [{'goal_id':g,'status':'achieved' if i==0 else status,'reason':'Independent current remaining goal is '+status,'evidence':['native:goal-specific/'+g+'/'+status],'task_id':None} for i,g in enumerate(f['policy']['goals'])]
   def assess(f,status='needs_research',candidates=None,**kw):
    values={'schema_version':2,'goal_coverage':rows(f,status)};values.update(kw);return c.q.assessment(f,f['proof'],candidates or [],**values)
   def project(f):return f['c']._core('next_step',{'task_id':f['query_task']},f['c'].status()['active_version'])['quality']
   def same(f,spent=1):
    now=f['b'].status();assert now['started']==f['base']['started'] and now['deadline']==f['base']['deadline'] and now['requests_spent']==spent;return now
   def entry(f):return next(x for x in f['b'].status()['entries'] if x['request_id']==f['ticket']['request_id'])
   def denied_reconcile(f):
    pending=f['c'].status()['request'];assert pending and pending['id']==f['ticket']['request_id'];runs=f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]
    try:f['b'].reconcile(f['ticket']['request_id'])
    except (ValueError,TypeError):pass
    assert f['c'].status()['request']['id']==pending['id'];assert entry(f)['phase']!='completed';assert runs==f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0];same(f)
   def finished(f,status,reason):
    q=project(f);assert q['reason']==reason and q['claim_allowed'] is False and q['all_owner_goals_assessed_achieved'] is False and q['assessment_sha256'];assert any(x['status']==status for x in q['goal_statuses'])
    f['b'].reconcile(f['ticket']['request_id']);assert entry(f)['phase']=='completed','fresh fulfilled semantic review was not consumed';assert f['c'].status()['request'] is None;same(f)
    count=f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]
    for i in range(3):
     f['clock'][0]+=6;reopen(f);state=f['c'].tick();assert state['request'] is None and state['phase'] not in {'quality_review_requested','worker_requested','stop_no_value'};assert reason in state['reason'];same(f);q2=project(f);assert q2['request_id']==q['request_id'] and q2['reason']==reason and q2['claim_allowed'] is False and q2['all_owner_goals_assessed_achieved'] is False
    assert count==f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]
    try:f['e'].claim('native-unapproved-worker')
    except c.q.Error:pass
    else:raise AssertionError('typed goal blocker granted Core work')
    assert count==f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]
   fixtures=[]
   for status,reason in [('needs_approval','goal_approval_required'),('needs_research','goal_research_required'),('needs_validation','goal_validation_required'),('needs_capability','goal_capability_required'),('actionable','goal_planning_required')]:
    f=setup(status);assess(f,status);finished(f,status,reason);fixtures.append(f);checks.append('fresh '+status+' review consumed once across repeated due/restart with original slot and blocked Core work')
   f=setup('already-unavailable-bound-task');candidate=c.q.candidate(f['query_task']);candidate['goal_id']=f['policy']['goals'][1];coverage=rows(f,'actionable');coverage[1]['task_id']=f['query_task'];assess(f,'actionable',[candidate],goal_coverage=coverage);finished(f,'actionable','goal_task_unavailable');checks.append('existing accepted bound task remains unavailable after its semantic review is consumed')
   f=fixtures[2];raw=(f['repo']/'value.txt').read_bytes();(f['repo']/'value.txt').write_bytes(raw+b'changed-source\n');f['clock'][0]+=6;reopen(f);q=project(f);state=f['c'].tick();assert not q['claim_allowed'] and q['assessment_sha256'] is None and state['request'] is None and q['reason'] in state['reason'];same(f);(f['repo']/'value.txt').write_bytes(raw);f['clock'][0]+=6;reopen(f);state=f['c'].tick();assert state['request'] is None and 'goal_validation_required' in state['reason'];same(f);checks.append('source drift cannot inherit resolved authority or stale reason; restoring exact source restores only the typed wait')
   # A changed valid blocked projection must not inherit blanket suppression.
   f=fixtures[1];assess(f,'needs_validation');f['clock'][0]+=6;request=f['c'].tick();assert request['phase']=='quality_review_requested' and request['request']['id']!=f['ticket']['request_id'];assert request['requests_spent']==2;same(f,2);new=f['b'].reserve();new=f['b'].begin(new['request_id']);assess(f,'needs_validation');f['b'].reconcile(new['request_id']);assert f['c'].status()['request'] is None;same(f,2);checks.append('new qualified independently changed blocked projection can be reviewed without renewing original resources')
   # A genuinely eligible new task resumes normal admission and +2 reservation.
   f=fixtures[0];tid=c.q.task(f,'real-next-work');candidate=c.q.candidate(tid,finding='c'*64);candidate['goal_id']=f['policy']['goals'][1];coverage=rows(f,'actionable');coverage[1]['task_id']=tid;assess(f,'actionable',[candidate],goal_coverage=coverage);assert project(f)['claim_allowed'];f['clock'][0]+=6;next_worker=f['c'].tick();assert next_worker['phase']=='worker_requested' and next_worker['request']['task_id']==tid and next_worker['requests_spent']==3;same(f,3);ticket=f['b'].reserve();ticket=f['b'].begin(ticket['request_id']);assert ticket['kind']=='worker';checks.append('fresh real eligible candidate escapes suppression and reserves worker review pair under original remaining budget')
   f=setup('untrusted-or-stale');original=assess(f)
   invalid=[{'created_at':time.time()-301},{'reviewer':'worker'},{'reviewer':'untrusted-reviewer'},{'source_sha256':'0'*64},{'proof_run_id':'run-'+'0'*12},{'goal_coverage':rows(f,'needs_research')[:1]},{'changes':{'goal_alignment':'unknown'}}]
   # Missing is real external owner-file absence, not fake DB state.
   f['assessment_path'].unlink();denied_reconcile(f)
   for kw in invalid:
    assess(f,**kw);denied_reconcile(f)
   # Malformed bounded external assessment cannot become a completed receipt.
   f['assessment_path'].write_text('{"schema_version":2,');denied_reconcile(f)
   assess(f)
   for path in [f['repo']/'value.txt',f['config_path'],f['policy_path'],Path(f['c'].status()['active_version']['controller_path'])/'agent_workflow/__init__.py']:
    raw=path.read_bytes();path.chmod(0o644);path.write_bytes(raw+b'\n');denied_reconcile(f);path.write_bytes(raw)
   assess(f);f['b'].reconcile(f['ticket']['request_id']);assert entry(f)['phase']=='completed' and f['c'].status()['request'] is None;same(f);checks.append('missing stale malformed incomplete self unknown wrong-source/proof and drifted source/policy/controller never consume review; restored fresh authority resolves')
   # Actual resolver side effect succeeds; only the outer host acknowledgment is lost.
   f=setup('actual-resolved-event-lost-reply');coverage=[{**r,'status':'achieved'} for r in rows(f,'needs_research')];assess(f,goal_coverage=coverage);actual=f['c'].resolve_quality_review;calls=[]
   def lost(*args,**kw):
    result=actual(*args,**kw);calls.append(result['request'] is None);raise ValueError('controlled lost semantic reply after actual successful resolution')
   f['c'].resolve_quality_review=lost
   try:f['b'].reconcile(f['ticket']['request_id'])
   except ValueError:pass
   assert calls==[True] and f['c'].status()['request'] is None;assert f['c'].db.execute("SELECT count(*) FROM events WHERE kind='quality_review_resolved'").fetchone()[0]==1
   reopen(f)
   def replay_forbidden(*args,**kw):raise AssertionError('resolved semantic mutator must not be replayed')
   f['c'].resolve_quality_review=replay_forbidden;f['b'].reconcile(f['ticket']['request_id']);assert entry(f)['phase']=='completed' and entry(f)['dispatch_started'] is True;same(f);assert f['c'].db.execute("SELECT count(*) FROM events WHERE kind='quality_review_resolved'").fetchone()[0]==1;assert f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]==1
   try:f['b'].begin(f['ticket']['request_id'])
   except ValueError:pass
   else:raise AssertionError('resolved lost-reply intent dispatched twice')
   checks.append('actual successful semantic event recovers same lost-reply intent after restart without repeating resolver dispatch slot or Core work')
   # The real event above belongs to a different request; stale current input cannot borrow it.
   previous=f['ticket']['request_id'];assess(f,created_at=time.time()-301);f['clock'][0]+=6;pending=f['c'].tick();assert pending['phase']=='quality_review_requested' and pending['request']['id']!=previous;same(f,2);f['ticket']=f['b'].reserve();f['ticket']=f['b'].begin(f['ticket']['request_id']);reopen(f)
   try:f['b'].reconcile(f['ticket']['request_id'])
   except ValueError:pass
   assert f['c'].status()['request']['id']==f['ticket']['request_id'] and entry(f)['phase']!='completed';same(f,2);assert f['c'].db.execute("SELECT count(*) FROM events WHERE kind='quality_review_resolved'").fetchone()[0]==1
   assess(f,'needs_approval');f['b'].reconcile(f['ticket']['request_id']);assert entry(f)['phase']=='completed' and f['c'].status()['request'] is None;same(f,2);assert f['c'].db.execute("SELECT count(*) FROM events WHERE kind='quality_review_resolved'").fetchone()[0]==2;assert not project(f)['claim_allowed'];checks.append('unrelated prior resolved event cannot complete stale current review; actual fresh trusted response completes only its own intent')
   f=setup('original-deadline');assess(f);f['clock'][0]=f['base']['deadline']+1;reopen(f);denied_reconcile(f);assert f['c'].status()['request']['id']==f['ticket']['request_id'];checks.append('original expired deadline prevents semantic completion and restart never refreshes it')
  print(json.dumps({'passed':True,'checks':checks,'native_local_fixture':True,'actual_agent_dispatch':False,'model_calls':0,'network_calls':0,'db_tampering':False}));return 0
 except Exception as e:
  print(json.dumps({'passed':False,'checks':checks,'error_type':type(e).__name__,'error':str(e),'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 1
if __name__=='__main__':sys.exit(main())
