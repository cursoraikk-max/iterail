"""Stopped ledgers cannot restart after metadata deletion with lock retained."""
import sys,json,ast,tempfile,hashlib
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'trusted'))
from common import source_digest,load,require,ROOT
repo=Path(sys.argv[1]).resolve();before=source_digest(repo);m=load(repo,'strategy');source=Path(__file__).resolve().parent/'strategy.py';tree=ast.parse(source.read_text());tree.body=[n for n in tree.body if not(isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='run')];ns={'__file__':str(source)};exec(compile(tree,str(source),'exec'),ns)
with tempfile.TemporaryDirectory() as t:
 root=Path(t);p,_=ns['setup'](m,root);s=m.StrategyStore(root/'store',trusted_policy=p)
 for ident,ref in [('nogain1','nogain1.json'),('nogain2','nogain2.json')]:r=s.preregister(ns['proposal'](p,ident,ref));report=s.evaluate(r['id'])
 require(report['decision']=='stop' and report['no_gain_count']==2)
 files_before=sorted(x.name for x in (root/'store').iterdir());deleted=[]
 for name in ['binding.json','head.json','ledger.jsonl']:
  f=root/'store'/name
  if f.exists():f.unlink();deleted.append(name)
 require((root/'store'/'.lock').exists(),'lock retained prerequisite missing')
 reset_allowed=False;would_retain=False
 try:
  resumed=m.StrategyStore(root/'store',trusted_policy=p);reset_allowed=True;r=resumed.preregister(ns['proposal'](p,'after-reset','candidate.json'));v=resumed.evaluate(r['id']);would_retain=v['decision']=='retain'
 except (ValueError,TypeError,OSError):pass
 passed=not reset_allowed
after=source_digest(repo);print(json.dumps({'id':'p3-strategy-stopped-ledger-reset','passed':passed,'classification':'passed' if passed else 'product_defect','source_stable':before==after,'exactsourceSHA':before,'evidence':{'external_backend_calls':0,'previous_decision':'stop','previous_no_gain_count':2,'files_before':files_before,'deleted':deleted,'lock_retained':True,'reset_allowed':reset_allowed,'retained_after_reset':would_retain}}))
raise SystemExit(0 if passed else 1)
