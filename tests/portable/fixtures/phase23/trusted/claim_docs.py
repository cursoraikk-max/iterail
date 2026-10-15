"""Frozen owner regression: documented claim shape against real CLI and stdio MCP."""
import json,os,re,subprocess,sys,tempfile
from pathlib import Path
repo=Path(sys.argv[1]).resolve();env=dict(os.environ,PYTHONPATH=str(repo),PYTHONDONTWRITEBYTECODE='1');checks=[]
def require(v,label):
 checks.append({'check':label,'passed':bool(v)})
 if not v:raise AssertionError(label)
try:
 readme=(repo/'README.md').read_text();skill=(repo/'skills/agent-workflow/SKILL.md').read_text()
 match=re.search(r'### Claim handle\s+.*?```json\s*(.*?)\s*```',readme,re.S)
 require(match is not None,'README provides claim response example')
 example=json.loads(match.group(1));require(set(example)=={'id','task_id','token'},'example names actual result fields')
 require(re.search(r'`id`.*?`run_id`',skill,re.S) is not None and 'Save returned `run_id` and `token`' not in skill,'Skill explicitly maps returned id to action run_id')
 require('before reading individual fields' in readme and 'mode 0600' in readme,'document private persistence before parsing')
 snippet=re.search(r'### Claim handle\s+.*?```python\s*(.*?)\s*```',readme,re.S);require(snippet is not None,'README supplies executable private persistence example')
 with tempfile.TemporaryDirectory(prefix='iterail-claim-docs-') as tmp:
  root=Path(tmp);project=root/'project';project.mkdir();subprocess.run(['git','init','-q',str(project)],check=True,capture_output=True,env=env);(project/'work.txt').write_text('fixture')
  config={'repo':str(project),'tests':[[sys.executable,'-c','pass']],'sources':['local'],'budget':{'lease_seconds':30,'run_seconds':60,'test_seconds':5,'max_attempts':2},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'network':False,'publish':False}};cfg=root/'config.json';cfg.write_text(json.dumps(config))
  def cli(state,action,args={},init=False):
   argv=[sys.executable,'-B','-m','agent_workflow','--state',str(state)]+(['--config',str(cfg)] if init else [])+[action,'--args',json.dumps(args)];p=subprocess.run(argv,env=env,cwd=project,capture_output=True,text=True,timeout=10);require(p.returncode==0,'CLI '+action+' exits zero');return json.loads(p.stdout)
  state=root/'cli';cli(state,'init',init=True);cli(state,'import',{'external_key':'doc-cli','title':'fixture'});raw=cli(state,'claim',{'worker':'doc-contract'})
  # Whole result is saved privately before deliberately exercising a mistaken mapper.
  handle=root/'claim.private.json';scope={'claim_response':raw,'private_path':str(handle)};exec(compile(snippet.group(1),'documented-claim-persistence','exec'),scope)
  try:raw['result']['run_id']
  except KeyError:pass
  saved=json.loads(handle.read_text())['result'];require(set(example)<=set(saved) and 'run_id' not in saved and saved['id'].startswith('run-'),'real CLI uses id rather than run_id');require(saved['token']==raw['result']['token'] and handle.stat().st_mode&0o777==0o600,'whole private handle survives mapper error')
  require(scope['args']=={'run_id':saved['id'],'token':saved['token']},'documented action args map id to run_id');cli(state,'progress',dict(scope['args'],note='documented persisted handle'));require(True,'documented mapper performs authorized mutation')
  state=root/'mcp';cli(state,'init',init=True);base=[sys.executable,'-B','-m','agent_workflow','--state',str(state),'mcp'];p=subprocess.Popen(base,env=env,cwd=project,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  def rpc(i,m,params={}):
   p.stdin.write(json.dumps({'jsonrpc':'2.0','id':i,'method':m,'params':params})+'\n');p.stdin.flush();return json.loads(p.stdout.readline())['result']
  def call(i,n,a):return json.loads(rpc(i,'tools/call',{'name':n,'arguments':a})['content'][0]['text'])
  try:
   rpc(1,'initialize',{'protocolVersion':'2025-06-18'});p.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n');p.stdin.flush();call(2,'import',{'external_key':'doc-mcp','title':'fixture'});actual=call(3,'claim',{'worker':'doc-contract'});require(set(example)<=set(actual) and 'run_id' not in actual and actual['id'].startswith('run-'),'real MCP matches documented CLI handle fields')
  finally:p.stdin.close();p.wait(timeout=10)
  require(p.returncode==0 and not p.stderr.read(),'MCP closes cleanly')
 report={'passed':True,'checks':checks,'external_model_calls':0,'tokens_emitted':False}
except Exception as e:report={'passed':False,'checks':checks,'error':type(e).__name__+': '+str(e),'external_model_calls':0,'tokens_emitted':False}
print(json.dumps(report));raise SystemExit(0 if report['passed'] else 1)
