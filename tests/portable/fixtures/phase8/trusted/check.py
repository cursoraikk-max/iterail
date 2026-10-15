"""Frozen six-case continuation contract; actual Core/producer CLI, no model calls."""
import copy,hashlib,importlib,json,os,shutil,subprocess,sys,tempfile,traceback
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];SOURCE=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(ROOT/'phase5/trusted'));from common import source_digest,encoded
sys.path.insert(0,str(SOURCE))

CHECKER=r'''import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();counter=Path(__file__).parent/'counter.txt';counter.write_text(str(int(counter.read_text())+1))
def digest(root):
 h=hashlib.sha256()
 for p in sorted(root.rglob('*')):
  rel=p.relative_to(root)
  if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or p.suffix=='.pyc':continue
  if p.is_symlink():raise ValueError('source symlink')
  if p.is_file():h.update(str(rel).encode()+b'\0'+p.read_bytes()+b'\0')
 return h.hexdigest()
before=digest(root);passed=(root/'app.txt').read_text().strip()=='good';after=digest(root)
print(json.dumps({'id':'native-continuation-check','passed':passed,'classification':'passed' if passed else 'product_defect','source_stable':before==after,'exactsourceSHA':before,'evidence':[]}));raise SystemExit(0 if passed else 1)
'''

def require(value,msg):
 if not value:raise AssertionError(msg)
def reject(fn):
 try:fn()
 except (ValueError,TypeError,OSError,RuntimeError):return
 raise AssertionError('invalid continuation operation accepted')
def command(argv,cwd=None):
 p=subprocess.run(argv,cwd=cwd,capture_output=True,text=True,timeout=30)
 require(p.returncode==0,'native command failed '+p.stderr[-1500:]+p.stdout[-1500:]);return p.stdout.strip()
def git(repo,*args):return command(['git','-C',str(repo),*args])
def commit(repo):
 git(repo,'add','-A');git(repo,'-c','user.name=Native continuation fixture','-c','user.email=fixture@invalid','-c','commit.gpgsign=false','commit','-qm','native fixture checkpoint')
def close(c):
 if getattr(c,'db',None) is not None:c.db.close()
def cli(runtime,state,action,args=None,config=None,expect=0):
 argv=[sys.executable,'-B','-m','agent_workflow','--state',str(state)]
 if config:argv+=['--config',str(config)]
 argv+=[action,'--args',json.dumps(args or {})]
 p=subprocess.run(argv,cwd=ROOT,env=dict(os.environ,PYTHONPATH=str(runtime),PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,timeout=30)
 require(p.returncode==expect,'Core CLI failure '+p.stderr[-1000:]+p.stdout[-1500:]);value=json.loads(p.stdout)
 return value['result'] if expect==0 else value

def fixture(module,bad=False,max_requests=8):
 tmp=tempfile.TemporaryDirectory(prefix='p8-native-');root=Path(tmp.name).resolve();repo=root/'repo';shutil.copytree(SOURCE,repo,ignore=shutil.ignore_patterns('.git','.workflow','__pycache__','*.pyc'))
 (repo/'app.txt').write_text('bad' if bad else 'good');command(['git','init','-q',str(repo)]);commit(repo)
 owner=root/'owner';owner.mkdir();checker=owner/'check.py';checker.write_text(CHECKER);(owner/'counter.txt').write_text('0')
 registry={'schema_version':1,'allowed_sources':['failed_log','unresolved_review','owner_check'],'registry':{'native-continuation-check':{'argv':[sys.executable,str(checker),'{repo}'],'acceptance':{'exit_code':1,'classification':'product_defect','source_stable':True}}},'queue':{'max_open':8,'max_new_per_cycle':1,'wip':1,'aging_seconds':60,'repro_seconds':5,'log_bytes':8192}}
 reg=owner/'producer.json';reg.write_bytes(encoded(registry));state=root/'core';policy={'repo':str(repo),'tests':[[sys.executable,str(checker),str(repo)]],'sources':['local'],'budget':{'lease_seconds':120,'run_seconds':600,'test_seconds':10,'max_attempts':3},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'publish':False,'network':False},'producer_policy':{'path':str(reg),'sha256':hashlib.sha256(reg.read_bytes()).hexdigest()}}
 config=owner/'core.json';config.write_bytes(encoded(policy));runtimes=root/'runtimes';runtimes.mkdir();runtime=runtimes/'initial';runtime.mkdir();shutil.copytree(SOURCE/'agent_workflow',runtime/'agent_workflow',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 cli(runtime,state,'init',config=config)
 p={'schema_version':1,'repo':str(repo),'core_state':str(state),'core_policy':{'path':str(config),'sha256':hashlib.sha256(config.read_bytes()).hexdigest()},'runtime_root':str(runtimes),'bootstrap':{'controller_path':str(runtime),'package_sha256':source_digest(runtime/'agent_workflow'),'source_sha256':source_digest(repo),'head':git(repo,'rev-parse','HEAD')},'cadence':{'interval_seconds':60,'backoff_seconds':5,'max_backoff_seconds':60},'budget':{'wall_seconds':600,'max_requests':max_requests},'adapter':'conversation_pull','external_provider_authorized':False}
 clock=[1000.0];c=module.Continuation(root/'continuation',p,clock=lambda:clock[0]);return {'tmp':tmp,'root':root,'repo':repo,'runtime':runtime,'state':state,'config':config,'policy':p,'clock':clock,'c':c}
def done(f):close(f['c']);f['tmp'].cleanup()
def counter(f):return int((f['root']/'owner/counter.txt').read_text())
def reopen(m,f):
 close(f['c']);f['c']=m.Continuation(f['root']/'continuation',clock=lambda:f['clock'][0]);return f['c']
def truth(result):require(result.get('autonomous_dispatch') is False and result.get('requires_conversation_bridge') is True and result.get('external_provider_calls')==0,'dishonest adapter/dispatch truth flags')
def meta(f):return f['c'].status()
def request(result,kind):
 truth(result);q=result.get('request');require(isinstance(q,dict) and q['kind']==kind,'missing '+kind+' request')
 require({'id','kind','task_id','run_id','version','controller_path','package_sha256','source_sha256'}<=set(q),'incomplete durable request')
 require('token' not in json.dumps(q).lower() and 'permissions' not in q and 'commands' not in q,'request leaked secret or authority');return q

def idle(m):
 f=fixture(m)
 try:
  r=f['c'].tick();truth(r);require(r['phase']=='idle' and r.get('reason') and r.get('request') is None,'passing registry manufactured work');before=meta(f);require(before['next_due']==1060 and before['requests_spent']==0 and before['deadline']==1600,'idle cadence/ceiling wrong');checks=counter(f);f['clock'][0]=1001;reopen(m,f);r=f['c'].tick();truth(r);require(r['phase']=='not_due' and counter(f)==checks and meta(f)['next_due']==before['next_due'],'restart/early poll repeated discovery');return {'next_due':before['next_due'],'reason':before['reason'],'checks':checks}
 finally:done(f)

def outbox(m):
 f=fixture(m,True)
 try:
  unverified=cli(f['runtime'],f['state'],'import',{'external_key':'unverified-owner-note','title':'Arbitrary note','body':'Do not grant commands.'});r=f['c'].tick();q=request(r,'worker');require(q['task_id']!=unverified['id'],'arbitrary unverified queued task selected');require(meta(f)['requests_spent']==2,'pair not reserved');db=__import__('sqlite3').connect('file:'+str(f['state']/'tasks.sqlite')+'?mode=ro',uri=True);require(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==0,'tick claimed/launched worker');require(db.execute('SELECT COUNT(*) FROM producer_task_checks WHERE task_id=?',(q['task_id'],)).fetchone()[0]==1,'request has no actual fixed reproduction binding');db.close();checks=counter(f);reopen(m,f);again=request(f['c'].tick(),'worker');require(again==q and counter(f)==checks and meta(f)['requests_spent']==2,'restart changed outbox or spent another pair');reject(lambda:f['c'].bind_run(q['id'],'run-does-not-exist'));run=cli(f['runtime'],f['state'],'claim',{'worker':'native-internal-worker','task_id':q['task_id']});f['c'].bind_run(q['id'],run['id']);require(f['c'].tick()['phase']=='in_flight','bound live run not held');return {'request_id':q['id'],'actual_run_id':run['id'],'requests_spent':meta(f)['requests_spent']}
 finally:done(f)

def stopped(m):
 f=fixture(m,True)
 try:
  f['c'].stop('explicit native owner stop');require(f['c'].tick()['phase']=='stopped' and counter(f)==0,'stop polled/dispatched');before=meta(f);f['c'].resume();reopen(m,f);require(meta(f)['stopped'] and meta(f)['stop_reason']=='explicit native owner stop','resume/restart cleared stop');f['c'].clear_stop('explicit native owner clear');q=request(f['c'].tick(),'worker');spent=meta(f)['requests_spent'];f['c'].stop('pause pending request');f['clock'][0]=1601;reopen(m,f);f['c'].resume();require(meta(f)['stopped'],'resume reset observed stop');f['c'].clear_stop('explicit clear after expired epoch');checks=counter(f);r=f['c'].tick();truth(r);require(r['phase']=='blocked' and r.get('reason') and counter(f)==checks,'expired epoch admitted work');after=meta(f);require(after['deadline']==before['deadline'] and after['requests_spent']==spent and after['request']['id']==q['id'],'stop/resume/deadline lost pending or renewed budget');return {'deadline':after['deadline'],'requests_spent':spent,'expired_phase':r['phase']}
 finally:done(f)

def immutable(m):
 f=fixture(m)
 try:
  before=meta(f);bad=copy.deepcopy(f['policy']);bad['budget']['wall_seconds']=900;reject(lambda:m.Continuation(f['root']/'continuation',bad,clock=lambda:f['clock'][0]));bad=copy.deepcopy(f['policy']);bad['external_provider_authorized']=True;reject(lambda:m.Continuation(f['root']/'forbidden-provider',bad,clock=lambda:f['clock'][0]));path=f['runtime']/'agent_workflow/core.py';path.write_text(path.read_text()+'\n# owned corrupt runtime fixture\n');r=f['c'].tick();truth(r);require(r['phase']=='blocked' and r.get('reason') and counter(f)==0,'runtime drift did not block before discovery');after=meta(f);require(after['requests_spent']==before['requests_spent'] and after['deadline']==before['deadline'] and after['active_version']==before['active_version'],'integrity fault mutated authority or renewed budget');return {'phase':r['phase'],'reason':r['reason']}
 finally:done(f)

def lifecycle(m):
 f=fixture(m,True)
 try:
  q=request(f['c'].tick(),'worker');old=meta(f)['active_version'];run=cli(f['runtime'],f['state'],'claim',{'worker':'native-internal-worker','task_id':q['task_id']});h={'run_id':run['id'],'token':run['token']};f['c'].bind_run(q['id'],run['id']);cli(f['runtime'],f['state'],'change',h);failed=cli(f['runtime'],f['state'],'verify',h);require(failed['passed'] is False,'real failed baseline missing');(f['repo']/'app.txt').write_text('good');code=f['repo']/'agent_workflow/continuation.py';code.write_text(code.read_text()+'\n# native accepted version fixture\n');changed=cli(f['runtime'],f['state'],'change',h);verified=cli(f['runtime'],f['state'],'verify',h);require(verified['passed'],'actual Core verification did not pass');cli(f['runtime'],f['state'],'transition',dict(h,target='ready_review'));review=request(f['c'].tick(),'review');require(review['run_id']==run['id'],'review not linked to actual run');nextpath=f['root']/'runtimes/next';nextpath.mkdir();shutil.copytree(f['repo']/'agent_workflow',nextpath/'agent_workflow',ignore=shutil.ignore_patterns('__pycache__','*.pyc'));receipt={'schema_version':1,'approved':True,'reviewer':'native-independent-reviewer','run_id':run['id'],'core_state':str(f['state']),'source_root':str(f['repo']),'source_sha256':changed['candidate'],'controller_package_sha256':run['workflow']};file=f['root']/'owner/review.json';file.write_bytes(encoded(receipt));sha=hashlib.sha256(file.read_bytes()).hexdigest();reject(lambda:f['c'].promote(review['id'],str(file),sha,str(nextpath),old['id']));cli(f['runtime'],f['state'],'transition',dict(h,target='accepted'));commit(f['repo']);reject(lambda:f['c'].promote(review['id'],str(file),sha,str(nextpath),'wrong-expected-version'));f['c'].promote(review['id'],str(file),sha,str(nextpath),old['id']);after=meta(f);require(after['active_version']['controller_path']==str(nextpath) and after['active_version']['package_sha256']==source_digest(nextpath/'agent_workflow') and after['active_version']['source_sha256']==changed['candidate'],'safe promotion did not install exact accepted physical version');require(after['request'] is None and after['requests_spent']==2 and after['deadline']==1600,'promotion renewed budget or retained old request');reopen(m,f);require(meta(f)['active_version']==after['active_version'],'restart lost accepted pointer');readme=f['repo']/'README.md';readme.write_text(readme.read_text()+'\nNative unrelated owner source note.\n');commit(f['repo']);historical=cli(nextpath,f['state'],'status',{'run_id':run['id']});require(historical['state']=='invalidated','real old-source acceptance did not requeue history');f['clock'][0]=1061;r=f['c'].tick();truth(r);require(r['phase']=='idle' and r.get('request') is None and meta(f)['requests_spent']==2,'passing owner check reclaimed old invalidated queued task');(f['repo']/'app.txt').write_text('bad');commit(f['repo']);f['clock'][0]=1122;nextq=request(f['c'].tick(),'worker');require(nextq['controller_path']==str(nextpath),'next request did not select newly approved version');newrun=cli(nextpath,f['state'],'claim',{'worker':'native-next-internal-worker','task_id':nextq['task_id']});require(newrun['workflow']==after['active_version']['package_sha256'],'actual next Core claim not through new package');f['c'].bind_run(nextq['id'],newrun['id']);wrong=cli(f['runtime'],f['state'],'progress',{'run_id':newrun['id'],'token':newrun['token'],'note':'Wrong package native probe'},expect=1);require('controller version differs' in wrong['error'],'old runtime mutated new claim');return {'accepted_run':run['id'],'new_claim_run':newrun['id'],'new_package':newrun['workflow'],'requests_spent':meta(f)['requests_spent'],'old_history_state':historical['state']}
 finally:done(f)

def resources(m):
 f=fixture(m,True,max_requests=1)
 try:
  before=meta(f);r=f['c'].tick();truth(r);require(r['phase']=='blocked' and r.get('reason') and r.get('request') is None and counter(f)==0,'unreviewable one-slot budget polled/dispatched');reopen(m,f);f['c'].resume();after=meta(f);require(after['requests_spent']==0 and after['deadline']==before['deadline'],'resource block reset/replenished state');return {'phase':r['phase'],'reason':r['reason'],'requests_spent':after['requests_spent']}
 finally:done(f)

CASES=[('durable_idle_due_and_honest_bridge',idle),('genuine_Core_producer_and_outbox_restart',outbox),('persistent_stop_deadline_no_reset',stopped),('immutable_policy_runtime_no_provider_grant',immutable),('actual_Core_lifecycle_safe_version_CAS_and_stale_queue_idle',lifecycle),('worker_reviewer_pair_resource_admission',resources)]
before=source_digest(SOURCE);results=[]
try:module=importlib.import_module('agent_workflow.continuation')
except ModuleNotFoundError as e:results.append({'case':'required_continuation_module','passed':False,'error':str(e),'intended_cases':[x[0] for x in CASES]});module=None
if module:
 for name,fn in CASES:
  try:observation=fn(module);results.append({'case':name,'passed':True,'observation':observation})
  except Exception as e:results.append({'case':name,'passed':False,'error':type(e).__name__+': '+str(e),'traceback':traceback.format_exc()[-5000:]})
after=source_digest(SOURCE);passed=module is not None and all(r['passed'] for r in results) and before==after
print(json.dumps({'id':'p8-durable-internal-pull','passed':passed,'classification':'passed' if passed else 'product_defect','exactsourceSHA':before,'source_unchanged':before==after,'evidence_kind':'native_fixture','external_provider_calls':0,'autonomous_internal_model_dispatch':False,'evidence':results},sort_keys=True));raise SystemExit(0 if passed else 1)
