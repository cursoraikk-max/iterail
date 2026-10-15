"""Trusted SO002 actual local subprocess fixtures, no backend/provider calls."""
import argparse,json,os,subprocess,sys,tempfile,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);a=p.parse_args();repo=Path(a.repo).resolve();sys.path.insert(0,str(repo))
from agent_workflow.core import digest
from agent_workflow.runner import RunnerError
before=digest(repo);cases=[]
def require(v,msg='assertion failed'):
 if not v:raise AssertionError(msg)
def case(name,fn):
 try:fn();cases.append({'case':name,'exit_code':0})
 except Exception as e:cases.append({'case':name,'exit_code':1,'error':type(e).__name__+': '+str(e)})
def guard(mode,checkpoints=(),profile=True,eof=False,total=3):
 with tempfile.TemporaryDirectory(prefix='so002-guardian-') as t:
  root=Path(t);spec={'argv':[sys.executable,'-u','-c','import time;\nwhile True: print("heartbeat token tests:pass",flush=True); time.sleep(.04)'],'cwd':t,'seconds':total,'bytes':1024,'output':str(root/'out'),'result':str(root/'result.json'),'call_id':'native-trusted-call'}
  if profile:spec.update(timeouts={'startup_seconds':1,'idle_seconds':1,'max_progress_extensions':3},startup_mode=mode,total_cutoff=time.time()+total)
  (root/'spec.json').write_text(json.dumps(spec));env=dict(os.environ,PYTHONPATH=str(repo))
  proc=subprocess.Popen([sys.executable,'-m','agent_workflow.guardian',str(root/'spec.json')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
  start=time.monotonic()
  try:
   for delay,stage,seq in checkpoints:
    time.sleep(max(0,delay-(time.monotonic()-start)));proc.stdin.write((json.dumps({'type':'checkpoint','stage':stage,'seq':seq})+'\n').encode());proc.stdin.flush()
   if eof:proc.stdin.close()
   proc.wait(timeout=total+4);require(proc.returncode==0,proc.stderr.read().decode());r=json.loads((root/'result.json').read_text())
   require(r['duration']<=total+1,'hard total extended');require(r.get('group_termination_confirmed') is True,'group cleanup not confirmed');require(r.get('call_id')=='native-trusted-call');return r
  finally:
   if proc.poll() is None:proc.kill();proc.wait()
   if proc.stdin and not proc.stdin.closed:proc.stdin.close()
   proc.stdout.close();proc.stderr.close()
def startup():
 r=guard('claim');require(r['reason']=='startup_timeout' and r['exit_code']==124)
def idle():
 r=guard('process_spawn');require(r['reason']=='idle_timeout' and r['exit_code']==124)
def total():
 r=guard('claim',[(.2,0,1),(.8,1,2),(1.5,2,3),(2.2,3,4)])
 require(r['reason']=='total_timeout' and r['exit_code']==124);require(r['progress_extensions']==3)
def replay():
 r=guard('claim',[(.2,0,1),(.5,1,2),(.8,1,3),(1.1,1,4)])
 require(r['reason']=='idle_timeout');require(r['progress_extensions']==1,'replayed checkpoint renewed deadline')
def eof():
 r=guard('claim',eof=True);require(r['exit_code']==125 and r['reason']=='parent_eof')
for name,fn in [('stdout_spam_never_completes_claim_startup',startup),('stdout_spam_never_renews_idle',idle),('trusted_monotonic_checkpoints_never_extend_total',total),('replay_stage_never_renews_idle',replay),('parent_eof_stops_group',eof)]:case(name,fn)
def recovery():
 from tests.test_runner import RunnerTests
 x=RunnerTests();x.setUp()
 try:
  r=x.r;r.db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?)',('missing','fixture','backend:worker','running',time.time()-1,None))
  try:r.recover()
  except RunnerError:pass
  else:raise AssertionError('missing terminal identity permits redispatch')
  require(r.status()['meta']['calls']==0)
 finally:x.doCleanups()
case('missing_terminal_receipt_blocks_recovery',recovery)
def dirty():
 from tests.test_runner import RunnerTests
 from agent_workflow.runner import git
 x=RunnerTests();x.setUp()
 try:
  r=x.r;r.discover();j=r.status()['jobs'][0];head=git(x.repo,'rev-parse','HEAD');r.db.execute('INSERT INTO integration VALUES(?,?,?,?)',(j['id'],head,head,'intent'))
  (x.repo/'core.py').write_text('USER CHANGE MUST SURVIVE\n');old=digest(x.repo)
  try:r.recover()
  except RunnerError:pass
  require(digest(x.repo)==old,'recovery discarded dirty source')
  require(r.db.execute('SELECT phase FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]=='conflict')
 finally:x.doCleanups()
case('after_cas_dirty_tree_preserved_conflict',dirty)
after=digest(repo);passed=all(x['exit_code']==0 for x in cases) and before==after
print(json.dumps({'id':'so002-trusted','evidence_mode':'native-subprocess-fixture','backend_calls':0,'source_digest':before,'source_stable':before==after,'passed':passed,'cases':cases}));sys.exit(0 if passed else 1)
