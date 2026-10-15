"""Fixed Core/CLI/MCP UIUX interface; native artifacts, zero providers."""
import hashlib,json,os,struct,subprocess,sys,tempfile,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'phase5/trusted'))
import common
from common import run,require,rejects,command

def wrapper(m):
 with tempfile.TemporaryDirectory(prefix='uiux-interface-') as td:
  root=Path(td).resolve();repo=root/'repo';repo.mkdir();(repo/'index.html').write_text('<button>Home</button>');command(['git','init','-q',str(repo)]);art=root/'art';art.mkdir()
  p={'schema_version':1,'project':{'id':'interface-fixture','has_ui':True},'artifact_root':str(art),'standards':{'users':['reader'],'flows':[{'id':'home','steps':['Open home']}],'references':[{'id':'style','uri':'https://owner.example/style'}],'tokens':{'color':'#123456'},'viewports':[{'id':'small','width':32,'height':24}],'a11y':['keyboard']},'cadence':{'interval_seconds':60},'limits':{'artifact_bytes':4096,'evidence_items':8},'rubric':[{'id':'flow','minimum_score':3}],'approved_brand_changes':[]}
  owner=root/'owner.json';owner.write_text(json.dumps(p));desc={'path':str(owner),'sha256':hashlib.sha256(owner.read_bytes()).hexdigest()}
  base={'repo':str(repo),'tests':[[sys.executable,'-c','pass']],'sources':['local'],'budget':{'lease_seconds':20,'run_seconds':60,'test_seconds':20,'max_attempts':2},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'publish':False,'network':False}}
  from agent_workflow.cli import dispatch
  plain=m.Engine(root/'plain',base);require(dispatch(plain,'uiux_discover',{})['phase']=='not_applicable','absent optional policy not N/A');require(not plain.db.execute('SELECT * FROM tasks').fetchall(),'disabled UI imported tasks');rejects(lambda:dispatch(plain,'uiux_init',{'policy':json.dumps(p)}),'caller enabled absent policy')
  missing=dict(base,uiux_policy={'path':str(root/'missing.json'),'sha256':'0'*64});rejects(lambda:m.Engine(root/'missing-state',missing),'missing owner policy accepted')
  c=dict(base,uiux_policy=desc);engine=m.Engine(root/'state',c);dispatch(engine,'uiux_init',{'policy':json.dumps(p)});original_import=engine.import_task; interrupted=[True]
  def fault_import(*args,**kwargs):
   result=original_import(*args,**kwargs)
   if interrupted[0]:interrupted[0]=False;raise RuntimeError('native due importer interruption after durable import')
   return result
  engine.import_task=fault_import
  try:dispatch(engine,'uiux_discover',{})
  except RuntimeError:pass
  engine.db.close();engine=m.Engine(root/'state');r=dispatch(engine,'uiux_discover',{});due=engine.db.execute('SELECT * FROM tasks').fetchall();require(len(due)==1,'exactly one durable due audit task missing after restart');duebody=json.loads(due[0]['body']);require(duebody['commands']==[] and duebody['permissions']=={},'due audit expanded commands/permissions');require(r['source_sha256']==m.digest(repo),'wrapper did not compute actual repository hash');rejects(lambda:dispatch(engine,'uiux_discover',{'source_root':str(root)}),'client source override accepted')
  env=dict(os.environ,PYTHONPATH=str(common.REPO),PYTHONDONTWRITEBYTECODE='1');prefix=[sys.executable,'-m','agent_workflow.cli','--state',str(root/'state')]
  cli=subprocess.run(prefix+['uiux_discover'],env=env,capture_output=True,text=True,timeout=10);require(cli.returncode==0 and json.loads(cli.stdout)['result']['run_id']==r['run_id'],'CLI not shared Store')
  bad=subprocess.run(prefix+['uiux_discover','--args',json.dumps({'source_sha256':'0'*64})],env=env,capture_output=True,text=True,timeout=10);require(bad.returncode!=0 and json.loads(bad.stdout)['ok'] is False,'CLI source spoof accepted')
  messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'uiux_discover','arguments':{}}},{'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'uiux_discover','arguments':{'source_root':str(root)}}}]
  rpc=subprocess.run(prefix+['mcp'],input=''.join(json.dumps(v)+'\n' for v in messages),env=env,capture_output=True,text=True,timeout=10);require(rpc.returncode==0,'MCP process failure');responses={x['id']:x for x in map(json.loads,rpc.stdout.splitlines())};names={x['name'] for x in responses[2]['result']['tools']};require({'uiux_init','uiux_discover','uiux_evidence','uiux_review','uiux_query'}<=names,'UIUX MCP actions missing');require(json.loads(responses[3]['result']['content'][0]['text'])['run_id']==r['run_id'] and (responses[4]['error']['code']==-32602 and 'result' not in responses[4]),'MCP Store mismatch/source spoof')
  def chunk(k,b):return struct.pack('!I',len(b))+k+b+struct.pack('!I',zlib.crc32(k+b)&0xffffffff)
  image=art/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',32,24,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\x00'+b'\x00'*96)*24))+chunk(b'IEND',b''));inter=art/'interaction.json';inter.write_text(json.dumps({'flow_id':'home','steps':[{'step':'Open home','selector':'button','outcome':'passed'}]}))
  for ident,stage,path in [('screen','screenshot',image),('interaction','interaction',inter)]:
   packet={'id':ident,'kind':'fixture','source_sha256':r['source_sha256'],'actor':'fixture-author','artifact':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'viewport_id':'small','flow_id':'home'}};out=dispatch(engine,'uiux_evidence',{'run_id':r['run_id'],'stage':stage,'evidence':json.dumps(packet)});require(out['production_visual_verified'] is False,'wrapper falsely verified fixture')
  args={'run_id':r['run_id'],'reviewer':'independent-fixture-reviewer','rubric':json.dumps([{'id':'flow','score':1,'reason':'Investigate button feedback in supplied interaction','evidence_ids':['screen','interaction']}]),'verdict':'needs_improvement','rationale':'Native submitted evidence supports investigation only','findings':json.dumps([{'id':'minor','title':'Investigate feedback','description':'Inspect owner home feedback','evidence_ids':['interaction'],'major_brand_change':False,'brand_change_id':None}])}
  dispatch(engine,'uiux_review',args);dispatch(engine,'uiux_review',args);rows=engine.db.execute('SELECT * FROM tasks').fetchall();require(len(rows)==2 and all(x['state']=='queued' for x in rows),'inert task not imported/deduped');body=json.loads(rows[0]['body']);require(body['commands']==[] and body['permissions']=={},'UI import expanded authority');require(engine.config==m.validate_config(c),'review changed Core policy')
  args['findings']=json.dumps([{'id':'major','title':'Rebrand','description':'Owner approval required','evidence_ids':['screen'],'major_brand_change':True,'brand_change_id':'unapproved'}]);args['run_id']=dispatch(engine,'uiux_discover',{})['run_id']
  try:dispatch(engine,'uiux_review',args)
  except (ValueError,RuntimeError):pass
  require(len(engine.db.execute('SELECT * FROM tasks').fetchall())==2,'major brand task imported')
  (repo/'index.html').write_text('<button>Changed</button>');rejects(lambda:dispatch(engine,'uiux_review',args),'wrapper reviewed stale source');require(r['run_id'] in dispatch(engine,'uiux_query',{})['stale_run_ids'],'wrapper query failed source freshness')
  owner.write_text(json.dumps(dict(p,approved_brand_changes=['unapproved'])));rejects(lambda:dispatch(engine,'uiux_query',{}),'tampered owner descriptor accepted')

run('p5-uiux-interface-v2','core',[('shared_Core_CLI_MCP_owner_source_and_inert_tasks',wrapper)])
