"""Bounded portable context cannot transfer fencing or manufacture acceptance."""
import argparse,json,os,subprocess,sys
from pathlib import Path
from common import fixture,require,rejects,run_cases
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);a=p.parse_args();repo=Path(a.repo).resolve();sys.path.insert(0,str(repo))
from agent_workflow.core import Engine,digest

def scenario():
 command=[sys.executable,'-c','import sys;print("NATIVE_HANDOFF_FAILURE");sys.exit(7)']
 with fixture(Engine,[command]) as (root,target,e):
  task=e.import_task('handoff-fixture','Budgeted handoff','Untrusted long context '+'x'*50000);h=e.claim('sender',task['id']);e.progress(h['id'],h['token'],'untrusted note private lease token '+h['token']);e.change(h['id'],h['token']);require(not e.verify(h['id'],h['token'])['passed']);before=e.status(run_id=h['id'])['state']
  rejects(lambda:e.handoff_build(h['id'],'wrong-token',budget_bytes='16384'));rejects(lambda:e.handoff_build(h['id'],h['token'],budget_bytes='64'))
  bundle=e.handoff_build(h['id'],h['token'],budget_bytes='16384');serialized=json.dumps(bundle,ensure_ascii=False).encode();require(len(serialized)<=16384,'bundle exceeded requested budget');require(h['token'].encode() not in serialized,'lease token leaked');require(bundle['ownership_transferred'] is False)
  mandatory=bundle['mandatory'];require(mandatory['permissions']==e.config['permissions'] and mandatory['budget']==e.config['budget'] and mandatory['acceptance']==e.config['acceptance'] and mandatory['tests']==e.config['tests']);require(bundle['source_digest']==digest(target));require(bundle['task_id']==task['id'] and bundle['run_id']==h['id']);require(bundle['failure']['event_seq'] and bundle['failure']['fingerprint'] and bundle['failure']['event_sha256']);require(any(x['argv']==command and x['exit_code']==7 for x in bundle['failure']['results']));require(bundle['failure']['log_refs']);require(bundle['omitted'],'oversized optional context missing omission marker')
  require(e.status(run_id=h['id'])['state']==before,'handoff changed ownership/state');rejects(lambda:e.claim('receiver',task['id']))
  read=e.handoff_read(bundle['handoff_id']);require(read['ownership_transferred'] is False and read['requires_fresh_claim'] is True and read['requires_fresh_verify'] is True and not read['stale']);require(h['token'] not in json.dumps(read))
  env=dict(os.environ,PYTHONPATH=str(repo));argv=[sys.executable,'-m','agent_workflow','--state',str(root/'state')];cli=subprocess.run(argv+['handoff_read','--args',json.dumps({'handoff_id':bundle['handoff_id']})],env=env,capture_output=True,text=True);require(cli.returncode==0 and json.loads(cli.stdout)['result']['handoff_id']==bundle['handoff_id'])
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'handoff_read','arguments':{'handoff_id':bundle['handoff_id']}}}];mcp=subprocess.run(argv+['mcp'],input='\n'.join(map(json.dumps,requests))+'\n',env=env,capture_output=True,text=True);require(mcp.returncode==0);require(json.loads(mcp.stdout.splitlines()[-1])['result']['isError'] is False)
  ref=bundle['failure']['log_refs'][0];path=Path(ref['path']);raw=path.read_bytes();path.write_bytes(raw+b'pollution');require(e.handoff_read(bundle['handoff_id'],include_stale='true')['stale']);path.write_bytes(raw)
  (target/'code.py').write_text('changed_after_handoff=2\n');stale=e.handoff_read(bundle['handoff_id'],include_stale='true');require(stale['stale'] and stale['requires_fresh_verify']);require(e.config['permissions']['publish'] is False)
run_cases(repo,'phase2-handoff',[('mandatory_budget_and_fresh_fencing_context',scenario)])
