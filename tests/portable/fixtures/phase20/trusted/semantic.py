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
import contextlib,io,runpy
SOURCE=Path(sys.argv[1]).resolve();pins();sys.path.insert(0,str(TRUSTED));import common
old=common.fixture;calls=[]
def fixture(*args,**kwargs):
 tmp,root,config=old(*args,**kwargs)
 before=json.loads(json.dumps(config));assert config['budget']['call_seconds']==2
 config['budget']['call_seconds']=10
 expected=json.loads(json.dumps(before));expected['budget']['call_seconds']=10
 assert config==expected,'unexpected fixture policy change'
 calls.append({'original_fixture_call_seconds':2,'semantic_fixture_call_seconds':10})
 return tmp,root,config
common.fixture=fixture;argv=sys.argv;sys.argv=[str(TRUSTED/'target.py'),str(SOURCE)];capture=io.StringIO();code=1
try:
 with contextlib.redirect_stdout(capture):
  try:runpy.run_path(str(TRUSTED/'target.py'),run_name='__main__')
  except SystemExit as result:code=result.code
finally:common.fixture=old;sys.argv=argv
record=json.loads(capture.getvalue());pins()
assert len(calls)==1,'original semantic fixture count changed'
record.update(id='iterail-native-CAS-semantics',original_assertions_digest=ASSERTIONS_AST_SHA,original_assertion_count=ASSERTIONS_COUNT,original_trusted_sha256=PINS,fixture_budget_revision=calls[0],production_budget_changed=False,original_assertions_executed_unchanged=True)
print(json.dumps(record,sort_keys=True));raise SystemExit(code)
