"""Frozen startup infrastructure block regression. Native actors only; no sockets/Codex."""
import hashlib,json,sqlite3,sys
from pathlib import Path
import startup_common as common
from startup_common import require,meta,close,command,commit,source_digest
ROOT=Path(__file__).resolve().parents[2]
SYNTHETIC_LOGS=[ROOT/'startup-errors'/name for name in ('pre-session-a.log','pre-session-b.log')]
SIGNATURE='Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)'
WARNING='WARNING: proceeding, even though we could not create PATH aliases: Operation not permitted (os error 1)'
ACTOR=r"""import json,sys
from pathlib import Path
kind,repo,slot,verdict,mode,counter=sys.argv[1:7]
p=Path(counter);p.write_text(str(int(p.read_text())+1))
signature='Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)'
warning='WARNING: proceeding, even though we could not create PATH aliases: Operation not permitted (os error 1)'
if mode=='exact':print(warning);print(signature)
elif mode=='unknown':print('Error: unrelated operation failed: Operation not permitted (os error 1)')
elif mode=='warning':print(warning);print('Error: NATIVE_UNKNOWN_STARTUP_FAILURE')
elif mode=='quoted':print(json.dumps({'type':'item.completed','item':{'type':'command_execution','aggregated_output':signature,'exit_code':1}}));print('Error: NATIVE_UNKNOWN_BACKEND_FAILURE')
elif mode=='late':print(json.dumps({'type':'thread.started','thread_id':'native-simulated-already-started'}));print(json.dumps({'type':'turn.started'}));print(signature)
else:raise SystemExit('unknown native mode')
raise SystemExit(1)
"""
def fixture(m,mode):
 tmp,root,c=common.fixture(m,max_calls=4,wall=600);repo=root/'shell-project';repo.mkdir();(repo/'app.sh').write_text('#!/bin/sh\nexit 1\n');command(['git','init','-q',str(repo)]);commit(repo)
 checker=Path(c['checks'][0]['argv'][1]);checker.write_text("import hashlib,json,sys\nfrom pathlib import Path\nr=Path(sys.argv[1]).resolve();h=hashlib.sha256()\nfor p in sorted(r.rglob('*')):\n rel=p.relative_to(r)\n if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or p.suffix=='.pyc':continue\n if p.is_file():h.update(str(rel).encode()+b'\\0'+p.read_bytes()+b'\\0')\nprint(json.dumps({'id':'fixture-check','passed':False,'classification':'product_defect','exactsourceSHA':h.hexdigest(),'source_stable':True,'evidence':['native shell failure control, not a real model improvement']}))\nraise SystemExit(1)\n")
 backend=Path(c['backend']);backend.write_text('#!'+sys.executable+'\n'+ACTOR);backend.chmod(0o700);counter=root/'native-launch-count';counter.write_text('0');c['backend_adapter']['argv'] += [mode,str(counter)];c.update(repo=str(repo),project={'id':'native-startup-block','kind':'target'},self_update=[],allowlist=['app.sh']);c['budget']['call_seconds']=5
 return tmp,root,c,counter

def observations(root):
 backend=[];claims=0;turns=0
 for p in root.rglob('runner.sqlite'):
  d=sqlite3.connect('file:'+str(p.resolve())+'?mode=ro',uri=True)
  try:
   for kind,raw in d.execute("SELECT kind,result FROM calls WHERE kind LIKE 'backend:%' ORDER BY rowid"):
    r=json.loads(raw);data=Path(r['output']).read_bytes();require(hashlib.sha256(data).hexdigest()==r['saved_sha256'],'raw simulated evidence hash mismatch');require(r['group_termination_confirmed'] is True,'native actor group not cleaned')
    for line in data.decode(errors='replace').splitlines():
     try:e=json.loads(line)
     except ValueError:continue
     if isinstance(e,dict) and e.get('type')=='turn.completed':turns+=1
    backend.append({'kind':kind,'exit_code':r['exit_code'],'raw_sha256':hashlib.sha256(data).hexdigest()})
  finally:d.close()
 for p in root.rglob('tasks.sqlite'):
  d=sqlite3.connect('file:'+str(p.resolve())+'?mode=ro',uri=True);claims+=d.execute('SELECT count(*) FROM runs').fetchone()[0];d.close()
 return {'backend_launches':len(backend),'reviewer_launches':sum(r['kind']=='backend:reviewer' for r in backend),'successful_model_turns':turns,'Core_claims':claims,'backend':backend}

def accounting(s,expected):
 status=s.status();a=status.get('accounting');require(isinstance(a,dict),'public launch/model/reviewer reservation accounting absent')
 for key,value in expected.items():require(a.get(key)==value,'untruthful public accounting '+key+': '+repr(a.get(key)))
 recovery=a.get('recovery');require(isinstance(recovery,dict),'infrastructure recovery conditions absent')
 require(recovery.get('requires_owner_review') is True and recovery.get('automatic_retry_allowed') is False and recovery.get('reservation_refund_allowed') is False and recovery.get('budget_reset_allowed') is False,'recovery silently authorizes retry/refund/reset')
 require(isinstance(recovery.get('conditions'),list) and bool(recovery['conditions']) and all(isinstance(x,str) and x for x in recovery['conditions']),'explicit recovery conditions missing')
 return a

def exact(m):
 tmp,root,c,counter=fixture(m,'exact');s=None
 try:
  s=m.Supervisor(root/'owner',c);before=meta(s);head=command(['git','-C',c['repo'],'rev-parse','HEAD']);source=source_digest(c['repo']);first=s.cycle();after_first=meta(s);second=s.cycle();obs=observations(root)
  details={'first_phase':first.get('phase'),'first_failure_class':first.get('failure_class'),'first_blocked':after_first['blocked'],'first_stopped':after_first['stopped'],'second_phase':second.get('phase'),'actual_launches':int(counter.read_text()),'reserved_calls':meta(s)['calls'],**obs}
  require(first.get('phase')=='blocked' and first.get('failure_class')=='infrastructure_blocked' and after_first['blocked']=='infrastructure_blocked' and after_first['stopped'],'first confirmed infrastructure failure not immediately durably stopped: '+json.dumps(details,sort_keys=True))
  require(details['actual_launches']==obs['backend_launches']==1 and obs['reviewer_launches']==obs['successful_model_turns']==obs['Core_claims']==0 and meta(s)['calls']==2,'second worker launched or unexecuted review reservation refunded: '+json.dumps(details,sort_keys=True))
  public=accounting(s,{'actual_backend_launches':1,'successful_model_turns':0,'reviewer_launches':0,'unexecuted_reviewer_reservations':1})
  require(meta(s)['started']==before['started'] and meta(s)['deadline']==before['deadline'],'startup block resets owner wall')
  s.set_stop(False);resume=s.cycle();require(meta(s)['blocked']=='infrastructure_blocked' and int(counter.read_text())==1 and meta(s)['calls']==2,'stop-resume bypassed infrastructure block')
  close(s);s=m.Supervisor(root/'owner');restarted=s.cycle();again=accounting(s,{'actual_backend_launches':1,'successful_model_turns':0,'reviewer_launches':0,'unexecuted_reviewer_reservations':1})
  require(meta(s)['blocked']=='infrastructure_blocked' and int(counter.read_text())==1 and meta(s)['calls']==2 and meta(s)['started']==before['started'] and meta(s)['deadline']==before['deadline'],'restart redispatched/refunded/reset blocked owner')
  require(source_digest(c['repo'])==source and command(['git','-C',c['repo'],'rev-parse','HEAD'])==head,'startup diagnosis mutated target')
  return dict(details,public_accounting=public,restarted_phase=restarted['phase'],recovery_accounting=again)
 finally:
  if s:close(s)
  tmp.cleanup()

def conservative(m):
 results=[]
 for mode in ('unknown','warning','quoted','late'):
  tmp,root,c,counter=fixture(m,mode);s=None
  try:
   s=m.Supervisor(root/'owner',c);before=meta(s);r=s.cycle();after=meta(s);obs=observations(root);require(r.get('failure_class')!='infrastructure_blocked' and after['blocked']!='infrastructure_blocked' and not after['stopped'],'ambiguous/non-startup text invented infrastructure block: '+mode)
   require(after['calls']==2 and after['deadline']==before['deadline'] and int(counter.read_text())==1 and obs['Core_claims']==obs['successful_model_turns']==obs['reviewer_launches']==0,'unknown failure accounting changed')
   results.append({'mode':mode,'failure_class':r.get('failure_class'),'phase':r.get('phase'),'native_launches':1,'Core_claims':0})
  finally:
   if s:close(s)
   tmp.cleanup()
 return {'controls':results}

def synthetic_logs(m):
 facts=[]
 for p in SYNTHETIC_LOGS:
  raw=p.read_bytes();text=raw.decode(errors='replace');require(SIGNATURE in text,'synthetic pre-session signature fixture changed');types=[]
  for line in text.splitlines():
   try:e=json.loads(line)
   except ValueError:continue
   if isinstance(e,dict):types.append(e.get('type'))
  require(not any(x in types for x in ('thread.started','turn.started','turn.completed')),'synthetic pre-session fixture contains a session marker')
  facts.append({'filename':p.name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'confirmed_pre_session_initialization_failure':True})
 return {'synthetic_pre_session_logs':facts,'provider_calls_this_check':0}
common.run('iterail-startup-infrastructure-block','evolution',[('synthetic_pre_session_signature_fixture',synthetic_logs),('first_failure_immediate_durable_block_no_second_launch_no_refund',exact),('ambiguous_warning_tool_and_post_session_os_errors_remain_general',conservative)])
