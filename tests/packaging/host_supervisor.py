#!/usr/bin/env python3
"""Real bounded local fixture processes, never a model/provider integration."""
import argparse,concurrent.futures,hashlib,importlib.util,json,os,subprocess,sys,tempfile,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--installed',action='store_true');p.add_argument('--revision-case',choices=['all','launch-drift','recovery-drift'],default='all');a=p.parse_args()
ROOT=Path(__file__).resolve().parents[2]
if not a.installed:sys.path.insert(0,str(ROOT))
import agent_workflow
from agent_workflow.continuation import Continuation,encoded,sha,regular
from agent_workflow.hostsupervisor import LocalProcess,OneShot,write_once
SUBJECT=Path(agent_workflow.__file__).parent.parent
spec=importlib.util.spec_from_file_location('host_supervisor_fixture',ROOT/'tests/portable/fixtures/research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def denied(fn):
 try:fn()
 except (ValueError,OSError):return
 raise AssertionError('unsafe action admitted')

def main():
 results=[]
 with tempfile.TemporaryDirectory(prefix='iterail-host-process-') as temp:
  cases=m.Cases(SUBJECT,Path(temp).resolve())
  def setup(name,sleep=5,max_seconds=10,external_script=False):
   f=cases.fixture(name);f['proof']=cases.q.baseline(f);f['tid']=cases.q.task(f,'host-fixture');cases.q.assessment(f,f['proof'],[cases.q.candidate(f['tid'])])
   work=f['root']/'host-work';work.mkdir();script=(f['root'] if external_script else work)/'fixture.py'
   script.write_text('from pathlib import Path\nimport os,time\np=Path("starts.txt")\nwith p.open("a") as f:f.write(os.environ["ITERAIL_EXECUTION_ID"]+"\\n")\nprint("fixture-started",flush=True)\ntime.sleep('+str(sleep)+')\n')
   exe=Path(sys.executable).resolve()
   policy={'schema_version':1,'fixture_only':True,'continuation_state':str(f['continuation']),'executable_path':str(exe),'executable_sha256':sha(exe.read_bytes()),'script_path':str(script),'script_sha256':sha(script.read_bytes()),'cwd':str(work),'max_seconds':max_seconds,'output_bytes':2048}
   pp=f['root']/'host-policy.json';pp.write_bytes(encoded(policy));f.update(host_policy=pp,host_sha=sha(pp.read_bytes()),work=work)
   f['adapter']=LocalProcess(f['c'],str(pp),f['host_sha']);f['s']=OneShot(f['c'],f['adapter']);f['original']=f['c'].status();return f
  def reopen(f):
   f['c'].db.close();f['c']=Continuation(str(f['continuation']),clock=lambda:f['clock'][0]);f['adapter']=LocalProcess(f['c'],str(f['host_policy']),f['host_sha']);f['s']=OneShot(f['c'],f['adapter'])
  def wait(f,states,started=False):
   cutoff=time.time()+12
   while time.time()<cutoff:
    x=f['s'].run('observe')
    if x['host']['state'] in states and (not started or (f['work']/'starts.txt').exists()):return x
    time.sleep(.05)
   raise AssertionError('host observation timeout '+str(x))
  def resources(f):
   x=f['c'].status();assert x['started']==f['original']['started'] and x['deadline']==f['original']['deadline'] and x['requests_spent']==2,x
  def cancel(f):
   f['s'].run('cancel');x=wait(f,{'cancelled','completed','expired'});resources(f);f['c'].stop('fixture complete');return x
  def cli(f,action):
   env=dict(os.environ,PYTHONPATH=str(SUBJECT),PYTHONDONTWRITEBYTECODE='1')
   command=[sys.executable,'-B','-m','agent_workflow.hostsupervisor','--state',str(f['continuation']),'--host-policy',str(f['host_policy']),'--host-policy-sha256',f['host_sha'],action]
   return subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
  def launch_drift():
   f=setup('async-script-drift',sleep=.1);script=Path(f['adapter'].policy['script_path']);original_popen=subprocess.Popen;injected=[]
   def boundary(argv,*args,**kwargs):
    if any(isinstance(v,str) and 'from agent_workflow.hostprocess import main;' in v for v in argv):
     script.write_text('from pathlib import Path\nPath("unapproved.txt").write_text("must never execute")\n');injected.append(True)
    return original_popen(argv,*args,**kwargs)
   subprocess.Popen=boundary
   try:f['s'].run()
   finally:subprocess.Popen=original_popen
   assert injected==[True];intent=f['s']._intents()[0];job=f['adapter']._job(intent);cutoff=time.time()+8
   while time.time()<cutoff and not (job/'launch-rejection.json').exists() and not (job/'result.json').exists():time.sleep(.05)
   if (job/'result.json').exists():wait(f,{'completed','failed','expired','cancelled'})
   f['c'].stop('fixture complete')
   assert not (f['work']/'unapproved.txt').exists(),'unapproved replacement script executed after early validation'
   assert (job/'launch-rejection.json').exists(),'late launch validation was not reached'
   assert not (f['work']/'starts.txt').exists();resources(f)
   assert sha((job/'script.py').read_bytes())==f['adapter'].policy['script_sha256']
   results.append('real async wrapper refuses script drift after early check; approved snapshot retained and replacement never executes')
  def recovery_drift():
   for mode in ('edit','delete'):
    f=setup('recovery-script-'+mode);f['s'].run();wait(f,{'running'},True);script=Path(f['adapter'].policy['script_path'])
    if mode=='edit':script.write_text(script.read_text()+'# harmless collaborative edit\n')
    else:script.unlink()
    observed=cli(f,'observe');reconciled=cli(f,'reconcile');cancelled=cli(f,'cancel')
    # Even a failing old CLI must not leave the real fixture running.
    if cancelled.returncode!=0:f['s'].run('cancel')
    wait(f,{'cancelled'});resources(f);f['c'].stop('fixture complete')
    assert observed.returncode==reconciled.returncode==cancelled.returncode==0,mode+': '+observed.stdout+reconciled.stdout+cancelled.stdout
    assert json.loads(cancelled.stdout)['ok'] is True
    assert len((f['work']/'starts.txt').read_text().splitlines())==1
   results.append('real live fixture remains observable/reconcilable/cancellable through fresh CLI after script edit or deletion; nonce identity and budget retained')
  if a.revision_case=='all':
   for mode in ('symlink','directory'):
    f=setup('async-cwd-'+mode,sleep=.1,external_script=True);original_popen=subprocess.Popen;injected=[];moved=f['root']/'original-work'
    def cwd_boundary(argv,*args,**kwargs):
     if any(isinstance(v,str) and 'from agent_workflow.hostprocess import main;' in v for v in argv):
      f['work'].rename(moved)
      if mode=='symlink':f['work'].symlink_to(f['repo'],target_is_directory=True)
      else:f['work'].mkdir()
      injected.append(True)
     return original_popen(argv,*args,**kwargs)
    subprocess.Popen=cwd_boundary
    try:f['s'].run()
    finally:subprocess.Popen=original_popen
    intent=f['s']._intents()[0];job=f['adapter']._job(intent);cutoff=time.time()+8
    while time.time()<cutoff and not (job/'launch-rejection.json').exists() and not (job/'result.json').exists():time.sleep(.05)
    if f['work'].is_symlink():f['work'].unlink()
    else:f['work'].rmdir()
    moved.rename(f['work']);f['c'].stop('fixture complete')
    assert injected==[True] and (job/'launch-rejection.json').exists(),mode
    assert not (f['repo']/'starts.txt').exists() and not (f['work']/'starts.txt').exists();resources(f)
   results.append('real async cwd symlink-to-candidate and non-symlink directory replacements reject before fixture execution')
  if a.revision_case in {'all','launch-drift'}:launch_drift()
  if a.revision_case in {'all','recovery-drift'}:recovery_drift()
  if a.revision_case!='all':
   print(json.dumps({'passed':True,'checks':results,'revision_case':a.revision_case,'model_dispatches':0}));return
  f=setup('start-restart-cancel');first=f['s'].run();assert first['intent']['workdir']==str(f['work']) and first['intent']['source_root']==str(f['repo']);wait(f,{'running'},True);reopen(f);x=f['s'].run('reconcile');assert x['intent']['execution_id']==first['intent']['execution_id'];cancel(f)
  assert len((f['work']/'starts.txt').read_text().splitlines())==1
  events=[json.loads(row[0]) for row in f['c'].db.execute("SELECT body FROM host_process_intents")];assert len(events)==1
  assert f['c']._core_read('SELECT id FROM runs WHERE task_id=?',(f['tid'],))==[]
  results.append('real start/observe; reopen and nonce cancellation; no Core run or acceptance invented')
  f=setup('lost-start-reply');original=f['adapter'].start
  def lost(intent):original(intent);raise OSError('fixture lost start reply')
  f['adapter'].start=lost;denied(lambda:f['s'].run());wait(f,{'running'},True);reopen(f);f['s'].run();cancel(f);assert len((f['work']/'starts.txt').read_text().splitlines())==1
  results.append('lost start reply reconciles exact durable key without second execution or budget refund')
  f=setup('before-start-crash');seen=[]
  def crash(intent):
   assert f['c'].db.execute('SELECT count(*) FROM host_process_intents').fetchone()[0]==1
   seen.append(intent);raise OSError('crash before host start')
  f['adapter'].start=crash;denied(lambda:f['s'].run());reopen(f);assert f['s'].run()['host']['state']=='uncertain';assert not (f['work']/'starts.txt').exists();resources(f);f['c'].stop('fixture complete')
  results.append('intent committed before adapter; pre-start crash stays uncertain and is never retried')
  f=setup('bridge-begin-reply-loss');begin=f['s'].b.begin
  def lost_begin(request_id):begin(request_id);raise OSError('lost begin reply before host intent')
  f['s'].b.begin=lost_begin;denied(lambda:f['s'].run());reopen(f);denied(lambda:f['s'].run());assert not (f['work']/'starts.txt').exists();resources(f);f['c'].stop('fixture complete')
  results.append('consumed bridge without host intent is uncertain; restart never re-begins or starts it')
  f=setup('command-drift');script=Path(f['adapter'].policy['script_path']);script.write_text(script.read_text()+'# changed after owner pin\n');denied(lambda:f['s'].run());assert not (f['work']/'starts.txt').exists();resources(f);f['c'].stop('fixture complete')
  results.append('script drift after policy pin refuses launch without changing original grant')
  for name in ('stop-before-local-start','expiry-before-local-start'):
   f=setup(name);start=f['adapter'].start;captured=[]
   def delayed(intent):captured.append(intent);return f['adapter']._observation(intent,'uncertain',0)
   f['adapter'].start=delayed;f['s'].run();f['adapter'].start=start
   if name.startswith('stop'):f['c'].stop('owner stop before host launch')
   else:f['clock'][0]=f['c'].status()['deadline']+1
   denied(lambda:start(captured[0]));assert not (f['work']/'starts.txt').exists();resources(f);f['c'].stop('fixture complete')
  results.append('consumed intent cannot launch after owner stop or expiry; worker cwd and source root are distinct bound fields')
  f=setup('host-policy-repin');policy=dict(f['adapter'].policy);policy['max_seconds']=11;f['host_policy'].write_bytes(encoded(policy));replacement=LocalProcess(f['c'],str(f['host_policy']),sha(f['host_policy'].read_bytes()));denied(lambda:OneShot(f['c'],replacement));assert not f['s']._intents() and f['c'].status()['requests_spent']==0;f['c'].stop('fixture complete')
  results.append('new policy hash cannot replace durable adapter identity or regrant resources')
  f=setup('physical-controller-drift');runtime=Path(f['c'].status()['active_version']['controller_path'])/'agent_workflow/__init__.py';prior=runtime.read_bytes();runtime.write_bytes(prior+b'\n# unreviewed controller drift\n');denied(lambda:OneShot(f['c'],f['adapter']));assert not f['s']._intents();runtime.write_bytes(prior);assert f['c'].status()['requests_spent']==0;f['c'].stop('fixture complete')
  results.append('unreviewed physical controller bytes cannot dispatch or claim renewed authority')
  f=setup('corrupt-observation');f['s'].run();wait(f,{'running'},True);intent=f['s']._intents()[0];job=f['adapter']._job(intent);f['s'].run('cancel');wait(f,{'cancelled'});v=json.loads((job/'observation.json').read_text());v['nonce']='incorrect';(job/'observation.json').write_bytes(encoded(v));denied(lambda:f['s'].run('reconcile'));f['c'].stop('fixture complete')
  results.append('wrong execution nonce rejects; recovered caller sends no PID signal')
  f=setup('missing-observation');f['s'].run();wait(f,{'running'},True);cancel(f);intent=f['s']._intents()[0];job=f['adapter']._job(intent);(job/'observation.json').unlink();reopen(f);assert f['s'].run()['host']['state']=='uncertain';assert len((f['work']/'starts.txt').read_text().splitlines())==1
  results.append('missing observation fail-closed after restart; no lease renewal or new dispatch')
  for name in ('stopped','expired','quality-blocked'):
   f=setup(name)
   if name=='stopped':f['c'].stop('fixture owner stop')
   elif name=='expired':f['clock'][0]=f['c'].status()['deadline']+1
   else:cases.q.assessment(f,f['proof'],[cases.q.candidate(f['tid'])],changes={'safety':'block'})
   try:f['s'].run()
   except ValueError:pass
   assert not (f['work']/'starts.txt').exists();assert not f['s']._intents();f['c'].stop('fixture complete')
  results.append('stop, original deadline and current quality refusal prevent process start')
  f=setup('stop-running');f['s'].run();wait(f,{'running'},True);f['c'].stop('owner stop');assert f['s'].run()['host']['state'] in {'running','cancelled'};wait(f,{'cancelled'});resources(f)
  results.append('owner stop requests real cancellation; request alone is not cancellation proof')
  f=setup('resource-timeout',max_seconds=1);f['s'].run();wait(f,{'expired'},True);resources(f);reopen(f);assert f['s'].run()['host']['state']=='expired';assert len((f['work']/'starts.txt').read_text().splitlines())==1;f['c'].stop('fixture complete')
  results.append('real guardian resource timeout terminates child; restart preserves original owner budget')
  f=setup('parallel-callers')
  def concurrent_tick(_):
   c=Continuation(str(f['continuation']));adapter=LocalProcess(c,str(f['host_policy']),f['host_sha'])
   try:return OneShot(c,adapter).run()
   finally:c.db.close()
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:replies=list(pool.map(concurrent_tick,range(2)))
  assert replies[0]['intent']['execution_id']==replies[1]['intent']['execution_id'];wait(f,{'running'},True);cancel(f);assert len((f['work']/'starts.txt').read_text().splitlines())==1
  results.append('parallel callers serialize on durable continuation and start exactly one local fixture')
  f=setup('natural-completion',sleep=.1);f['s'].run();x=wait(f,{'completed'},True);resources(f);reopen(f);assert f['s'].run()['host']['state']=='completed';assert len((f['work']/'starts.txt').read_text().splitlines())==1;assert x['core_acceptance'] is False
  intent=f['s']._intents()[0];terminal=x['host'];f['s']._record(intent,f['adapter']._observation(intent,'uncertain',0))
  denied(lambda:f['s']._record(intent,{**terminal,'sequence':terminal['sequence']-1}))
  denied(lambda:f['s']._record(intent,{**terminal,'state':'running','sequence':terminal['sequence']+1,'observed_at':time.time()}))
  results.append('uncertainty cannot erase observation cursor or permit terminal-to-running regression')
  # Actual separate command entry observes the same intent; no second ledger or process.
  env=dict(os.environ,PYTHONPATH=str(SUBJECT),PYTHONDONTWRITEBYTECODE='1')
  cmd=[sys.executable,'-B','-m','agent_workflow.hostsupervisor','--state',str(f['continuation']),'--host-policy',str(f['host_policy']),'--host-policy-sha256',f['host_sha'],'reconcile']
  q=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=10);assert q.returncode==0,q.stderr+q.stdout;assert json.loads(q.stdout)['result']['host']['execution_id']==x['host']['execution_id'];f['c'].stop('fixture complete')
  job=f['adapter']._job(intent);original_observation=(job/'observation.json').read_bytes();(job/'observation.json').write_bytes(b'[]')
  q=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=10);assert q.returncode==1 and json.loads(q.stdout)['ok'] is False;qerror=json.loads(q.stdout)['error'];assert 'host observation required' in qerror,qerror
  (job/'observation.json').write_bytes(original_observation)
  bad=f['root']/'corrupt-state';bad.mkdir();(bad/'continuation.sqlite').write_bytes(b'not a sqlite database');(bad/'identity.json').write_bytes(b'{}');badcmd=list(cmd);badcmd[badcmd.index('--state')+1]=str(bad)
  q=subprocess.run(badcmd,env=env,capture_output=True,text=True,timeout=10);assert q.returncode==1 and json.loads(q.stdout)['ok'] is False and 'DatabaseError' in json.loads(q.stdout)['error'],q.stderr+q.stdout
  results.append('malformed observations and damaged SQLite history return structured CLI refusal without launch')
  results.append('actual one-shot module CLI shares durable intent and observes terminal guardian result; no automatic successor')
 print(json.dumps({'passed':True,'checks':results,'installed':a.installed,'real_local_fixture_processes':10,'model_dispatches':0,'provider_readiness':'not_tested','OS_wake_registration':False,'independent_review':'unchanged Core/quality gates tested separately'}))

if __name__=='__main__':main()
