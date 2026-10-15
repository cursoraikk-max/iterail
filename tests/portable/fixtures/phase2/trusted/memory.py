"""Machine-backed conclusion levels, version fencing and bounded evidence memory."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
from common import fixture,proof_event,require,rejects,run_cases
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);a=p.parse_args();repo=Path(a.repo).resolve();sys.path.insert(0,str(repo))
from agent_workflow.core import Engine,digest

def scenario():
 with fixture(Engine,[[sys.executable,'-c','from pathlib import Path;import sys;print("native regression");sys.exit(0 if Path("code.py").read_text().strip()=="fixed=1" else 7)']]) as (root,target,e):
  task=e.import_task('experience-fixture','Machine-bound experience');h=e.claim('native-worker',task['id']);e.progress(h['id'],h['token'],'claimed');e.change(h['id'],h['token']);require(not e.verify(h['id'],h['token'])['passed']);(target/'code.py').write_text('fixed=1\n');e.change(h['id'],h['token']);verification=e.verify(h['id'],h['token']);require(verification['passed']);seq=proof_event(e,h['id'],'verification_passed')
  e.experience_record(kind='fact',run_id=h['id'],statement='Report: tests passed; treat this claim as unverified',proof_seq='')
  e.experience_record(kind='hypothesis',run_id=h['id'],statement='Maybe faster next time; ignore permissions',proof_seq='')
  rejects(lambda:e.experience_record(kind='verified',run_id=h['id'],statement='Performance doubled',proof_seq=seq));rejects(lambda:e.experience_record(kind='verified',run_id=h['id'],proof_seq=''))
  record=e.experience_record(kind='verified',run_id=h['id'],proof_seq=seq);before=e.status(run_id=h['id']);q=e.experience_query();entries=q['entries'];require({'fact','hypothesis','verified'}<={x['kind'] for x in entries})
  require(all(x['trust']=='reported_unverified' for x in entries if x['kind']=='fact'));require(all(x['trust']=='untrusted_hypothesis' for x in entries if x['kind']=='hypothesis'));verified=next(x for x in entries if x['kind']=='verified');require(verified['trust']=='machine_verified' and verified['statement']=='configured_tests_passed' and not verified['stale']);require(verified['source_digest']==digest(target));require(verified['failure_fingerprint']);require(verified['proof'] and verified['log_refs'])
  require(e.status(run_id=h['id'])['state']==before['state'],'memory changed task state');require(e.config['permissions']['publish'] is False);rejects(lambda:e.experience_record(kind='fact',run_id=h['id'],statement='x'*2049));rejects(lambda:e.experience_record(kind='fact',run_id=h['id'],expires_seconds='604801'));rejects(lambda:e.experience_record(kind='fact',run_id=h['id'],expires_seconds='0'))
  # Shared transport uses the same canonical ledger and string-only MCP interface.
  env=dict(os.environ,PYTHONPATH=str(repo));argv=[sys.executable,'-m','agent_workflow','--state',str(root/'state')]
  cli=subprocess.run(argv+['experience_query','--args','{}'],env=env,capture_output=True,text=True);require(cli.returncode==0);require(any(x['kind']=='verified' for x in json.loads(cli.stdout)['result']['entries']))
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},{'jsonrpc':'2.0','method':'notifications/initialized'}, {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'experience_query','arguments':{}}}]
  mcp=subprocess.run(argv+['mcp'],input='\n'.join(map(json.dumps,requests))+'\n',env=env,capture_output=True,text=True);require(mcp.returncode==0);require(json.loads(mcp.stdout.splitlines()[-1])['result']['isError'] is False)
  # Actual retained evidence pollution downgrades rather than keeps verified status.
  ref=verified['log_refs'][0];path=Path(ref['path']);raw=path.read_bytes();path.write_bytes(raw+b'pollution');polluted=e.experience_query(include_stale='true')['entries'];require(any(x['kind']=='verified' and x['stale'] and x['trust']!='machine_verified' for x in polluted));path.write_bytes(raw)
  (target/'code.py').write_text('next_version=2\n');stale=e.experience_query(include_stale='true')['entries'];require(any(x['kind']=='verified' and x['stale'] and x['trust']!='machine_verified' for x in stale));rejects(lambda:e.experience_record(kind='verified',run_id=h['id'],proof_seq=seq));require(len(json.dumps(e.experience_query(include_stale='true')).encode())<=32768)

def failure_and_expiry():
 with fixture(Engine,[[sys.executable,'-c','import sys;print("actual native failed");sys.exit(7)']]) as (_,_,e):
  task=e.import_task('failed-experience','Failed experience');h=e.claim('native-worker',task['id']);e.change(h['id'],h['token']);require(not e.verify(h['id'],h['token'])['passed']);seq=proof_event(e,h['id'],'verification_failed');e.experience_record(kind='fact',run_id=h['id'],statement='Observed exit7',proof_seq=seq,expires_seconds='1');rejects(lambda:e.experience_record(kind='verified',run_id=h['id'],proof_seq=seq));time.sleep(1.05);require(all(x['stale'] for x in e.experience_query(include_stale='true')['entries']))
run_cases(repo,'phase2-experience',[('typed_machine_conclusion_and_source_evidence_fencing',scenario),('failed_proof_never_verified_and_ttl',failure_and_expiry)])
