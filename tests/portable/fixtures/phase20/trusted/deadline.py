import ast,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];TRUSTED=ROOT/'phase4/trusted'
sys.dont_write_bytecode=True
PINS={'target.py': 'a88410bede2efdb6e93ae2622ec2848c290b7446ba1c1ead26c91dba2345d229', 'common.py': 'c0f604e908510453fcc831485cc0629fce88a3ce9b5a84c0631fd57d4fdb4fb0', 'native_backend.py': '5c8534bf4a190d20f32616359386f6de4b38c7f1f975ef392d8282d449439ce5'}
ASSERTIONS_AST_SHA='dd693dfc07f0b7d3e9e727e1372645ee026e3df7646c30195e4d0b8ecaf73f6e'
ASSERTIONS_COUNT=12
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def pins():
 for name,value in PINS.items():
  if sha(TRUSTED/name)!=value:raise AssertionError('original trusted fixture changed')
 tree=ast.parse((TRUSTED/'target.py').read_text())
 fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='native_target')
 if hashlib.sha256(ast.dump(fn,include_attributes=False).encode()).hexdigest()!=ASSERTIONS_AST_SHA:raise AssertionError('original assertions AST changed')
 if sum(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='require' for n in ast.walk(fn))!=ASSERTIONS_COUNT:raise AssertionError('original assertion count changed')
import tempfile
from unittest.mock import patch
SOURCE=Path(sys.argv[1]).resolve();pins();sys.path.insert(0,str(SOURCE));sys.path.insert(0,str(TRUSTED));import common
from agent_workflow import diagnostics,evolution
initial=common.source_digest(SOURCE);account={};cycle=evolution.Supervisor.cycle
def observed_cycle(self):
 result=cycle(self);meta=self.status()['meta'];account['calls']=meta['calls'];account['deadline']=meta['deadline'];account['started']=meta['started']
 return result
try:
 with tempfile.TemporaryDirectory(prefix='iterail-fixed-deadline-proof-') as output:
  with patch.object(evolution.Supervisor,'cycle',observed_cycle):
   result=diagnostics.run_native_target(TRUSTED,SOURCE,output,delay_seconds=3)
  assert result['passed'] is False and result['cleanup_complete'] is True,'deadline callback unexpectedly passed or leaked raw fixture'
  assert result['call_cap_seconds']==2 and result['injected_delay_seconds']==3,'fixed deadline profile changed'
  assert result['subject_source_unchanged'] is True and result['trusted_unchanged'] is True,'deadline source changed'
  artifact=Path(result['artifact']);proof=json.loads(artifact.read_bytes());assert sha(artifact)==result['artifact_sha256'] and proof['complete'] is True,'deadline evidence incomplete'
  workers=[r for r in proof['runner_calls'] if r['kind']=='backend:worker'];assert len(workers)==1,'startup did not launch exactly one worker'
  worker=workers[0];assert worker['phase']=='finished' and worker['exit_code']==124 and worker['returncode']==-9 and worker['reason']=='timeout','actual deadline did not stop worker'
  # The production projector admits this boolean only with exact call/PID/guardian/start/log binding and group_termination_confirmed true.
  assert worker['pid_identity_verified'] is True,'worker identity/group cleanup unverified'
  marker=b'NATIVE_FIXTURE_INJECTED_DELAY\n'
  assert worker['log_bytes']==len(marker)==30 and worker['log_sha256']==hashlib.sha256(marker).hexdigest() and worker['log_event_types']==[],'unexpected protocol or startup marker'
  assert 0 < worker['total_deadline']-worker['started'] <= 2.1 and 0 < worker['duration'] <= 2.3,'bounded startup deadline was not enforced'
  assert proof['cycles']==[{'phase':'failed','failure_class':'backend_failure'}],'unexpected startup-failure lifecycle'
  assert not proof['core_runs'] and not proof['core_events'],'startup timeout claimed Core'
  assert not any(r['kind']=='backend:reviewer' for r in proof['runner_calls']),'failed worker launched reviewer'
  assert not any(g['cas_events'] for g in proof['git']),'failed worker integrated CAS'
  targets=[g for g in proof['git'] if g['initial_head'] is not None]
  assert len(targets)==1 and all(g['initial_head']==g['final_head'] and g['status_entries']==0 for g in targets),'bound target HEAD/status changed or ambiguous'
  assert account['calls']==2,'timeout refunded reserved pair'
  report={'id':'iterail-native-startup-deadline','passed':True,'classification':'passed','exactsourceSHA':initial,'source_stable':initial==common.source_digest(SOURCE),'evidence_kind':'native_fixture','external_provider_calls':0,'successful_model_turns':0,'original_assertions_digest':ASSERTIONS_AST_SHA,'original_trusted_sha256':PINS,'call_cap_seconds':2,'forced_startup_delay_seconds':3,'reserved_calls':account['calls'],'actual_worker_launches':1,'reviewer_launches':0,'Core_claims':0,'CAS_events':0,'raw_cleanup_complete':True,'worker_pid_and_group_cleanup_verified':True,'sanitized_artifact_sha256':result['artifact_sha256'],'sanitized_evidence':proof}
  assert report['source_stable'] is True,'source changed during deadline fixture';pins();assert len(json.dumps(report).encode())<=32768,'deadline proof output exceeded bound'
  print(json.dumps(report,sort_keys=True))
except Exception as error:
 print(json.dumps({'id':'iterail-native-startup-deadline','passed':False,'classification':'product_defect','exactsourceSHA':initial,'source_stable':initial==common.source_digest(SOURCE),'external_provider_calls':0,'error_sha256':hashlib.sha256(str(error).encode()).hexdigest()}));raise
