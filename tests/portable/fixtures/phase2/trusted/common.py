import contextlib,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path

def canonical(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def require(v,message='assertion failed'):
 if not v:raise AssertionError(message)
def rejects(fn):
 try:fn()
 except (ValueError,TypeError,OSError):return
 raise AssertionError('unsafe request accepted')
def config(repo,tests=None):return {'repo':str(repo),'tests':tests or [[sys.executable,'-c','print("native machine")']],'sources':['local'],'budget':{'lease_seconds':30,'run_seconds':60,'test_seconds':20,'max_attempts':3},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'publish':False,'network':False}}
@contextlib.contextmanager
def fixture(Engine,tests=None,extra=None):
 with tempfile.TemporaryDirectory(prefix='phase2-trusted-') as t:
  root=Path(t);repo=root/'repo';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True,capture_output=True);(repo/'code.py').write_text('initial=1\n');c=config(repo,tests);c.update(extra or {});e=Engine(root/'state',c)
  try:yield root,repo,e
  finally:e.db.close()
def proof_event(e,run,kind):
 rows=e.db.execute('SELECT seq,data FROM events WHERE run_id=? AND kind=? ORDER BY seq DESC',(run,kind)).fetchall();require(bool(rows),'machine proof missing');return str(rows[0][0])
def run_cases(repo,ident,functions):
 from agent_workflow.core import digest
 before=digest(repo);cases=[]
 for name,fn in functions:
  try:fn();cases.append({'case':name,'exit_code':0})
  except Exception as ex:cases.append({'case':name,'exit_code':1,'error':type(ex).__name__+': '+str(ex)})
 after=digest(repo);passed=all(x['exit_code']==0 for x in cases) and before==after
 print(json.dumps({'id':ident,'evidence_mode':'native-fixture-contract','external_backend_calls':0,'source_digest':before,'source_stable':before==after,'cases':cases,'passed':passed}));raise SystemExit(0 if passed else 1)
