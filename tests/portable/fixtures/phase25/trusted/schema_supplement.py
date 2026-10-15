import json,subprocess,sys,os,tempfile
from pathlib import Path
repo=Path(sys.argv[1]).resolve();sys.dont_write_bytecode=True;sys.path.insert(0,str(repo));from agent_workflow.core import Engine,digest
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);project=root/'project';project.mkdir();subprocess.run(['git','init','-q',str(project)],check=True,capture_output=True)
 cfg={'repo':str(project),'tests':[[sys.executable,'-c','pass']],'sources':['local'],'budget':{'lease_seconds':30,'run_seconds':60,'test_seconds':5,'max_attempts':2},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'network':False,'publish':False}}
 e=Engine(root/'state',cfg);e.db.close();checks=[]
 def rpc(i,m,p):return {'jsonrpc':'2.0','id':i,'method':m,'params':p}
 init=rpc(1,'initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'supplement','version':'1'}})
 def wire(msgs):
  p=subprocess.run([sys.executable,'-B','-m','agent_workflow','--state',str(root/'state'),'mcp'],env=dict(os.environ,PYTHONPATH=str(repo)),input='\n'.join(json.dumps(m) for m in msgs)+'\n',capture_output=True,text=True,timeout=10);return [json.loads(s) for s in p.stdout.splitlines()]
 for params in ([],{'_meta':[]}):
  rows=wire([init,{'jsonrpc':'2.0','method':'notifications/initialized','params':params},rpc(2,'tools/list',{})]);checks.append({'check':'malformed notification cannot enable ready '+str(params),'passed':'error' in rows[-1] and len(rows)==2})
 for params in ({'cursor':42},{'_meta':[]}):
  rows=wire([init,{'jsonrpc':'2.0','method':'notifications/initialized'},rpc(2,'tools/list',params)]);checks.append({'check':'malformed provided list field '+str(params),'passed':rows[-1].get('error',{}).get('code')==-32602 and 'result' not in rows[-1]})
 print(json.dumps({'passed':all(c['passed'] for c in checks),'checks':checks,'source_sha256':digest(repo)}));sys.exit(0 if all(c['passed'] for c in checks) else 1)
