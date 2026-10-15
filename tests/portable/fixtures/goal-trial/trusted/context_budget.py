"""Frozen discovery-driven context budget diagnostic regression; no models."""
import json,subprocess,sys,os,tempfile,sqlite3,hashlib
from pathlib import Path
repo=Path(sys.argv[1]).resolve();sys.dont_write_bytecode=True;sys.path.insert(0,str(repo));from agent_workflow.core import Engine,digest
root=Path(__file__).resolve().parents[2];owner=json.loads((root/'goal-trial/owner/core.json').read_text());checks=[]
def check(v,n):checks.append({'check':n,'passed':bool(v)})
with tempfile.TemporaryDirectory(prefix='iterail-context-diagnostic-') as tmp:
 state=Path(tmp)/'state';cfg=dict(owner,repo=str(repo));e=Engine(state,cfg);e.db.close();env=dict(os.environ,PYTHONPATH=str(repo),PYTHONDONTWRITEBYTECODE='1');prefix=[sys.executable,'-B','-m','agent_workflow','--state',str(state)]
 def cli(args):
  p=subprocess.run(prefix+['context_query','--args',json.dumps(args)],env=env,capture_output=True,text=True,timeout=20);return p,json.loads(p.stdout)
 def db():
  c=sqlite3.connect(state/'tasks.sqlite');d='\n'.join(c.iterdump());c.close();return hashlib.sha256(d.encode()).hexdigest()
 before=db();source=digest(repo);p,r=cli({'query':'README'});message=r.get('error','');check(p.returncode==1 and not r.get('ok') and 'byte_budget' in message and ('metadata' in message.lower() or 'frozen' in message.lower()) and '65536' in message,'default rejection identifies total packet budget/reserved metadata and legal retry bound')
 args={'query':'README','byte_budget':'65536'};p,success=cli(args);value=success.get('result',{});check(p.returncode==0 and success.get('ok') and value['used_bytes']<=65536,'only total byte_budget increased succeeds');skill=next(x for x in value['context']['files'] if x['path']=='skills/agent-workflow/SKILL.md');check(skill['content']==(repo/'skills/agent-workflow/SKILL.md').read_text() and skill['mandatory'],'mandatory Skill remains complete')
 def rpc(i,args):return {'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':'context_query','arguments':args}}
 messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},{'jsonrpc':'2.0','method':'notifications/initialized'},rpc(2,{'query':'README'}),rpc(3,args)];p=subprocess.run(prefix+['mcp'],env=env,input='\n'.join(json.dumps(x) for x in messages)+'\n',capture_output=True,text=True,timeout=20);rows=[json.loads(x) for x in p.stdout.splitlines()];check(rows[1]['result']['isError'] and rows[1]['result']['content'][0]['text']==message,'MCP same business error diagnostic');check(not rows[2]['result']['isError'] and json.loads(rows[2]['result']['content'][0]['text'])==value,'CLI/MCP successful packet equality');p,r=cli({'query':'README','byte_budget':'65537'});check(p.returncode==1 and not r.get('ok'),'65536 maximum remains enforced');check(before==db() and digest(repo)==source,'read-only state/source unchanged')
print(json.dumps({'passed':all(x['passed'] for x in checks),'checks':checks,'source_sha256':source,'models':0}));raise SystemExit(0 if all(x['passed'] for x in checks) else 1)
