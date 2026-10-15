"""Owner frozen wire regression: declared stdio/versions/tools only, no models."""
import json,os,subprocess,sys,tempfile,time,sqlite3,hashlib
from pathlib import Path
repo=Path(sys.argv[1]).resolve();sys.dont_write_bytecode=True;sys.path.insert(0,str(repo));from agent_workflow.core import Engine,digest
checks=[];versions=['2024-11-05','2025-03-26','2025-06-18'];canary='UNTRUSTED_SCHEMA_VALUE_DO_NOT_ECHO'
def check(x,label):checks.append({'check':label,'passed':bool(x)})
def cfg(project):return {'repo':str(project),'tests':[[sys.executable,'-c',"print('fixture-test-log');raise SystemExit(1)"]],'sources':['local'],'budget':{'lease_seconds':30,'run_seconds':60,'test_seconds':5,'max_attempts':2},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'network':False,'publish':False}}
def rpc(i,m,p=None):
 d={'jsonrpc':'2.0','id':i,'method':m}
 if p is not None:d['params']=p
 return d
def init(v):return rpc(1,'initialize',{'protocolVersion':v,'capabilities':{},'clientInfo':{'name':'fixed-wire-check','version':'1'}})
notify={'jsonrpc':'2.0','method':'notifications/initialized'}
def code(r,n):return isinstance(r,dict) and r.get('error',{}).get('code')==n and 'result' not in r
def facts(state):
 c=sqlite3.connect('file:'+str(state/'tasks.sqlite')+'?mode=ro',uri=True);r=[c.execute('select count(*) from tasks').fetchone()[0],c.execute('select count(*) from runs').fetchone()[0],c.execute('select count(*) from events').fetchone()[0]];c.close();return r
try:
 with tempfile.TemporaryDirectory(prefix='iterail-mcp-wire-') as tmp:
  root=Path(tmp);project=root/'project';project.mkdir();subprocess.run(['git','init','-q',str(project)],check=True,capture_output=True);(project/'work.txt').write_text('fixture');state=root/'state';e=Engine(state,cfg(project));e.db.close();env=dict(os.environ,PYTHONPATH=str(repo),PYTHONDONTWRITEBYTECODE='1');base=[sys.executable,'-B','-m','agent_workflow','--state',str(state),'mcp']
  def wire(messages):
   text='\n'.join(x if isinstance(x,str) else json.dumps(x) for x in messages)+'\n';p=subprocess.run(base,env=env,input=text,capture_output=True,text=True,timeout=15);return p,[json.loads(x) for x in p.stdout.splitlines()]
  for v in versions+['2099-01-01']:
   p,r=wire([init(v),notify,rpc(2,'tools/list'),rpc(3,'tools/call',{'name':'capabilities','arguments':{},'_meta':{'progressToken':'fixed'}})]);check(p.returncode==0 and not p.stderr and r[0]['result']['protocolVersion']==(v if v in versions else versions[-1]),'version negotiation '+v);tools=r[1]['result']['tools'];check(len(tools)==27 and r[0]['result']['capabilities']=={'tools':{}} and all(t['inputSchema']['type']=='object' and t['inputSchema']['additionalProperties'] is False for t in tools),'truthful tools/schema '+v);check(r[2]['result']['isError'] is False,'meta accepted '+v)
  requests=[init(versions[-1]),notify,rpc(2,'unknown'),rpc(3,'tools/call',{'name':'unknown','arguments':{}}),rpc(4,'tools/call',{'name':'capabilities','arguments':{'unknown':canary}}),rpc(5,'tools/call',{'name':'claim','arguments':{'worker':True}}),rpc(6,'tools/call',{'name':42}),rpc(7,'tools/list',[]),rpc(8,'tools/call',{'name':'next_step','arguments':{'run_id':'run-missing'}}),rpc(9,'initialize',{'protocolVersion':versions[0]}),rpc(10,'tools/list'),rpc(None,'ping'),rpc(True,'ping'),'{',{'jsonrpc':'2.0','method':'notifications/cancelled','params':{'requestId':'unknown'}},{'jsonrpc':'2.0','method':'tools/call','params':{'name':'import','arguments':{'external_key':'notification','title':'no mutation'}}},rpc(11,'ping')]
  before=facts(state);p,r=wire(requests);by={x.get('id'):x for x in r if isinstance(x,dict)}
  check(code(by[2],-32601),'unknown method is method-not-found');check(code(by[3],-32602),'unknown tool is invalid-params');check(all(code(by[i],-32602) for i in [4,5,6,7]),'schema/name/params invalid become protocol errors');check(by[8].get('result',{}).get('isError') is True and 'error' not in by[8],'business error is tool result');check('error' in by[9] and len(by[10]['result']['tools'])==27,'reinitialize rejects without changing ready state');check(sum(code(x,-32600) and x.get('id') is None for x in r)==2 and not any('result' in x and (x.get('id') is None or x.get('id') is True) for x in r),'null and boolean IDs each rejected without results');check(any(code(x,-32700) for x in r),'parse error code');
  q,nf=wire(['{"jsonrpc":"2.0","id":15,"method":"ping","params":{"number":NaN}}']);check(code(nf[0],-32700),'nonfinite JSON constant is parse error');check(len(r)==14 and by[11].get('result')=={},'notifications/cancel never replied');check(canary not in p.stdout and before==facts(state),'schema failures/no-request notifications do not leak or mutate')
  p,r=wire([rpc(1,'initialize',{'protocolVersion':123})]);check(code(r[0],-32602),'malformed provided version rejected')
  p,r=wire([rpc(1,'initialize',{'protocolVersion':versions[-1],'capabilities':[]})]);check(code(r[0],-32602),'malformed provided capabilities rejected')
  for v in versions[:2]:
   frame=[rpc(2,'ping'),{'jsonrpc':'2.0','method':'notifications/cancelled','params':{'requestId':'done'}},rpc(3,'tools/call',{'name':'capabilities','arguments':{}})];p,r=wire([init(v),notify,frame]);check(len(r)==2 and isinstance(r[1],list) and {x['id'] for x in r[1]}=={2,3},'legacy batch receive '+v)
  p,r=wire([init(versions[-1]),notify,[rpc(2,'ping')]]);check(code(r[-1],-32600),'June rejects unsupported batch frame')
  missing=root/'not-initialized';p=subprocess.run([sys.executable,'-B','-m','agent_workflow','--state',str(missing),'mcp'],env=env,input='',capture_output=True,text=True,timeout=5);check(p.returncode!=0 and not p.stdout and bool(p.stderr),'startup failure stdout remains protocol-only')
  e=Engine(state);t=e.import_task('verify-frame','fixture');h=e.claim('fixture-worker',t['id']);e.change(h['id'],h['token']);e.db.close();p,r=wire([init(versions[-1]),notify,rpc(2,'tools/call',{'name':'verify','arguments':{'run_id':h['id'],'token':h['token']}}),{'jsonrpc':'2.0','method':'notifications/cancelled','params':{'requestId':2}},rpc(3,'ping')]);verify=r[1].get('result',{});data=json.loads(verify['content'][0]['text']);check(p.returncode==0 and len(r)==3 and data['passed'] is False and 'fixture-test-log' in data['evidence']['results'][0]['stdout'] and not p.stderr,'serialized verification/late cancellation/EOF keep framed logs and failed proof');c=sqlite3.connect(state/'tasks.sqlite');check(c.execute('select state from runs where id=?',(h['id'],)).fetchone()[0]=='changed','failed verification never accepted on EOF');c.close()
 report={'passed':all(x['passed'] for x in checks),'checks':checks,'source_sha256':digest(repo),'scope':'Only declared stdio/three versions/tools; cancellation noncancellable serialized requests allowed; no optional new extensions','external_model_calls':0}
except Exception as e:report={'passed':False,'checks':checks,'fixture_error':type(e).__name__+': '+str(e),'source_sha256':digest(repo),'external_model_calls':0}
print(json.dumps(report));raise SystemExit(0 if report['passed'] else 1)
