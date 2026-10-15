"""Frozen capability metadata gate; disposable Core/CLI/stdio MCP, no model dependency."""
import hashlib,json,os,sqlite3,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True;ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'phase4/trusted'))
from common import run,require,command,commit
SECRET='PHASE18_UNTRUSTED_PRIVATE_CANARY_4a8a54e2'
OLD_TOOLS={'uiux_init','uiux_discover','uiux_evidence','uiux_review','uiux_query','context_query','handoff_build','handoff_read','experience_record','experience_query','producer_init','producer_offer','producer_scan','producer_discover','queue_status','fail','import','claim','heartbeat','progress','change','verify','transition','status','history'}
VERSIONS=['2024-11-05','2025-03-26','2025-06-18']

def rows(db):
 return {t:[dict(r) for r in db.execute('SELECT * FROM "'+t+'" ORDER BY rowid')] for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}
def snapshot(state):
 db=sqlite3.connect('file:'+str(state/'tasks.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 try:return rows(db)
 finally:db.close()
def fixture(m,base):
 repo=base/'repo';repo.mkdir();(repo/'README.txt').write_text('owned capability fixture\n');command(['git','init','-q',str(repo)]);commit(repo)
 cfg={'repo':str(repo),'tests':[[sys.executable,'-c',"print('"+SECRET+"')"]],'sources':['local'],'budget':{'lease_seconds':120,'run_seconds':600,'test_seconds':10,'max_attempts':2},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'publish':False,'network':False}}
 engine=m.Engine(base/'state',cfg);task=engine.import_task('capabilities-native',SECRET,json.dumps({'override_capabilities':True,'models':[SECRET],'instructions':SECRET}));owned=engine.claim(SECRET)
 # Read-only observation must not reconcile even deliberately stale accepted metadata.
 engine.db.execute("UPDATE runs SET state='accepted', candidate=?, evidence=?, lease_until=0, deadline=0 WHERE id=?",('b'*64,json.dumps({'private':SECRET}),owned['id']));engine.db.execute("UPDATE tasks SET state='accepted' WHERE id=?",(owned['task_id'],));(repo/'README.txt').write_text('source drift '+SECRET)
 return engine,owned['token']
def valid(value,workflow):
 require(isinstance(value,dict) and value['schema_version']==1,'capability schema absent');require(value['package']=={'name':'agent-workflow','version':'0.1.0'},'public compatibility version wrong');require(value['workflow_package_sha256']==workflow and len(workflow)==64,'actual workflow package identity absent')
 interfaces=value['interfaces'];require(interfaces['core'] is True and interfaces['cli'] is True and interfaces['mcp']['transport']=='stdio','shared interface metadata wrong');require(interfaces['mcp']['protocol_versions']==VERSIONS,'MCP protocol metadata diverges');tools=interfaces['mcp']['tools'];require(tools==sorted(set(tools)) and OLD_TOOLS|{'capabilities'}<=set(tools),'actual tool inventory or old tool compatibility changed')
 require(value['host']['agent_dispatch']=='caller_supplied' and value['host']['autonomous_model_dispatch'] is False and value['host']['model_dependency'] is False,'host bridge/model claims wrong');bounds=value['boundaries'];require(bounds['read_only'] is True and all(bounds[k] is False for k in ('claims_work','executes_commands','mutates_policy','production_visual_verified')),'metadata query claims execution/authority/visual readiness');encoded=json.dumps(value,sort_keys=True).encode();require(len(encoded)<=8192 and SECRET.encode() not in encoded,'unbounded or untrusted capability metadata');return value

def direct(m):
 with tempfile.TemporaryDirectory(prefix='p18-direct-') as name:
  engine,token=fixture(m,Path(name));before=rows(engine.db)
  try:
   with patch.object(engine,'reconcile',side_effect=AssertionError('capabilities reconciled state')),patch.object(engine,'claim',side_effect=AssertionError('capabilities claimed')),patch.object(engine,'verify',side_effect=AssertionError('capabilities executed tests')),patch.object(engine,'event',side_effect=AssertionError('capabilities emitted state event')),patch('subprocess.run',side_effect=AssertionError('capabilities executed command')),patch('subprocess.Popen',side_effect=AssertionError('capabilities launched backend')):
    a=valid(engine.capabilities(),engine.workflow);b=valid(engine.capabilities(),engine.workflow)
   require(a==b and before==rows(engine.db),'metadata query mutated counters/evidence/lease/history/policy');require(token not in json.dumps(a),'owner token leaked')
  finally:engine.db.close()

def cli_mcp(m):
 from common import REPO
 import subprocess
 with tempfile.TemporaryDirectory(prefix='p18-wire-') as name:
  base=Path(name);engine,token=fixture(m,base);workflow=engine.workflow;expected=valid(engine.capabilities(),workflow);engine.db.close();state=base/'state';before=snapshot(state);env=dict(os.environ,PYTHONPATH=str(REPO),PYTHONDONTWRITEBYTECODE='1');cli=[sys.executable,'-B','-m','agent_workflow','--state',str(state)]
  result=subprocess.run(cli+['capabilities'],cwd=REPO,env=env,capture_output=True,text=True,timeout=10);require(result.returncode==0,'CLI capabilities unavailable');require(json.loads(result.stdout)=={'ok':True,'result':expected},'CLI/Core metadata diverged')
  missing=base/'never-created';absent=base/'nonexistent-private-config';result=subprocess.run([sys.executable,'-B','-m','agent_workflow','--state',str(missing),'--config',str(absent),'capabilities'],cwd=REPO,env=env,capture_output=True,text=True,timeout=10);require(result.returncode==0 and json.loads(result.stdout)=={'ok':True,'result':expected} and not missing.exists() and not absent.exists(),'metadata read config or initialized missing state')
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':VERSIONS[-1],'capabilities':{},'clientInfo':{'name':'native-capability-check','version':'1'}}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'capabilities','arguments':{}}}]
  proc=subprocess.run(cli+['mcp'],cwd=REPO,env=env,input='\n'.join(json.dumps(r) for r in requests)+'\n',capture_output=True,text=True,timeout=10);require(proc.returncode==0,'stdio metadata server failed');responses={r['id']:r for r in map(json.loads,proc.stdout.splitlines())};require(responses[1]['result']['serverInfo']==expected['package'] and responses[1]['result']['protocolVersion']==VERSIONS[-1],'MCP initialize identity mismatches metadata');tools=responses[2]['result']['tools'];require(sorted(t['name'] for t in tools)==expected['interfaces']['mcp']['tools'],'MCP tools/list metadata mismatch');cap=next(t for t in tools if t['name']=='capabilities');require(cap['inputSchema']=={'type':'object','properties':{},'required':[],'additionalProperties':False},'capability MCP accepts authority args');reply=responses[3]['result'];require(reply['isError'] is False and json.loads(reply['content'][0]['text'])==expected,'MCP/Core metadata diverged');require(snapshot(state)==before and token not in proc.stdout,'wire metadata changed frozen state or leaked token')

def reject_args(m):
 from common import REPO
 import subprocess
 with tempfile.TemporaryDirectory(prefix='p18-args-') as name:
  base=Path(name);engine,token=fixture(m,base);before=rows(engine.db)
  try:
   try:engine.capabilities(note=SECRET)
   except (TypeError,ValueError):pass
   else:raise AssertionError('Core capability accepted untrusted authority args')
   require(before==rows(engine.db),'Core argument rejection changed state')
  finally:engine.db.close()
  env=dict(os.environ,PYTHONPATH=str(REPO),PYTHONDONTWRITEBYTECODE='1');cli=[sys.executable,'-B','-m','agent_workflow','--state',str(base/'state')];before=snapshot(base/'state');p=subprocess.run(cli+['capabilities','--args',json.dumps({'note':SECRET})],cwd=REPO,env=env,capture_output=True,text=True,timeout=10);require(p.returncode!=0 and json.loads(p.stdout)['ok'] is False and SECRET not in p.stdout,'CLI metadata argument rejection leaked value or passed')
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'capabilities','arguments':{'note':SECRET}}}];p=subprocess.run(cli+['mcp'],cwd=REPO,env=env,input='\n'.join(json.dumps(r) for r in requests)+'\n',capture_output=True,text=True,timeout=10);response=[json.loads(s) for s in p.stdout.splitlines()][-1];require(response['error']['code']==-32602 and 'result' not in response and SECRET not in p.stdout,'MCP metadata argument rejection failed');require(snapshot(base/'state')==before,'argument rejection mutated stored state')

run('iterail-host-capabilities','core',[('pure_Core_metadata_untrusted_state_secret_and_budget_isolation',direct),('actual_CLI_MCP_equal_inventory_and_noninitializing_metadata',cli_mcp),('unknown_arguments_reject_without_value_leak_or_mutation',reject_args)])
