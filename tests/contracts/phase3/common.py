"""Independent fixed native contract runner. No candidate-controlled scoring."""
import copy, hashlib, importlib, json, sys, traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True

def canonical(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def require(v,msg='contract violated'):
 if not v: raise AssertionError(msg)
def rejects(fn):
 try: fn()
 except (ValueError,TypeError,OSError): return
 raise AssertionError('invalid input accepted')
def source_digest(root):
 h=hashlib.sha256()
 for p in sorted(Path(root).resolve().rglob('*')):
  rel=p.relative_to(Path(root).resolve())
  if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or p.suffix=='.pyc': continue
  if p.is_symlink(): raise ValueError('source symlink')
  if p.is_file(): h.update(str(rel).encode()+b'\0'+p.read_bytes()+b'\0')
 return h.hexdigest()
def load(repo,name):
 sys.path.insert(0,str(Path(repo).resolve()))
 return importlib.import_module('agent_workflow.'+name)
def run(ident,name,dependencies,cases):
 repo=Path(sys.argv[1]).resolve(); before=source_digest(repo); results=[]; classification='passed'; code=0
 missing=[x for x in dependencies if not (repo/'agent_workflow'/(x+'.py')).is_file()]
 if missing:
  classification='infra'; code=2; results=[{'case':'dependency','missing':missing,'exit_code':2}]
 elif not (repo/'agent_workflow'/(name+'.py')).is_file():
  classification='product_defect'; code=1; results=[{'case':'required_feature','error':'missing '+name,'exit_code':1}]
 else:
  try:
   mod=load(repo,name)
   for title,fn in cases:
    try: fn(mod); results.append({'case':title,'exit_code':0})
    except Exception as e: results.append({'case':title,'exit_code':1,'error':type(e).__name__+': '+str(e)})
  except Exception as e: results.append({'case':'import','exit_code':1,'error':type(e).__name__+': '+str(e)})
  if any(r['exit_code'] for r in results): classification='product_defect'; code=1
 after=source_digest(repo)
 if before!=after: classification='infra';code=2
 print(json.dumps({'id':ident,'passed':code==0,'classification':classification,'source_stable':before==after,'exactsourceSHA':before,'evidence':{'mode':'native-fixture-contract','external_backend_calls':0,'cases':results}},sort_keys=True))
 raise SystemExit(code)
