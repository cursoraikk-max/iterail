"""Fixed native stable-owner/governor acceptance. No provider/model calls."""
import copy, json, os, sqlite3, subprocess, sys, threading, time
from pathlib import Path
from common import run, fixture, require, rejects, meta, close, canonical


def policy_and_persistence(m):
 tmp,root,c=fixture(m)
 try:
  s=m.Supervisor(root/'owner',c);g=m.Governor(root/'owner');before=meta(g)
  require(before['calls']==0 and before['stopped'] in (0,False),'initial owner state')
  require(before['deadline']>before['started'],'absolute deadline required')
  rid,seconds=g.reserve('attempt',seconds=2,job='fixture-check')
  require(isinstance(rid,str) and 0<seconds<=2,'durable bounded pair reservation')
  after=meta(g);require(after['calls']==2,'attempt must charge worker/reviewer pair upfront')
  require(after['deadline']==before['deadline'] and after['started']==before['started'],'reservation extended original wall')
  g.finish(rid,{'exit_code':1,'evidence_kind':'native_fixture'})
  close(g);close(s)
  s=m.Supervisor(root/'owner');g=m.Governor(root/'owner')
  require(meta(g)['calls']==2 and meta(g)['deadline']==before['deadline'],'restart reset budget/deadline')
  changed=copy.deepcopy(c);changed['budget']['max_calls']+=2
  rejects(lambda:m.Supervisor(root/'owner',changed),'owner config rebound')
  s.set_stop(True);stopped=meta(g);require(stopped['stopped'],'stop not persisted')
  close(s);close(g);s=m.Supervisor(root/'owner');g=m.Governor(root/'owner')
  require(meta(s)['stopped'] and meta(g)['calls']==2,'restart cleared stop or calls')
  rejects(lambda:g.reserve('attempt',seconds=2,job='fixture-check'),'stopped owner dispatched')
  require(meta(g)['calls']==2,'denied reservation charged calls')
  s.set_stop(False);require(not meta(g)['stopped'],'explicit resume failed')
  require(meta(g)['deadline']==before['deadline'],'resume extended deadline')
  close(g);close(s)
 finally:tmp.cleanup()


def pair_admission(m):
 tmp,root,c=fixture(m,max_calls=3)
 try:
  s=m.Supervisor(root/'owner',c);g=m.Governor(root/'owner')
  rid,_=g.reserve('attempt',seconds=2,job='fixture-check');g.finish(rid,{'exit_code':1})
  before=g.status()
  rejects(lambda:g.reserve('attempt',seconds=2,job='fixture-check'),'pair admission spent final single session')
  after=g.status();require(meta(g)['calls']==2,'pair denial charged calls')
  require(after.get('reservations')==before.get('reservations'),'denial created reservation')
  require(after.get('events')==before.get('events'),'denial mutated owner events')
  rid,_=g.reserve('backend:reviewer',seconds=2,job='fixture-check')
  require(meta(g)['calls']==3,'final independent reviewer allowance lost')
  g.finish(rid,{'exit_code':0});rejects(lambda:g.reserve('backend:reviewer',seconds=2),'maxcalls exceeded')
  close(g);close(s)
 finally:tmp.cleanup()


def blocks_and_deadline(m):
 tmp,root,c=fixture(m)
 try:
  s=m.Supervisor(root/'owner',c);g=m.Governor(root/'owner');original=meta(g)
  g.block('auth');require(meta(g)['blocked'],'auth not persistent')
  close(g);close(s);s=m.Supervisor(root/'owner');g=m.Governor(root/'owner')
  require(meta(g)['blocked'],'restart cleared provider block')
  rejects(lambda:g.reserve('attempt',seconds=2),'blocked owner dispatched')
  s.set_stop(True);s.set_stop(False)
  require(meta(g)['blocked'],'stop/resume silently cleared provider block')
  require(meta(g)['deadline']==original['deadline'] and meta(g)['calls']==0,'blocking reset budget')
  close(g);close(s)
 finally:tmp.cleanup()
 tmp,root,c=fixture(m,wall=30)
 try:
  s=m.Supervisor(root/'owner',c);g=m.Governor(root/'owner')
  # Local fault fixture advances original absolute deadline, never calls provider.
  g.db.execute('UPDATE meta SET deadline=?',(time.time()-1,));before=meta(g)
  rejects(lambda:g.reserve('attempt',seconds=2),'expired owner dispatched')
  require(meta(g)['calls']==0 and meta(g)['deadline']==before['deadline'],'expiry denial replenished ledger')
  close(g);close(s)
 finally:tmp.cleanup()


def no_task_cycle(m):
 tmp,root,c=fixture(m)
 try:
  s=m.Supervisor(root/'owner',c);original=meta(s)
  result=s.cycle();require(isinstance(result,dict),'cycle must return typed result')
  require(meta(s)['calls']==0,'empty native discovery dispatched backend')
  require(meta(s)['active']==original['active'] and meta(s)['epoch']==original['epoch'],'empty discovery activated release')
  s.set_stop(True);before=s.status()
  try:s.cycle()
  except (ValueError,RuntimeError):pass
  require(meta(s)['calls']==0 and meta(s)['epoch']==original['epoch'],'stopped cycle dispatched or activated')
  close(s)
 finally:tmp.cleanup()


def stable_and_trusted_drift(m):
 for target in ('stable','trusted','history'):
  tmp,root,c=fixture(m)
  try:
   s=m.Supervisor(root/'owner',c);st=meta(s);close(s)
   if target=='stable':
    path=Path(st['stable_path']);require(path.is_file(),'stable_path must bind physical module')
    path.write_bytes(path.read_bytes()+b'\n# native drift fixture\n')
   elif target=='trusted':
    path=Path(c['checks'][0]['argv'][1]);path.write_text('raise SystemExit(0)\n')
   else:
    # Existing initialized owner root cannot be treated as a new epoch.
    for name in ('evolution.sqlite','owner.sqlite','supervisor.sqlite'):
     p=root/'owner'/name
     if p.exists():
      for side in ('','-wal','-shm'):
       q=Path(str(p)+side)
       if q.exists():q.unlink()
      break
    else:raise AssertionError('owner SQLite path must be exposed by fixed implementation')
   def restart_and_cycle():
    other=m.Supervisor(root/'owner',c)
    try:other.cycle()
    finally:close(other)
   rejects(restart_and_cycle,'drift/missing owner ledger accepted: '+target)
  finally:tmp.cleanup()


def protected_paths(m):
 for path in ('tests/test_runner.py','agent_workflow/evolution.py','agent_workflow/governor.py','agent_workflow/guardian.py','skills/example/SKILL.md','project.example.json','../trusted/check.py','*'):
  tmp,root,c=fixture(m)
  try:
   c['self_update'].append(path);c['allowlist'].append(path)
   rejects(lambda:m.Supervisor(root/'owner',c),'owner self-update opened protected path '+path)
  finally:tmp.cleanup()
 tmp,root,c=fixture(m)
 try:
  c['execution_mode']='provider';c['provider_authorized']=False
  rejects(lambda:m.Supervisor(root/'owner',c),'provider accepted without new owner grant')
 finally:tmp.cleanup()

def idle_stop_latency(m):
 tmp,root,c=fixture(m)
 try:
  s=m.Supervisor(root/'owner',c);errors=[]
  def foreground():
   other=None
   try:
    other=m.Supervisor(root/'owner');other.loop(max_cycles=100)
   except BaseException as error:errors.append(str(error))
   finally:
    if other is not None:close(other)
  thread=threading.Thread(target=foreground);thread.start();time.sleep(.25)
  started=time.monotonic();s.set_stop(True);thread.join(timeout=1.1)
  require(not thread.is_alive(),'idle stop response exceeded one second')
  require(time.monotonic()-started<1.1,'idle stop latency')
  require(not errors,'idle loop failed: '+str(errors))
  require(meta(s)['stopped'] and meta(s)['calls']==0,'idle stop did not persist or invented calls')
  close(s)
 finally:tmp.cleanup()


def default_runner_protection(m):
 from agent_workflow.runner import validate_config
 tmp,root,c=fixture(m)
 try:
  child={key:c[key] for key in ('repo','backend','checks','tests','budget','allowlist','poll_seconds')}
  child.update(project=c['project'],backend_adapter=c['backend_adapter'],self_update=c['self_update'],strategy=None)
  rejects(lambda:validate_config(child),'ordinary caller gained self-update through extended config')
 finally:tmp.cleanup()

run('p4-evolution','evolution',[
 ('persistent_policy_calls_deadline_stop',policy_and_persistence),('pair_admission_no_mutation',pair_admission),
 ('persistent_block_and_absolute_deadline',blocks_and_deadline),('empty_and_stopped_cycles',no_task_cycle),
 ('immutable_stable_trusted_and_history',stable_and_trusted_drift),('explicit_owner_permissions',protected_paths),('interruptible_idle_stop',idle_stop_latency),('default_runner_proof_required',default_runner_protection)])
