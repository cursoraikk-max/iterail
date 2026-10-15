"""Supplemental scope probes frozen before scheduler refinements, after initial module."""
import ast,copy,json,sqlite3,sys,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'phase5/trusted'))
from common import run,require,fixture,meta,close
base=Path(__file__).resolve().parents[1]/'trusted/check-v2.py';tree=ast.parse(base.read_text());tree.body=tree.body[:-1];h={'__file__':str(base)};exec(compile(tree,str(base),'exec'),h)

def stale_archive(m):
 attempts=[];fail=[True]
 def sink(p):
  h['inert'](p);attempts.append(copy.deepcopy(p))
  if fail[0]:fail[0]=False;raise RuntimeError('native failed before import')
 tmp,root,src,p,clock,s=h['setup'](m,sink=sink)
 try:
  require(s.tick()['phase']=='backoff','failed intent missing');old=copy.deepcopy(attempts[0]);(src/'index.html').write_text('<button>New source</button>');h['done'](s);clock[0]=1031;s=m.UIUXMaintenance(root/'state',p,src,task_sink=sink,clock=lambda:clock[0],gate=lambda:True);require(s.tick()['phase']=='due' and len(attempts)==2 and attempts[1]['external_key']!=old['external_key'],'stale failure prevented fresh audit or reused key');archived=s.status().get('stale_intents',[]);require(any(x.get('reason')=='source_changed' and x.get('proposal')==old for x in archived),'exact stale failed proposal/key/body not durably archived');h['done'](s);s=m.UIUXMaintenance(root/'state',p,src,task_sink=sink,clock=lambda:clock[0],gate=lambda:True);require(s.status().get('stale_intents')==archived,'restart lost immutable stale archive')
 finally:h['done'](s);tmp.cleanup()

def serialized_tick_stop(m):
 from agent_workflow.uiux import UIUXStore
 calls=[];first=threading.Event();release=threading.Event();mutex=threading.Lock()
 def sink(p):
  h['inert'](p)
  with mutex:calls.append(p['external_key']);ordinal=len(calls)
  if ordinal==1:first.set();release.wait(3)
 tmp,root,src,p,clock,a=h['setup'](m,sink=sink)
 try:
  a.tick(stopped=True);store=UIUXStore(root/'state/uiux',p,clock=lambda:clock[0]);store.discover(src);store.db.close();b=m.UIUXMaintenance(root/'state',p,src,task_sink=sink,clock=lambda:clock[0],gate=lambda:True);results=[];errors=[]
  def tick(s):
   try:results.append(s.tick()['phase'])
   except Exception as e:errors.append(str(e))
  t1=threading.Thread(target=tick,args=(a,));t1.start();require(first.wait(2),'first due callback absent');t2=threading.Thread(target=tick,args=(b,));t2.start();t2.join(.25);release.set();t1.join(5);t2.join(5);require(not errors and not t1.is_alive() and not t2.is_alive() and len(calls)==1 and sorted(results)==['due','not_due'],'cross-instance tick duplicated callback or lost durable schedule');h['done'](b)
  live=[True]
  def stopped_sink(p):live[0]=False;raise RuntimeError('native owner gate closed during callback')
  c=m.UIUXMaintenance(root/'late',p,src,task_sink=stopped_sink,clock=lambda:clock[0],gate=lambda:live[0]);before=c.status();require(c.tick()['phase']=='paused','mid-callback stop mislabeled retry failure');after=c.status();require(after['next_due']==before['next_due'] and after['failure_count']==before['failure_count'],'mid-callback stop advanced due/backoff');h['done'](c)
 finally:release.set();h['done'](a);tmp.cleanup()

def owner_typed_event(m):
 from agent_workflow import evolution
 tmp,root,c=fixture(evolution,wall=600)
 try:
  import hashlib
  root=root.resolve();c['repo']=str(Path(c['repo']).resolve());p=h['policy'](root,True,c['project']['id']);path=root/'owner-uiux.json';path.write_text(json.dumps(p));c['project']['uiux']={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};s=evolution.Supervisor(root/'owner',c);s.cycle();events=[json.loads(x['data']) for x in s.status()['events'] if x['kind']=='uiux_maintenance_checked'];require(len(events)==1 and events[0]['result']['phase']=='due' and len(json.dumps(events[0]).encode())<=4096,'enabled autoUI cycle lacks bounded due result event');before=meta(s);path.write_text(json.dumps(dict(p,approved_brand_changes=['tampered'])));r=s.maintenance_tick();require(r['phase']=='blocked' and r['failure_class']=='integrity','pre-scheduler integrity mislabeled as durablebackoff');require(meta(s)['calls']==before['calls'] and meta(s)['deadline']==before['deadline'],'typed UI integrity changed owner budget');close(s)
 finally:tmp.cleanup()

run('p6-uiux-supporting','uiux_maintenance',[('stale_failed_intent_exact_archive_and_fresh_audit',stale_archive),('cross_instance_tick_and_midcallback_stop',serialized_tick_stop),('enabled_hook_bounded_event_typed_integrity',owner_typed_event)])
