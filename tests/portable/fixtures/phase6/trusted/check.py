"""Preregistered native auto UIUX maintenance; no providers or capture."""
import copy,hashlib,json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'phase5/trusted'))
import common
from common import run,require,rejects,fixture,close,meta

def policy(root,has_ui=True,ident='native-ui'):
 art=root/'art';art.mkdir(exist_ok=True)
 return {'schema_version':1,'project':{'id':ident,'has_ui':has_ui},'artifact_root':str(art),'standards':{'users':['reader'],'flows':[{'id':'home','steps':['Open home']}],'references':[],'tokens':{},'viewports':[{'id':'small','width':32,'height':24}],'a11y':['keyboard']},'cadence':{'interval_seconds':60},'limits':{'artifact_bytes':4096,'evidence_items':8},'rubric':[{'id':'flow','minimum_score':3}],'approved_brand_changes':[]}
def setup(m,has_ui=True,sink=None,gate=None):
 tmp=tempfile.TemporaryDirectory(prefix='phase6-native-');root=Path(tmp.name).resolve();src=root/'src';src.mkdir();(src/'index.html').write_text('<button>Home</button>');p=policy(root,has_ui);clock=[1000.0];s=m.UIUXMaintenance(root/'state',p,src,task_sink=sink,clock=lambda:clock[0],gate=gate or (lambda:True));return tmp,root,src,p,clock,s
def done(s):
 db=getattr(s,'db',None)
 if db is not None:db.close()
def inert(p):require(p['commands']==[] and p['permissions']=={} and json.loads(p['body'])['production_visual_verified'] is False,'scheduler expanded authority or visual proof')

def no_ui(m):
 emitted=[];tmp,root,src,p,clock,s=setup(m,False,emitted.append)
 try:
  require(not (root/'state').exists(),'noUI constructor wrote state');r=s.tick();require(r['phase']=='not_applicable' and r['production_visual_verified'] is False,'noUI not N/A');s.status();require(not emitted and not (root/'state').exists(),'noUI created scheduler/Store/task files')
 finally:done(s);tmp.cleanup()

def due_restart(m):
 emitted=[];tmp,root,src,p,clock,s=setup(m,sink=emitted.append)
 try:
  r=s.tick();require(r['phase']=='due' and len(emitted)==1 and r['production_visual_verified'] is False,'initial audit missing');inert(emitted[0]);first=s.status()['last_run_id'];require(s.status()['next_due']==1060,'success next_due not owner cadence');clock[0]=1001;require(s.tick()['phase']=='not_due' and len(emitted)==1,'early tick duplicated task');done(s);s=m.UIUXMaintenance(root/'state',p,src,task_sink=emitted.append,clock=lambda:clock[0],gate=lambda:True);require(s.status()['next_due']==1060 and s.status()['last_run_id']==first,'restart lost cadence/dedupe');clock[0]=1061;s.tick();require(len(emitted)==2 and emitted[0]['external_key']!=emitted[1]['external_key'],'periodic pending audit suppressed or reused identity');clock[0]=1062;s.tick();require(len(emitted)==2,'repeated tick duplicated periodic audit');(src/'index.html').write_text('<button>Changed</button>');clock[0]=1122;s.tick();require(len(emitted)==3 and json.loads(emitted[1]['body'])['source_sha256']!=json.loads(emitted[2]['body'])['source_sha256'],'due audit did not use changed actual source')
 finally:done(s);tmp.cleanup()

def failure_backoff(m):
 imported={};attempts=[];fail=[2]
 def sink(p):
  inert(p);attempts.append(copy.deepcopy(p));imported.setdefault(p['external_key'],p)
  if fail[0]:fail[0]-=1;raise RuntimeError('native callback failure after durable external import')
 tmp,root,src,p,clock,s=setup(m,sink=sink)
 try:
  require(s.tick()['phase']=='backoff','callback failure lost');require(s.status()['failure_count']==1 and s.status()['next_due']==1030,'first backoff not bounded formula');clock[0]=1029;require(s.tick()['phase']=='not_due' and len(attempts)==1,'backoff retried early');done(s);clock[0]=1031;s=m.UIUXMaintenance(root/'state',p,src,task_sink=sink,clock=lambda:clock[0],gate=lambda:True);require(s.tick()['phase']=='backoff' and s.status()['failure_count']==2 and s.status()['next_due']==1091,'restart/reset changed second backoff');done(s);clock[0]=1092;s=m.UIUXMaintenance(root/'state',p,src,task_sink=sink,clock=lambda:clock[0],gate=lambda:True);require(s.tick()['phase']=='due','persisted intent not retried');require(len(imported)==1 and len(attempts)==3 and attempts[0]==attempts[1]==attempts[2],'retry after cadence created new intent or changed stable body');require(s.status()['failure_count']==0 and s.status()['next_due']==1152,'retry success did not clear failure/cadence')
 finally:done(s);tmp.cleanup()

def stopped_gate(m):
 emitted=[];allowed=[True];tmp,root,src,p,clock,s=setup(m,sink=emitted.append,gate=lambda:allowed[0])
 try:
  before=s.status();require(s.tick(stopped=True)['phase']=='paused','stopped tick admitted');require(not emitted and s.status()['stopped'] is True and s.status()['next_due']==before['next_due'],'stop advanced due or exported');done(s);s=m.UIUXMaintenance(root/'state',p,src,task_sink=emitted.append,clock=lambda:clock[0],gate=lambda:allowed[0]);require(s.status()['stopped'] is True,'restart lost observedstop');allowed[0]=False;before=s.status();require(s.tick(stopped=False)['phase']=='paused' and not emitted,'scheduler overrode owner closed gate');require(s.status()['next_due']==before['next_due'] and s.status()['failure_count']==before['failure_count'],'closed gate spent cadence/backoff');allowed[0]=True;require(s.tick(stopped=False)['phase']=='due' and len(emitted)==1,'live authorized admission could not resume scheduler')
 finally:done(s);tmp.cleanup()

def late_gate(m):
 emitted=[];checks=[0];allowed=[False]
 def gate():checks[0]+=1;return allowed[0] or checks[0]==1
 tmp,root,src,p,clock,s=setup(m,sink=emitted.append,gate=gate)
 try:
  before=s.status();require(s.tick()['phase']=='paused' and not emitted,'late ownerstop allowed export');require(s.status()['next_due']==before['next_due'] and s.status()['failure_count']==before['failure_count'],'latepause consumed due/backoff');allowed[0]=True;require(s.tick()['phase']=='due' and len(emitted)==1,'late paused intent lost/deduplicated incorrectly')
 finally:done(s);tmp.cleanup()

def immutable_bindings(m):
 tmp,root,src,p,clock,s=setup(m)
 try:
  s.tick();done(s);bad=copy.deepcopy(p);bad['standards']['tokens']['brand']='changed';rejects(lambda:m.UIUXMaintenance(root/'state',bad,src,clock=lambda:clock[0],gate=lambda:True),'scheduler owner standards mutable');foreign=root/'foreign';foreign.mkdir();(foreign/'private.txt').write_text('native data');rejects(lambda:m.UIUXMaintenance(root/'state',p,foreign,clock=lambda:clock[0],gate=lambda:True),'scheduler source scope changed on restart')
 finally:done(s);tmp.cleanup()

def supervisor_hook(m):
 from agent_workflow import evolution
 tmp,root,c=fixture(evolution,wall=600)
 try:
  root=root.resolve();p=policy(root,True,c['project']['id']);path=root/'owner-uiux.json';path.write_text(json.dumps(p));c['project']['uiux']={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};s=evolution.Supervisor(root/'owner',c);before=meta(s);require(s.cycle()['phase']=='idle','native passed check should remain idle');import sqlite3
  core=s.root/'uiux-core/tasks.sqlite';require(core.is_file(),'cycle did not automatically initialize shared UI Core');db=sqlite3.connect('file:'+str(core)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;tasks=[dict(x) for x in db.execute('SELECT * FROM tasks')];cp=json.loads(db.execute('SELECT body FROM policy').fetchone()[0]);db.close();require(len(tasks)==1 and tasks[0]['state']=='queued' and tasks[0]['external_key'].startswith('uiux:due:'),'automatic audit not actual inert Core task');inert(json.loads(tasks[0]['body']));require(cp['tests']==[[a.replace('{repo}',c['repo']) for a in argv] for argv in c['tests']] and cp['budget']['run_seconds']==c['budget']['wall_seconds'] and cp['budget']['test_seconds']==min(c['budget']['call_seconds'],c['budget']['wall_seconds']),'UI Core changed fixed owner tests/budget');require(cp['permissions']=={'execute_tests':True,'network':False,'publish':False} and cp['acceptance']=={'require_tests':True,'require_unchanged_candidate':True},'automatic UI changed Core authority');s.cycle();after=meta(s);db=sqlite3.connect('file:'+str(core)+'?mode=ro',uri=True);require(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==1,'repeatedcycle duplicated dueaudit');db.close();require(after['calls']==0 and after['deadline']==before['deadline'] and after['config_sha']==before['config_sha'] and not s.db.execute("SELECT * FROM reservations WHERE kind LIKE 'supervised:backend:%'").fetchall(),'UIauto hook dispatched backend/reset ownerbudget');s.set_stop();count=s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0];s.cycle();require(s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]==count and meta(s)['stopped'],'stopped owner advanced autoUI');close(s)
 finally:tmp.cleanup()

run('p6-uiux-auto','uiux_maintenance',[('no_UI_no_disk_effects',no_ui),('due_notdue_periodic_restart_source',due_restart),('failure_backoff_restart_stable_intent',failure_backoff),('observed_stop_and_owner_gate_inheritance',stopped_gate),('late_gate_before_export_retains_due',late_gate),('immutable_owner_source_bindings',immutable_bindings),('actual_Supervisor_hook_Coretask_no_backend',supervisor_hook)])
