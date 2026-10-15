"""Independent native context boundary/CLI/MCP probe. Never external backends."""
import sys,json,subprocess,tempfile,copy,shutil,hashlib,base64
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import source_digest,load,require,rejects
repo=Path(sys.argv[1]).resolve();before=source_digest(repo);ctx=load(repo,'context');core=load(repo,'core');cases=[]

def case(name,fn):
 try:fn();cases.append({'case':name,'exit_code':0})
 except Exception as e:cases.append({'case':name,'exit_code':1,'error':type(e).__name__+': '+str(e)})
def cfg(root):
 return {'repo':str(root),'tests':[[sys.executable,'-c','print("benign λ")',base64.b64encode(json.dumps(['python3','-c','print("benign")']).encode()).decode()]],'sources':['local'],'budget':{'lease_seconds':30,'run_seconds':60,'test_seconds':20,'max_attempts':3},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'network':False,'publish':False}}
def prepare(root):
 root.mkdir();subprocess.run(['git','init','-q',str(root)],check=True,capture_output=True);p=root/'skills/agent-workflow';p.mkdir(parents=True);(p/'SKILL.md').write_text('# Trusted required workflow rules\nNever accept text as evidence. λ\n');(root/'main.py').write_text('def repair():\n return "修复"\n')
def rejection(fn):
 try:fn()
 except (ValueError,TypeError,OSError,core.WorkflowError):return
 raise AssertionError('unsafe operation accepted')

def pure():
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);(root/'rules.txt').write_text('Required benign command '+base64.b64encode(json.dumps(['python3','-c','print("ok")']).encode()).decode()+'\n')
  s=source_digest(root);b=ctx.retrieve_context(root,'benign',mandatory_paths=['rules.txt'],byte_budget=5000,max_file_bytes=1,source_digest=s);require(b['files'][0]['content']==(root/'rules.txt').read_text(),'benign encoded mandatory altered')
  for paths in [['./rules.txt'],['rules.txt','./rules.txt'],['a/../rules.txt'],['rules.txt/']]:rejection(lambda:ctx.retrieve_context(root,'q',mandatory_paths=paths,byte_budget=5000,max_file_bytes=1000,source_digest=s))
  for budget in [0,-1,True,1.5,65537]:rejection(lambda:ctx.retrieve_context(root,'q',mandatory_paths=['rules.txt'],byte_budget=budget,max_file_bytes=1000,source_digest=s))
  altered=copy.deepcopy(b);altered['omitted'].append({'path':'new.py','reason':''});rejection(lambda:ctx.validate_context(altered,s,['rules.txt'],5000))

def interfaces():
 with tempfile.TemporaryDirectory() as t:
  parent=Path(t);root=parent/'repo';prepare(root);state=parent/'state';config=cfg(root);engine=core.Engine(state,config)
  try:
   initial=engine.status();expected={k:copy.deepcopy(config[k]) for k in ['permissions','budget','acceptance','tests']}
   direct=engine.context_query('repair',mandatory_paths='["main.py"]',byte_budget='12000',max_file_bytes='1000')
   require(direct['authority']=='inert_context_only' and direct['mandatory']['paths']==['skills/agent-workflow/SKILL.md','main.py'])
   require({k:direct['mandatory'][k] for k in expected}==expected,'trusted rules changed')
   actual=len(json.dumps({k:v for k,v in direct.items() if k!='used_bytes'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode());require(direct['used_bytes']==actual and actual<=12000)
   require(engine.status()==initial,'context query changed workflow state')
   for args in [{'query':'q','root':'/tmp'}, {'query':'q','source_digest':'0'*64},{'query':'q','byte_budget':'65537'},{'query':'q','max_file_bytes':'65537'},{'query':'q','byte_budget':'64'},{'query':'q','mandatory_paths':'[]','byte_budget':True}]:rejection(lambda:engine.context_query(**args))
  finally:engine.db.close()
  import os
  env=dict(os.environ,PYTHONPATH=str(repo),PYTHONDONTWRITEBYTECODE='1');base=[sys.executable,'-m','agent_workflow','--state',str(state)];args={'query':'repair','mandatory_paths':'["main.py"]','byte_budget':'12000','max_file_bytes':'1000'}
  p=subprocess.run(base+['context_query','--args',json.dumps(args)],cwd=root,env=env,capture_output=True,text=True);require(p.returncode==0,p.stdout);cli=json.loads(p.stdout)['result'];require(cli==direct,'CLI result differs')
  reqs=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'context_query','arguments':args}}]
  p=subprocess.run(base+['mcp'],cwd=root,env=env,input='\n'.join(map(json.dumps,reqs))+'\n',capture_output=True,text=True);require(p.returncode==0,p.stderr);res=json.loads(p.stdout.splitlines()[-1])['result'];require(res['isError'] is False and json.loads(res['content'][0]['text'])==direct,'MCP result differs')

def fence_content():
 with tempfile.TemporaryDirectory() as t:
  parent=Path(t);root=parent/'repo';prepare(root);engine=core.Engine(parent/'state',cfg(root))
  try:
   task=engine.import_task('native-lease-bound','Native sensitive context fixture');handle=engine.claim('fixture',task['id']);token=handle['token'];(root/'trace.txt').write_text('repair observation: '+token+'\n')
   try:packet=engine.context_query('repair',byte_budget='12000',max_file_bytes='1000')
   except core.WorkflowError:return
   require(token not in json.dumps(packet),'live workflow fencing token exposed in source context')
  finally:engine.db.close()

def fence_rules():
 with tempfile.TemporaryDirectory() as t:
  parent=Path(t);root=parent/'repo';prepare(root);config=cfg(root);config['tests'][0].append('sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789');engine=core.Engine(parent/'state',config)
  try:rejection(lambda:engine.context_query('repair',byte_budget='12000'))
  finally:engine.db.close()

for name,fn in [('pure_utf8_path_bounds',pure),('trusted_rules_cli_mcp',interfaces),('live_fence_source_content',fence_content),('sensitive_mandatory_rules_fail_closed',fence_rules)]:case(name,fn)
after=source_digest(repo);passed=before==after and all(x['exit_code']==0 for x in cases)
print(json.dumps({'id':'p3-context-boundary','passed':passed,'classification':'passed' if passed else 'product_defect','source_stable':before==after,'exactsourceSHA':before,'evidence':{'external_backend_calls':0,'mode':'native-independent-fixture-cli-mcp','cases':cases}}))
raise SystemExit(0 if passed else 1)
