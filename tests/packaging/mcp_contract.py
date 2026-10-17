#!/usr/bin/env python3
"""Frozen legacy schema, adapter dispatch and real optional-scope contracts."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

p=argparse.ArgumentParser();p.add_argument('--installed',action='store_true');a=p.parse_args()
ROOT=Path(__file__).resolve().parents[2]
if not a.installed:sys.path.insert(0,str(ROOT))
from agent_workflow import mcp
from agent_workflow.core import Engine
from agent_workflow.scope import initialize

LEGACY=json.loads((Path(__file__).with_name('legacy_mcp_fields.json')).read_text())
assert len(mcp.FIELDS)==27, 'legacy global FIELDS must remain27; optional scope metadata is separate'
assert json.loads(json.dumps(mcp.FIELDS))==LEGACY, 'legacy tool names, fields, types or required fields changed'

def transcript(calls):
 return [{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
         {'jsonrpc':'2.0','method':'notifications/initialized'},
         *[dict(jsonrpc='2.0',id=i+2,method=method,params=params) for i,(method,params) in enumerate(calls)]]

class AdapterFixture:
 """Dispatch-only fixture, not a real task/backend execution."""
 def __init__(self,scoped):self.config={'engineering_scope':{}} if scoped else {}
 def __getattr__(self,name):return lambda **args:{'method':name,'arguments':args}

def adapter(scoped,calls):
 inp=io.StringIO(''.join(json.dumps(x)+'\n' for x in transcript(calls)));out=io.StringIO()
 with patch.object(sys,'stdin',inp),patch.object(sys,'stdout',out):mcp.serve(AdapterFixture(scoped))
 return [json.loads(x) for x in out.getvalue().splitlines()][1:]

def result(row):
 assert not row['result'].get('isError'),row
 return json.loads(row['result']['content'][0]['text'])

listed=adapter(False,[('tools/list',{})])[0]['result']['tools']
assert {x['name'] for x in listed}==set(LEGACY)
for tool in listed:
 fields,required=LEGACY[tool['name']]
 assert tool['inputSchema']=={'type':'object','properties':{k:{'type':v} for k,v in fields.items()},'required':required,'additionalProperties':False}
 # Every existing adapter route retains the exact Core method and arguments.
 args={k:'fixture-value' for k in required}
 row=adapter(False,[('tools/call',{'name':tool['name'],'arguments':args})])[0]
 expected_method='import_task' if tool['name']=='import' else tool['name']
 assert result(row)=={'method':expected_method,'arguments':args}
 bad=adapter(False,[('tools/call',{'name':tool['name'],'arguments':{**args,'not_in_schema':'x'}})])[0]
 assert bad['error']['code']==-32602
 if required:
  missing=dict(args);missing.pop(required[0]);assert adapter(False,[('tools/call',{'name':tool['name'],'arguments':missing})])[0]['error']['code']==-32602
 if fields:
  wrong={**args,next(iter(fields)):123};assert adapter(False,[('tools/call',{'name':tool['name'],'arguments':wrong})])[0]['error']['code']==-32602

scope_names={'scope_status','scope_preflight','scope_repair'}
scoped={x['name']:x for x in adapter(True,[('tools/list',{})])[0]['result']['tools']}
assert set(scoped)==set(LEGACY)|scope_names and len(scoped)==30
for name in set(LEGACY)-{'claim'}:
 assert scoped[name]==next(x for x in listed if x['name']==name)
claim=scoped['claim']['inputSchema']
assert claim['properties']=={k:{'type':'string'} for k in ('worker','task_id','request_id')} and set(claim['required'])=={'worker','task_id','request_id'}
for name in ('scope_status','scope_preflight'):
 assert scoped[name]['inputSchema']=={'type':'object','properties':{},'required':[],'additionalProperties':False}
assert scoped['scope_repair']['inputSchema']=={'type':'object','properties':{k:{'type':'string'} for k in ('run_id','token','repair_id','reason')},'required':['run_id','token','repair_id','reason'],'additionalProperties':False}
for name in scope_names:
 assert adapter(False,[('tools/call',{'name':name,'arguments':{}})])[0]['error']['code']==-32602
# Catalog creation in either order must not mutate the legacy metadata.
assert len(adapter(False,[('tools/list',{})])[0]['result']['tools'])==27
assert json.loads(json.dumps(mcp.FIELDS))==LEGACY

with tempfile.TemporaryDirectory(prefix='iterail-mcp-contract-') as temp:
 home=Path(temp).resolve();repo=home/'repo';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True,capture_output=True)
 sentinel=home/'must-not-run'
 tests=[[sys.executable,'-c',f'from pathlib import Path;Path({str(sentinel)!r}).touch()']]
 policy={'schema_version':1,'scope_id':'mcp-contract','repo':str(repo),'ledger':str(home/'owner/scope.sqlite'),'started':time.time()-1,'deadline':time.time()+120,'max_dispatches':1,'max_repairs':1,'python':sys.executable,'build_requirements':{},'tests':tests}
 owner=home/'owner.json';owner.write_text(json.dumps(policy));descriptor={'path':str(owner),'sha256':hashlib.sha256(owner.read_bytes()).hexdigest()};initialize(descriptor)
 config={'repo':str(repo),'tests':tests,'sources':['local'],'budget':{'lease_seconds':60,'run_seconds':120,'test_seconds':10,'max_attempts':1},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'network':False,'publish':False},'engineering_scope':descriptor}
 state=home/'state';engine=Engine(state,config);task=engine.import_task('mcp-work','actual Core scope contract')
 prefix=[sys.executable,'-I','-B','-m','agent_workflow'] if a.installed else [sys.executable,'-B','-m','agent_workflow']
 env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
 if not a.installed:env['PYTHONPATH']=str(ROOT)
 def cli(name,args={}):
  x=subprocess.run(prefix+['--state',str(state),name,'--args',json.dumps(args)],env=env,cwd=home,capture_output=True,text=True,timeout=20)
  return json.loads(x.stdout)
 def rpc(calls):
  x=subprocess.run(prefix+['--state',str(state),'mcp'],input=''.join(json.dumps(v)+'\n' for v in transcript(calls)),env=env,cwd=home,capture_output=True,text=True,timeout=20)
  assert x.returncode==0,x.stderr
  return [json.loads(v) for v in x.stdout.splitlines()][1:]
 def call(name,args={}):return rpc([('tools/call',{'name':name,'arguments':args})])[0]
 before=cli('scope_status')['result'];assert before['dispatches']==before['repairs']==0
 assert result(call('scope_status'))==before
 for name in ('scope_status','scope_preflight'):
  assert call(name,{'unexpected':'x'})['error']['code']==-32602
 pre=result(call('scope_preflight'));assert pre['passed'] and not pre['executes_project_tests'] and not pre['provider_ready'] and not sentinel.exists()
 assert cli('scope_status')['result']['dispatches']==0
 # Schema failures must be rejected before admission/debit.
 assert call('claim',{'worker':'worker','task_id':task['id']})['error']['code']==-32602
 assert cli('scope_status')['result']['dispatches']==0
 run=result(call('claim',{'worker':'worker','task_id':task['id'],'request_id':'mcp/request'}))
 assert result(call('claim',{'worker':'worker','task_id':task['id'],'request_id':'mcp/request'}))['id']==run['id']
 args={'run_id':run['id'],'token':run['token'],'repair_id':'repair-one','reason':'contract repair'}
 assert call('scope_repair',{**args,'token':'wrong'})['result']['isError']
 assert call('scope_repair',{k:v for k,v in args.items() if k!='reason'})['error']['code']==-32602
 assert cli('scope_status')['result']['repairs']==0
 first=result(call('scope_repair',args));assert first['dispatches']==first['repairs']==1
 assert cli('scope_repair',args)['result']['repairs']==1
 assert call('scope_repair',{**args,'reason':'changed'})['result']['isError']
 assert call('scope_repair',{**args,'repair_id':'over-cap'})['result']['isError']
 assert call('scope_repair',{**args,'deadline':'9999999999'})['error']['code']==-32602
 end=cli('scope_status')['result'];assert end['deadline']==policy['deadline'] and end['repairs']==1 and end['dispatches']==1
 assert result(call('capabilities'))['interfaces']['mcp']['tools']==sorted(scoped)
 assert cli('scope_status')['result']==result(call('scope_status')) and not sentinel.exists()
 assert not {'scope_init','scope_reset','scope_resolve_not_started'}&set(scoped)
 engine.db.close()

print(json.dumps({'passed':True,'legacy_tools':sorted(LEGACY),'legacy_count':27,'scoped_count':30,'optional_tools':sorted(scope_names),'legacy_schema_exact':True,'all27_adapter_routes_checked':True,'real_stdio_scope_boundaries':True,'scope_permissions':'owner initialized; repair requires live owned run; no policy mutation or project execution','model_calls':0,'network_calls':0}))
