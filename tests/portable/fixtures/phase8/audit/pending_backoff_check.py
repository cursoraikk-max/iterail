"""Actual selected-Core CLI timeout fixture for durable pending backoff. No model calls."""
import ast,hashlib,importlib,json,subprocess,sys,traceback
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];SOURCE=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(ROOT/'phase5/trusted'));from common import source_digest
sys.path.insert(0,str(SOURCE));module=importlib.import_module('agent_workflow.continuation')
# Reuse the frozen owner/Core producer fixture without executing its test runner.
helper=ROOT/'phase8/trusted/check.py';tree=ast.parse(helper.read_text());cut=next(i for i,node in enumerate(tree.body) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='before' for t in node.targets));tree.body=tree.body[:cut]
h={'__file__':str(helper)};exec(compile(tree,str(helper),'exec'),h)
base_cli=h['cli'];original_run=subprocess.run;f=None;results=[];error=None

def instrumented_cli(runtime,state,action,args=None,config=None,expect=0):
 if action=='init':
  entry=runtime/'agent_workflow/__main__.py'
  shim='''import sys,time\nfrom pathlib import Path\nif 'status' in sys.argv:\n _native_root=Path(__file__).resolve().parents[1]\n _native_counter=_native_root/'native-status-count.txt'\n _native_counter.write_text(str(int(_native_counter.read_text())+1) if _native_counter.exists() else '1')\n if (_native_root/'native-slow-status.flag').exists():time.sleep(2)\n'''
  entry.write_text(shim+entry.read_text())
 return base_cli(runtime,state,action,args,config,expect)

def fixture_transport(argv,*args,**kwargs):
 # Reduce only the fixture's physical Python Core status timeout; still execute
 # the exact selected frozen Core CLI. Product logic and owner metadata are intact.
 if f is not None and isinstance(argv,list) and 'agent_workflow' in argv and 'status' in argv:
  kwargs['timeout']=min(kwargs.get('timeout',30),0.5)
 return original_run(argv,*args,**kwargs)

before=source_digest(SOURCE)
try:
 h['cli']=instrumented_cli;f=h['fixture'](module,bad=True)
 q=h['request'](f['c'].tick(),'worker');run=base_cli(f['runtime'],f['state'],'claim',{'worker':'native-pending-timeout-worker','task_id':q['task_id']});f['c'].bind_run(q['id'],run['id'])
 counter=f['runtime']/'native-status-count.txt';counter.write_text('0');flag=f['runtime']/'native-slow-status.flag';flag.write_text('native transport delay; no model/provider')
 initial=f['c'].status();deadline=initial['deadline'];spent=initial['requests_spent'];pending=initial['request'];snapshots=[]
 with patch.object(module.subprocess,'run',fixture_transport):
  first=f['c'].tick();firstmeta=f['c'].status();firstfail=f['c'].db.execute('SELECT failure_count FROM meta WHERE id=1').fetchone()[0]
  snapshots.append({'clock':1000,'phase':first['phase'],'physical_status_attempts':int(counter.read_text()),'next_due':firstmeta['next_due'],'failure_count':firstfail})
  h['require'](first['phase']=='backoff' and firstmeta['next_due']==1005 and firstfail==1,'fixture did not produce actual physical Core timeout/backoff')
  for now in [1001.0,1002.0]:
   f['clock'][0]=now
   h['reopen'](module,f)
   response=f['c'].tick();state=f['c'].status();failures=f['c'].db.execute('SELECT failure_count FROM meta WHERE id=1').fetchone()[0]
   snapshots.append({'clock':now,'phase':response['phase'],'physical_status_attempts':int(counter.read_text()),'next_due':state['next_due'],'failure_count':failures})
  late=f['c'].status();passed=all(s['phase']=='not_due' and s['physical_status_attempts']==1 and s['next_due']==1005 and s['failure_count']==1 for s in snapshots[1:])
  passed=passed and late['request']==pending and late['deadline']==deadline and late['requests_spent']==spent
  if passed:
   flag.unlink();f['clock'][0]=1005.0;recovered=f['c'].tick();h['require'](recovered['phase']=='in_flight' and int(counter.read_text())==2,'due retry did not observe the same real Core lease');snapshots.append({'clock':1005,'phase':recovered['phase'],'physical_status_attempts':int(counter.read_text())})
 results.append({'case':'pending_Core_status_timeout_respects_durable_due_across_restart','passed':passed,'snapshots':snapshots,'pending_preserved':late['request']==pending,'deadline_preserved':late['deadline']==deadline,'reserved_pair_preserved':late['requests_spent']==spent,'actual_core_run_id':run['id'],'native_timeout_seconds':0.5,'native_controller_package_sha256':source_digest(f['runtime']/'agent_workflow'),'fixture_kind':'owner-approved status-delay shim; actual selected Python project CLI; bounded injected transport ceiling'})
except Exception as exc:
 error=type(exc).__name__+': '+str(exc);passed=False;results.append({'case':'fixture_execution','passed':False,'error':error,'traceback':traceback.format_exc()[-5000:]})
finally:
 if f is not None:h['done'](f)
after=source_digest(SOURCE);passed=passed and before==after
print(json.dumps({'id':'iterail-pending-backoff','passed':passed,'classification':'passed' if passed else ('infrastructure_error' if error else 'product_defect'),'exactsourceSHA':before,'source_stable':before==after,'source_unchanged':before==after,'evidence_kind':'native_fixture','external_provider_calls':0,'autonomous_internal_model_dispatch':False,'evidence':results},sort_keys=True));raise SystemExit(0 if passed else 1)
