"""Independent finite ledger/concurrency/strict-data native probes."""
import sys,json,ast,copy,hashlib,tempfile,shutil,concurrent.futures
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'trusted'))
from common import source_digest,load,require,rejects,ROOT
repo=Path(sys.argv[1]).resolve();before=source_digest(repo);cases=[]
if not (repo/'agent_workflow/strategy.py').is_file():
 print(json.dumps({'id':'p3-strategy-boundary','passed':False,'classification':'infra','source_stable':True,'exactsourceSHA':before,'evidence':{'missing':'strategy','external_backend_calls':0}}));raise SystemExit(2)
m=load(repo,'strategy')
# Reuse frozen real native retrieval generator and immutable proposal fixtures.
source=Path(__file__).resolve().parent/'strategy.py';tree=ast.parse(source.read_text());tree.body=[n for n in tree.body if not(isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='run')];ns={'__file__':str(source)};exec(compile(tree,str(source),'exec'),ns);setup=ns['setup'];proposal=ns['proposal']

def case(name,fn):
 try:fn();cases.append({'case':name,'exit_code':0})
 except Exception as e:cases.append({'case':name,'exit_code':1,'error':type(e).__name__+': '+str(e)})
def invalid(fn):
 try:fn()
 except (ValueError,TypeError,OSError):return
 raise AssertionError('invalid or aliased input accepted')
def put(root,p,ref,value,raw=None):
 path=root/'evidence'/ref;path.write_text(raw if raw is not None else json.dumps(value,sort_keys=True));p['artifact_registry'][ref]=hashlib.sha256(path.read_bytes()).hexdigest()
def evaluate_or_reject(root,p,q):
 try:s=m.StrategyStore(root/'store',trusted_policy=p);r=s.preregister(q);v=s.evaluate(r['id']);require(v['decision'] in ['rollback','stop'],'invalid artifact retained')
 except (ValueError,TypeError,OSError):pass

def strict_data():
 for field,value in [('schema_version',True),('min_reduction',True)]:
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);p,_=setup(m,root);p[field]=value;invalid(lambda:m.StrategyStore(root/'store',trusted_policy=p))
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,_=setup(m,root);s=m.StrategyStore(root/'store',trusted_policy=p);q=proposal(p);q['schema_version']=True;invalid(lambda:s.preregister(q))
 for label,change in [('schema_bool',lambda v:v.update(schema_version=True)),('success_int',lambda v:v['results'][0].update(success=1)),('coverage_bool',lambda v:v['results'][0].update(coverage=True)),('bytes_float',lambda v:v['results'][0].update(used_bytes=1.0)),('elapsed_bool',lambda v:v['costs'].update(elapsed_seconds=True)),('money_nan',lambda v:v['costs'].update(money=float('nan'))),('retries_bool',lambda v:v['costs'].update(retries=True))]:
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);p,values=setup(m,root);v=values['candidate.json'];change(v);put(root,p,'candidate.json',v);evaluate_or_reject(root,p,proposal(p,label))
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,values=setup(m,root);v=values['candidate.json'];raw=json.dumps(v,sort_keys=True);raw=raw[:-1]+',"schema_version":1}';put(root,p,'candidate.json',v,raw=raw);evaluate_or_reject(root,p,proposal(p,'duplicate-json'))

def alias_pair():
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,_=setup(m,root)
  for original,alias in [('baseline.json','baseline-alias.json'),('candidate.json','candidate-alias.json')]:
   shutil.copyfile(root/'evidence'/original,root/'evidence'/alias);p['artifact_registry'][alias]=p['artifact_registry'][original]
  s=m.StrategyStore(root/'store',trusted_policy=p);r=s.preregister(proposal(p,'original'));v=s.evaluate(r['id']);require(v['decision']=='retain')
  q=proposal(p,'alias','candidate-alias.json');q['baseline_ref']='baseline-alias.json'
  try:r=s.preregister(q);s.evaluate(r['id'])
  except (ValueError,TypeError,OSError):pass
  require(len(s.history())==1,'same artifact pair counted twice under path aliases')

def persistence_crash():
 for mode in ['delete-ledger','partial-tail','delete-all']:
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);p,_=setup(m,root);s=m.StrategyStore(root/'store',trusted_policy=p);r=s.preregister(proposal(p));s.evaluate(r['id']);ledgers=[x for x in (root/'store').rglob('*') if x.is_file() and 'ledger' in x.name];require(ledgers,'append ledger missing')
   if mode=='delete-ledger':ledgers[0].unlink()
   elif mode=='partial-tail':
    with ledgers[0].open('ab') as f:f.write(b'{"incomplete_crash":')
   else:
    for x in (root/'store').iterdir():
     if x.is_dir():shutil.rmtree(x)
     else:x.unlink()
   invalid(lambda:m.StrategyStore(root/'store',trusted_policy=p))
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,_=setup(m,root);s=m.StrategyStore(root/'store',trusted_policy=p);r=s.preregister(proposal(p));v=s.evaluate(r['id']);v['decision']='stop';require(s.evaluate(r['id'])['decision']=='retain','returned report mutated history')

def concurrent_registration():
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,_=setup(m,root);m.StrategyStore(root/'store',trusted_policy=p);s1=m.StrategyStore(root/'store',trusted_policy=p);s2=m.StrategyStore(root/'store',trusted_policy=p)
  q1=proposal(p,'concurrent1','candidate.json');q2=proposal(p,'concurrent2','nogain1.json')
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
   f1=executor.submit(s1.preregister,q1);f2=executor.submit(s2.preregister,q2);r1=f1.result(timeout=10);r2=f2.result(timeout=10)
  resumed=m.StrategyStore(root/'store',trusted_policy=p);v1=resumed.evaluate(r1['id']);v2=resumed.evaluate(r2['id']);require(v1['decision']=='retain' and v2['decision']=='rollback' and len(resumed.history())==2,'concurrent append lost registration/report')

def containment():
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,_=setup(m,root);copy_path=root/'outside.json';shutil.copyfile(root/'evidence/candidate.json',copy_path);(root/'evidence/link.json').symlink_to(copy_path);p['artifact_registry']['link.json']=hashlib.sha256(copy_path.read_bytes()).hexdigest();evaluate_or_reject(root,p,proposal(p,'symlink','link.json'))
for name,fn in [('strict_boolean_and_json',strict_data),('duplicate_pair_path_alias',alias_pair),('ledger_crash_and_return_tamper',persistence_crash),('finite_concurrent_append',concurrent_registration),('evidence_containment',containment)]:case(name,fn)
after=source_digest(repo);passed=before==after and all(x['exit_code']==0 for x in cases)
print(json.dumps({'id':'p3-strategy-boundary','passed':passed,'classification':'passed' if passed else 'product_defect','source_stable':before==after,'exactsourceSHA':before,'evidence':{'external_backend_calls':0,'mode':'independent-native-ledger-fixtures','cases':cases}}))
raise SystemExit(0 if passed else 1)
