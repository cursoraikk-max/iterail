"""Independent fixed native acceptance helpers; no external provider calls."""
import hashlib, importlib, json, os, shutil, subprocess, sys, tempfile, traceback
from pathlib import Path
sys.dont_write_bytecode=True
REPO=None

def encoded(value):
 return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def canonical(value): return hashlib.sha256(encoded(value)).hexdigest()
def require(value,message='contract violated'):
 if not value:raise AssertionError(message)
def rejects(fn,message='invalid input accepted'):
 try:fn()
 except (ValueError,TypeError,OSError,RuntimeError):return
 raise AssertionError(message)
def source_digest(root):
 root=Path(root).resolve();h=hashlib.sha256()
 for path in sorted(root.rglob('*')):
  rel=path.relative_to(root)
  if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or path.suffix=='.pyc':continue
  if path.is_symlink():raise ValueError('source symlink')
  if path.is_file():h.update(str(rel).encode()+b'\0'+path.read_bytes()+b'\0')
 return h.hexdigest()
def command(argv,cwd=None,timeout=30):
 p=subprocess.run(argv,cwd=cwd,capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise AssertionError('command exit '+str(p.returncode)+': '+p.stderr[-2000:]+p.stdout[-2000:])
 return p.stdout.strip()
def git(root,*args):return command(['git','-C',str(root),*args])
def commit(root,message='native fixture'):
 git(root,'add','-A');git(root,'-c','user.name=Native fixture','-c','user.email=fixture@invalid','-c','commit.gpgsign=false','commit','-qm',message)
def fixture(owner_mod,*,max_calls=4,wall=600):
 tmp=tempfile.TemporaryDirectory(prefix='phase4-native-');root=Path(tmp.name);repo=root/'repo'
 shutil.copytree(REPO,repo,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','.workflow'))
 command(['git','init','-q',str(repo)]);commit(repo)
 check=root/'check.py';check.write_text('import json,sys\nprint(json.dumps({"id":"fixture-check","passed":True,"classification":"passed","evidence":[]}))\n')
 backend=root/'fake-backend.py';backend.write_text('#!'+sys.executable+'\nraise SystemExit("NATIVE_FIXTURE_BACKEND_MUST_NOT_BE_DISPATCHED")\n');backend.chmod(0o700)
 c={'schema_version':1,'project':{'id':'native-controller','kind':'controller'},'backend_adapter':{'kind':'command','argv':[str(backend),'{kind}','{repo}','{slot}','{verdict}'],'protocol':'exec-v1'},'repo':str(repo),'backend':str(backend),'checks':[{'id':'fixture-check','argv':[sys.executable,str(check),'{repo}'],'repair':'Native fixture no product defect','finding_contract':'strict-v1'}],
    'tests':[[sys.executable,str(check),'{repo}']], 'health':[[sys.executable,str(check),'{repo}']],
    'allowlist':['agent_workflow/core.py','agent_workflow/runner.py','docs/*.md'],
    'budget':{'wall_seconds':wall,'max_calls':max_calls,'call_seconds':2,'log_bytes':8192,'max_attempts':2},
    'poll_seconds':1,'self_update':['agent_workflow/core.py','agent_workflow/runner.py'],
    'idle':{'initial_seconds':0.1,'max_seconds':0.4},'breakers':{'same_class_failures':2,'no_gain_cycles':2},
    'execution_mode':'native_fixture','provider_authorized':False,'strategy_catalog':None,'strategy_catalog_sha256':None}
 return tmp,root,c

def close(obj):
 if hasattr(obj,'db'):obj.db.close()
def meta(obj):
 value=obj.status();return value.get('meta',value)
def run(ident,module,cases):
 global REPO
 REPO=Path(sys.argv[1]).resolve();before=source_digest(REPO);results=[];code=0
 try:
  sys.path.insert(0,str(REPO));mod=importlib.import_module('agent_workflow.'+module)
  for title,fn in cases:
   try:fn(mod);results.append({'case':title,'exit_code':0})
   except BaseException as error:
    if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
    results.append({'case':title,'exit_code':1,'error':type(error).__name__+': '+str(error),'traceback':traceback.format_exc()[-6000:]});code=1
 except (ImportError,AttributeError) as error:results.append({'case':'required_feature','exit_code':1,'error':str(error)});code=1
 after=source_digest(REPO)
 if before!=after:code=2;results.append({'case':'source_stability','exit_code':2})
 print(json.dumps({'id':ident,'passed':code==0,'classification':'passed' if code==0 else ('product_defect' if code==1 else 'infrastructure_error'),
  'source_stable':before==after,'exactsourceSHA':before,'evidence_kind':'native_fixture','external_provider_calls':0,'evidence':results},sort_keys=True))
 raise SystemExit(code)
