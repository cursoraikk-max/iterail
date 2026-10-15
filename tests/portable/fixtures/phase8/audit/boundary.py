"""Read-only phase8 protected boundary; SQL payloads are hashed, never exported."""
import hashlib,importlib.util,json,sqlite3,subprocess,sys
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('p7_boundary',ROOT/'phase7/audit/boundary_snapshot.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)

def snapshot():
 value=base.snapshot();p7=ROOT/'phase7';files={}
 paths=list((p7/'audit').glob('*.py'))+list((p7/'audit').glob('*manifest*.json'))+list((p7/'controllers').glob('frozen-*/agent_workflow/*.py'))+list(p7.glob('project-*.json'))+[p7/'call.py',p7/'deployments.json',p7/'evidence/independent-task1-review-receipt.json',p7/'evidence/independent-task2-review-receipt.json']
 for path in paths:files[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
 states={}
 for state in ['task1','task2']:
  db=sqlite3.connect('file:'+str(p7/'state'/state/'tasks.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
  try:
   tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
   states[state]={table:hashlib.sha256(base.encoded([dict(r) for r in db.execute('SELECT * FROM "'+table+'" ORDER BY rowid')])).hexdigest() for table in tables}
  finally:db.close()
 value['phase7_source_sha256']=base.source_digest(p7/'candidate');value['phase7_HEAD']=subprocess.run(['git','-C',str(p7/'candidate'),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip();value['phase7_protected_file_sha256']=files;value['phase7_Core_table_sha256']=states;return value
if __name__=='__main__':
 result=snapshot()
 if len(sys.argv)>1:
  prior=json.loads(Path(sys.argv[1]).read_bytes());report={'passed':result==prior,'read_only':True,'snapshot':result,'phase7_source_unchanged':result['phase7_source_sha256']==prior['phase7_source_sha256'] and result['phase7_HEAD']==prior['phase7_HEAD'],'phase7_Core_tables_unchanged':result['phase7_Core_table_sha256']==prior['phase7_Core_table_sha256'],'phase7_protected_files_unchanged':result['phase7_protected_file_sha256']==prior['phase7_protected_file_sha256']};print(json.dumps(report,sort_keys=True,indent=2));raise SystemExit(0 if report['passed'] else 1)
 print(json.dumps(result,sort_keys=True,indent=2))
