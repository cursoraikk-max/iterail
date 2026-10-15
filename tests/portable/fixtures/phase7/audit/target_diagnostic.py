"""One native target diagnostic replay with fixture retention; protected checks unchanged."""
import ast,hashlib,json,shutil,sys,traceback
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];source=ROOT/'phase7/candidate'
sys.path.insert(0,str(ROOT/'phase4/trusted'));import common
common.REPO=source;sys.path.insert(0,str(source));from agent_workflow import evolution
file=ROOT/'phase4/trusted/target.py';tree=ast.parse(file.read_text());tree.body=tree.body[:-1]
h={'__file__':str(file)};exec(compile(tree,str(file),'exec'),h)
destination=ROOT/'phase7/evidence/target-audit-fixture';results=[]
original_fixture=h['fixture'];original_cycle=evolution.Supervisor.cycle

def fixture(*args,**kwargs):
    tmp,root,c=original_fixture(*args,**kwargs);cleanup=tmp.cleanup
    def retain():
        try:
            shutil.copytree(root,destination);destination.chmod(0o700)
        finally:cleanup()
    tmp.cleanup=retain;return tmp,root,c

def cycle(owner,*args,**kwargs):
    r=original_cycle(owner,*args,**kwargs)
    results.append({key:r[key] for key in ['phase','failure_class','reason','schema_version','deployed','job_id','check_id','evidence_kind'] if key in r})
    return r
h['fixture']=fixture;evolution.Supervisor.cycle=cycle;before=common.source_digest(source)
try:h['native_target'](evolution);error=None
except Exception as e:error=type(e).__name__+': '+str(e)
finally:evolution.Supervisor.cycle=original_cycle
after=common.source_digest(source)
print(json.dumps({'diagnostic_only':True,'protected_check_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'source_before':before,'source_after':after,'source_unchanged':before==after,'external_provider_calls':0,'result_error':error,'cycle_results':results,'retained_fixture':str(destination)},sort_keys=True,indent=2))
raise SystemExit(0 if error is None else 1)
