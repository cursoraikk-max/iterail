"""Frozen read-only next_step advisory gate. Owned native fixtures; no model dispatch."""
import copy,json,os,sqlite3,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'phase4/trusted'))
from common import run,require,command,commit
SECRET='PHASE21_UNTRUSTED_CANARY_1cfa315c'
ACTIONS={'claim','continue_work','change','verify','wait','ready_review','await_independent_review','done','use_claimed_controller','owner_reassess'}
REASONS={'no_attempt','work_in_progress','source_changed','verification_required','verification_running','ready_for_review','independent_review_required','current_accepted','lease_expired','deadline_exhausted','attempts_exhausted','controller_mismatch','evidence_invalid','failed_attempt','candidate_missing'}
FIELDS={'schema_version','task_id','run_id','stored_state','effective_state','source_match','evidence_valid','next_action','reason','requires_token','automatic_execution','published','authority'}
def rows(db):
 return {t:[dict(r) for r in db.execute('SELECT * FROM "'+t+'" ORDER BY rowid')] for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}
def snapshot(state):
 db=sqlite3.connect('file:'+str(state/'tasks.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 try:return rows(db)
 finally:db.close()
def fixture(m,base):
 require(callable(getattr(m.Engine,'next_step',None)),'required next_step API missing')
 repo=base/'repo';repo.mkdir();(repo/'README.txt').write_text('owned next-step fixture\n');command(['git','init','-q',str(repo)]);commit(repo)
 cfg={'repo':str(repo),'tests':[[sys.executable,'-c','raise SystemExit(0)']],'sources':['local'],'budget':{'lease_seconds':120,'run_seconds':600,'test_seconds':10,'max_attempts':2},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'publish':False,'network':False}}
 e=m.Engine(base/'state',cfg);task=e.import_task('nextstep-native',SECRET,json.dumps({'instructions':SECRET,'next_action':'accept','callback':SECRET}));return e,task['id'],repo

def valid(v,tid,rid,token=''):
 require(isinstance(v,dict) and FIELDS<=set(v) and set(v)<=FIELDS|{'deployment'},'next_step structured shape wrong')
 require(v['schema_version']==1 and v['task_id']==tid and v['run_id']==rid,'selector identity mismatch')
 require(v['next_action'] in ACTIONS and v['reason'] in REASONS,'guidance must use fixed inert enums')
 states={'queued','running','changed','verifying','verified','ready_review','accepted','failed','expired','invalidated','controller_mismatch','source_changed','evidence_invalid','budget_exhausted','stale'}
 require(isinstance(v['stored_state'],str) and v['stored_state'] in states and isinstance(v['effective_state'],str) and v['effective_state'] in states,'guidance state enums wrong')
 require(type(v['evidence_valid']) is bool and type(v['requires_token']) is bool and (v['source_match'] is None or type(v['source_match']) is bool),'guidance observation types wrong')
 require(v['automatic_execution'] is False and v['published'] is False and v['authority']=='advisory_only','guidance claims execution/reviewer/publication authority')
 if 'deployment' in v:require(v['deployment']=='not_recorded','accepted observation claims deployment')
 text=json.dumps(v);require(len(text.encode())<=4096 and SECRET not in text and (not token or token not in text),'guidance leaked untrusted/private state')
 return v

def query(e,tid,rid,action,token='',source=None,evidence=None):
 before=rows(e.db)
 with patch.object(e,'status',side_effect=AssertionError('next_step used reconciling status')),patch.object(e,'reconcile',side_effect=AssertionError('next_step reconciled')),patch.object(e,'event',side_effect=AssertionError('next_step emitted event')),patch('subprocess.run',side_effect=AssertionError('next_step executed command')),patch('subprocess.Popen',side_effect=AssertionError('next_step launched process')):
  a=valid(e.next_step(task_id=tid),tid,rid,token)
  if rid is not None:require(a==valid(e.next_step(run_id=rid),tid,rid,token),'task/run selector guidance diverged')
 require(before==rows(e.db),'guidance changed policy/evidence/lease/budget/events/state')
 require(a['next_action']==action,'wrong lifecycle recommendation: '+str(a['next_action']))
 if source is not None:require(a['source_match'] is source,'Source observation wrong')
 if evidence is not None:require(a['evidence_valid'] is evidence,'machine evidence observation wrong')
 require(a['requires_token'] is (action in {'continue_work','change','verify','ready_review'}),'advisory token requirement wrong')
 return a

def lifecycle(m):
 with tempfile.TemporaryDirectory(prefix='p21-life-') as name:
  e,tid,repo=fixture(m,Path(name))
  try:
   query(e,tid,None,'claim',evidence=False)
   own=e.claim(SECRET);rid=own['id'];token=own['token'];query(e,tid,rid,'continue_work',token,True,False)
   e.change(rid,token);query(e,tid,rid,'verify',token,True,False)
   result=e.verify(rid,token);require(result['passed'] is True,'owned native fixture did not verify');query(e,tid,rid,'ready_review',token,True,True)
   e.transition(rid,token,'ready_review');query(e,tid,rid,'await_independent_review',token,True,True)
   e.transition(rid,token,'accepted');query(e,tid,rid,'done',token,True,True)
   # Historical acceptance survives a newer loaded controller; no implicit invalidation.
   proof=json.loads(e.db.execute('SELECT evidence FROM runs WHERE id=?',(rid,)).fetchone()[0]);proof['workflow']='f'*64;e.db.execute('UPDATE runs SET workflow=?,evidence=? WHERE id=?',('f'*64,json.dumps(proof),rid));query(e,tid,rid,'done',token,True,True)
   (repo/'README.txt').write_text('different source '+SECRET);query(e,tid,rid,'owner_reassess',token,False,False)
  finally:e.db.close()

def proof_and_fencing(m):
 with tempfile.TemporaryDirectory(prefix='p21-proof-') as name:
  e,tid,repo=fixture(m,Path(name))
  try:
   own=e.claim(SECRET);rid=own['id'];token=own['token'];e.change(rid,token);ev=e.verify(rid,token)['evidence'];candidate=ev['candidate'];wf=e.workflow
   def state(value,proof=None,workflow=wf,lease=None,revision=1):
    e.db.execute('UPDATE runs SET state=?,candidate=?,evidence=?,workflow=?,lease_until=?,deadline=?,revision=? WHERE id=?',(value,candidate,json.dumps(ev if proof is None else proof),workflow,time.time()+100 if lease is None else lease,time.time()+500,revision,rid));e.db.execute('UPDATE tasks SET state=?,revision=1 WHERE id=?',(value,tid))
   state('running',workflow='f'*64);query(e,tid,rid,'use_claimed_controller',token)
   state('verifying');query(e,tid,rid,'wait',token)
   (repo/'README.txt').write_text('active source drift');query(e,tid,rid,'wait',token,False)
   state('verified');query(e,tid,rid,'change',token,False,False)
   (repo/'README.txt').write_text('owned next-step fixture\n')
   corruptions=[]
   for field,bad in [('baseline','b'*64),('candidate','c'*64),('workflow','d'*64),('task_revision',True),('source_unchanged',False),('ownership_valid_at_finish',False)]:
    p=copy.deepcopy(ev);p[field]=bad;corruptions.append(p)
   p=copy.deepcopy(ev);p['results'][0]['exit_code']=False;corruptions.append(p)
   p=copy.deepcopy(ev);p['results'][0]['argv']=[SECRET];corruptions.append(p)
   p=copy.deepcopy(ev);p['policy']['budget']['max_attempts']=99;corruptions.append(p)
   for p in corruptions:state('verified',proof=p);query(e,tid,rid,'change',token,True,False)
   state('accepted',proof=corruptions[0]);query(e,tid,rid,'owner_reassess',token,True,False)
   state('verified',revision=2);query(e,tid,rid,'owner_reassess',token,True,False)
   # Expired raw running rows remain untouched; attempts are not refunded.
   state('running',lease=0);query(e,tid,rid,'claim',token)
   second=dict(e.db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone());second.update(id='run-native-second-attempt',state='failed')
   names=list(second);e.db.execute('INSERT INTO runs ('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(second[k] for k in names));e.db.execute("UPDATE tasks SET state='queued' WHERE id=?",(tid,));query(e,tid,second['id'],'owner_reassess',token)
  finally:e.db.close()

def wire_and_rejections(m):
 from common import REPO
 import subprocess
 with tempfile.TemporaryDirectory(prefix='p21-wire-') as name:
  base=Path(name);e,tid,repo=fixture(m,base);own=e.claim(SECRET);rid=own['id'];token=own['token'];e.change(rid,token);e.verify(rid,token);e.transition(rid,token,'ready_review');e.transition(rid,token,'accepted');expected=query(e,tid,rid,'done',token,True,True);before=rows(e.db)
  try:
   for args in ({},{'task_id':tid,'run_id':rid},{'run_id':SECRET},{'task_id':[]},{'run_id':rid,'token':token}):
    try:e.next_step(**args)
    except (ValueError,TypeError) as error:require(SECRET not in str(error) and token not in str(error),'rejection echoed private/untrusted values')
    else:raise AssertionError('invalid selector or authority argument accepted')
   require(before==rows(e.db),'invalid direct request mutated state')
  finally:e.db.close()
  env=dict(os.environ,PYTHONPATH=str(REPO),PYTHONDONTWRITEBYTECODE='1');cli=[sys.executable,'-B','-m','agent_workflow','--state',str(base/'state')];before=snapshot(base/'state')
  p=subprocess.run(cli+['--config',str(base/'missing-private-config'),'next_step','--args',json.dumps({'run_id':rid})],cwd=REPO,env=env,capture_output=True,text=True,timeout=10);require(p.returncode==0 and json.loads(p.stdout)=={'ok':True,'result':expected},'readonly CLI initialized/read config or diverged')
  missing=base/'missing-state';p=subprocess.run([sys.executable,'-B','-m','agent_workflow','--state',str(missing),'next_step','--args',json.dumps({'run_id':SECRET})],cwd=REPO,env=env,capture_output=True,text=True,timeout=10);require(p.returncode!=0 and not missing.exists() and SECRET not in p.stdout,'missing state created or private input echoed')
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'next_step','arguments':{'run_id':rid}}},{'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'capabilities','arguments':{}}},{'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'next_step','arguments':{'run_id':rid,'token':token}}}]
  p=subprocess.run(cli+['mcp'],cwd=REPO,env=env,input='\n'.join(json.dumps(r) for r in requests)+'\n',capture_output=True,text=True,timeout=10);require(p.returncode==0,'MCP guidance server failed');replies={r['id']:r for r in map(json.loads,p.stdout.splitlines())};tools=replies[2]['result']['tools'];tool=next(t for t in tools if t['name']=='next_step');require(tool['inputSchema']=={'type':'object','properties':{'task_id':{'type':'string'},'run_id':{'type':'string'}},'required':[],'additionalProperties':False},'next_step tool admits authority args');require(json.loads(replies[3]['result']['content'][0]['text'])==expected,'MCP/Core snapshot diverged');cap=json.loads(replies[4]['result']['content'][0]['text']);require(len(tools)==27 and cap['interfaces']['mcp']['tools']==sorted(t['name'] for t in tools),'capability catalog does not reflect27 actualtools');require(replies[5]['error']['code']==-32602 and 'result' not in replies[5] and token not in p.stdout and SECRET not in p.stdout,'MCP authority rejection leaks');require(snapshot(base/'state')==before,'CLI/MCP observation mutated acceptance/evidence/lease/events/policy')

run('iterail-next-step','core',[('readonly_lifecycle_advice_without_role_or_deployment_authority',lifecycle),('Source_proof_version_lease_and_attempt_fencing',proof_and_fencing),('CLI_MCP_parity_pure_missing_state_and_untrusted_rejection',wire_and_rejections)])
