"""Frozen no-model recovery contract: real Core producer, original accepted metrics gap."""
import ast,hashlib,json,shutil,subprocess,sys,tempfile,traceback
from pathlib import Path
sys.dont_write_bytecode=True
SOURCE=Path(sys.argv[1]).resolve();HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(SOURCE))
from agent_workflow.core import Engine,digest
before=digest(SOURCE);cases=[];infrastructure=False
CHECKER=r"""import ast,hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);owner=Path(__file__).parent;counter=owner/'attempts';counter.write_text(str(int(counter.read_text())+1))
def digest(root):
 h=hashlib.sha256()
 for p in sorted(root.rglob('*')):
  rel=p.relative_to(root)
  if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or p.suffix=='.pyc':continue
  if p.is_file():h.update(str(rel).encode()+b'\0'+p.read_bytes()+b'\0')
 return h.hexdigest()
sha=digest(root)
if (owner/'unavailable').exists():
 print(json.dumps({'id':'known-metrics-feature','passed':False,'classification':'infrastructure_error','source_stable':True,'exactsourceSHA':sha,'evidence':['temporary native checker transport unavailable']}));raise SystemExit(2)
functions={n.name for n in ast.parse((root/'metrics.py').read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
required=sys.argv[2];passed=required in functions
print(json.dumps({'id':'known-metrics-feature','passed':passed,'classification':'passed' if passed else 'product_defect','source_stable':True,'exactsourceSHA':sha,'evidence':[{'required_existing_API':required,'present':passed,'fixture_origin':'unchanged accepted ac4cf152 metrics.py'}]}));raise SystemExit(0 if passed else 1)
"""
def require(v,msg):
 if not v:raise AssertionError(msg)
def one(mode):
 with tempfile.TemporaryDirectory(prefix='p9-producer-') as t:
  root=Path(t);repo=root/'repo';repo.mkdir();shutil.copyfile(HERE/'baseline_metrics.py',repo/'metrics.py');subprocess.run(['git','init','-q',str(repo)],check=True);subprocess.run(['git','-C',str(repo),'add','metrics.py'],check=True);subprocess.run(['git','-C',str(repo),'-c','user.name=Native fixture','-c','user.email=fixture@invalid','-c','commit.gpgsign=false','commit','-qm','unchanged accepted metrics fixture'],check=True);owner=root/'owner';owner.mkdir();checker=owner/'checker.py';checker.write_text(CHECKER);(owner/'attempts').write_text('0')
  required='evolution_summary' if mode=='recovery' else 'build_metrics'
  registry={'schema_version':1,'allowed_sources':['failed_log','unresolved_review','owner_check'],'registry':{'known-metrics-feature':{'argv':[sys.executable,'-B',str(checker),'{repo}',required],'acceptance':{'exit_code':1,'classification':'product_defect','source_stable':True}}},'queue':{'max_open':8,'max_new_per_cycle':1,'wip':1,'aging_seconds':60,'repro_seconds':5,'log_bytes':8192}}
  reg=owner/'producer.json';reg.write_text(json.dumps(registry,sort_keys=True));policy={'repo':str(repo),'tests':[[sys.executable,'-B',str(checker),str(repo),required]],'sources':['local'],'budget':{'lease_seconds':120,'run_seconds':600,'test_seconds':10,'max_attempts':3},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'network':False,'publish':False},'producer_policy':{'path':str(reg),'sha256':hashlib.sha256(reg.read_bytes()).hexdigest()}}
  original=digest(repo);checker_sha=hashlib.sha256(checker.read_bytes()).hexdigest();state=root/'core';e=Engine(state,policy);snapshot=json.dumps(e.config,sort_keys=True)
  try:
   flag=owner/'unavailable'
   if mode=='recovery':flag.touch()
   first=e.producer_discover();first_count=int((owner/'attempts').read_text());first_rows=[dict(r) for r in e.db.execute('SELECT id,status,evidence FROM producer_candidates')]
   require(first_count==1 and e.db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0,'first infrastructure/pass attempt manufactured task')
   if mode=='recovery':
    require(json.loads(first_rows[0]['evidence'])['result']['exit_code']==2,'first result not actual infrastructure exit2');flag.unlink()
   e.db.close();e=Engine(state);second=e.producer_discover();second_count=int((owner/'attempts').read_text());third=e.producer_discover();third_count=int((owner/'attempts').read_text());tasks=e.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
   details={'first_physical_attempts':first_count,'second_physical_attempts':second_count,'third_physical_attempts':third_count,'second_admitted':second['admitted'],'tasks':tasks,'unchanged_original_metrics_fixture':digest(repo)==original,'fixed_checker_unchanged':hashlib.sha256(checker.read_bytes()).hexdigest()==checker_sha,'source_under_test_unchanged':digest(SOURCE)==before,'native_Core_runs':e.db.execute('SELECT count(*) FROM runs').fetchone()[0]}
   require(details['unchanged_original_metrics_fixture'] and details['fixed_checker_unchanged'] and json.dumps(e.config,sort_keys=True)==snapshot,'recovery changed source/checker/frozen authority')
   if mode=='recovery':
    require(second_count==2 and len(second['admitted'])==1 and tasks==1,'transient infrastructure rejection permanently caches existing real feature failure: '+json.dumps(details,sort_keys=True))
    require(third_count==2 and third['admitted']==[],'admitted semantic finding rerun or duplicated')
    binding=json.loads(e.db.execute('SELECT body FROM producer_task_checks').fetchone()[0]);require(binding['check_id']=='known-metrics-feature' and binding['argv']==registry['registry']['known-metrics-feature']['argv'][:3]+[str(repo.resolve()),required],'admission changed immutable check binding')
    failures=[json.loads(r[0]) for r in e.db.execute("SELECT data FROM events WHERE kind='producer_reproduced'")];require(any(x.get('evidence',{}).get('result',{}).get('exit_code')==2 for x in failures),'initial infrastructure evidence was discarded')
   else:require(second_count==third_count==1 and tasks==0,'known passing result was unnecessarily rerun or admitted')
   return details
  finally:e.db.close()
for name,mode in [('transient_infrastructure_rejection_retries_same_source_after_restart','recovery'),('valid_passing_result_remains_cached_without_tasks','pass')]:
 try:details=one(mode);cases.append({'case':name,'passed':True,'details':details})
 except AssertionError as error:cases.append({'case':name,'passed':False,'classification':'product_defect','error':type(error).__name__+': '+str(error)})
 except Exception as error:infrastructure=True;cases.append({'case':name,'passed':False,'classification':'infrastructure_error','error':type(error).__name__+': '+str(error)})
stable=digest(SOURCE)==before;passed=all(c['passed'] for c in cases) and stable
print(json.dumps({'id':'iterail-producer-transient-recovery','passed':passed,'classification':'passed' if passed else ('infrastructure_error' if infrastructure or not stable else 'product_defect'),'exactsourceSHA':before,'source_stable':stable,'evidence_kind':'native_fixture','external_provider_calls':0,'evidence':cases},sort_keys=True));raise SystemExit(0 if passed else (2 if infrastructure or not stable else 1))
