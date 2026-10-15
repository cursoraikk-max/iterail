"""Frozen native failure-evidence gate. No provider calls or socket probes."""
import hashlib,json,sqlite3,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'phase4/trusted'))
from common import run,require,rejects,command,commit,source_digest
SECRET='PHASE14_PRIVATE_CANARY_NEVER_EXPORT_7c9f86a55dc5'

def make_fixture(root):
 root.mkdir(parents=True,exist_ok=True);state=root/'state';logs=state/'logs';logs.mkdir(parents=True)
 project=root/'project';project.mkdir();(project/'app.sh').write_text('echo '+SECRET+'\nexit 1\n');command(['git','init','-q',str(project)]);commit(project)
 head=command(['git','-C',str(project),'rev-parse','HEAD']);(project/'app.sh').write_text('echo '+SECRET+'\nexit 0\n')
 ident='call-0123456789ab';raw=(json.dumps({'type':'thread.started','thread_id':SECRET})+'\n'+json.dumps({'type':'item.completed','item':{'type':'command_execution','aggregated_output':SECRET,'exit_code':0}})+'\n'+SECRET+'\n').encode();log=logs/(ident+'.log');log.write_bytes(raw)
 pid={'pid':123456,'guardian':123455,'started':100.0,'call_id':ident};(logs/(ident+'.pid')).write_text(json.dumps(pid))
 terminal={'call_id':ident,'child_pid':pid['pid'],'guardian_pid':pid['guardian'],'started':100.0,'duration':2.0,'total_deadline':102.0,'exit_code':124,'returncode':-9,'reason':'timeout','group_termination_confirmed':True,'cleanup_errors':[],'output':str(log),'output_bytes':len(raw),'saved_bytes':len(raw),'truncated':False,'output_sha256':hashlib.sha256(raw).hexdigest(),'saved_sha256':hashlib.sha256(raw).hexdigest(),'cwd':str(project),'argv':['echo',SECRET]}
 d=sqlite3.connect(state/'runner.sqlite');d.executescript('CREATE TABLE calls(id TEXT,job_id TEXT,kind TEXT,phase TEXT,until REAL,result TEXT);CREATE TABLE events(seq INTEGER,at REAL,job_id TEXT,kind TEXT,data TEXT);');d.execute('INSERT INTO calls VALUES(?,?,?,?,?,?)',(ident,'job-0123456789ab','backend:worker','finished',102.0,json.dumps(terminal)));d.execute('INSERT INTO events VALUES(?,?,?,?,?)',(1,100.0,'job-0123456789ab','process_started',json.dumps({'call':ident,'guardian_pid':pid['guardian'],'token':SECRET})));d.execute('INSERT INTO events VALUES(?,?,?,?,?)',(2,102.0,'job-0123456789ab','integration_intent',json.dumps({'token':SECRET,'expected':head})));d.commit();d.close()
 core=root/'core';core.mkdir();d=sqlite3.connect(core/'tasks.sqlite');d.executescript('CREATE TABLE runs(id TEXT,task_id TEXT,worker TEXT,token TEXT,state TEXT,started REAL,lease_until REAL,deadline REAL,policy TEXT,revision INTEGER,workflow TEXT,baseline TEXT,candidate TEXT,evidence TEXT);CREATE TABLE events(seq INTEGER,at REAL,kind TEXT,task_id TEXT,run_id TEXT,data TEXT);');d.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('run-0123456789ab','task-0123456789ab',SECRET,SECRET,'running',100.0,130.0,140.0,json.dumps({'secret':SECRET}),1,'a'*64,'b'*64,None,json.dumps({'secret':SECRET})));d.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(1,100.0,'claimed','task-0123456789ab','run-0123456789ab',json.dumps({'token':SECRET})));d.commit();d.close()
 (root/'arbitrary-private.json').write_text(json.dumps({'token':SECRET,'password':SECRET}));return project,head,ident,log

def artifact(result,out):
 require(isinstance(result,dict) and 'artifact' in result,'missing artifact result');p=Path(result['artifact']);require(p.is_file() and p.resolve().is_relative_to(out.resolve()),'artifact missing or escaped output');return p,json.loads(p.read_bytes())
def safe_output(out):
 files=[p for p in out.rglob('*') if p.is_file()];require(files and all(p.suffix=='.json' and SECRET.encode() not in p.read_bytes() for p in files),'raw database/log/secret exported');return files

def projection(m):
 with tempfile.TemporaryDirectory(prefix='p14-projection-') as name:
  root=Path(name);fixture=root/'fixture';out=root/'out';project,head,ident,log=make_fixture(fixture)
  before={str(p.relative_to(fixture)):hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.rglob('*') if p.is_file()}
  result=m.retain_failure(fixture,out,error=AssertionError(SECRET),initial_git={str(project):head});path,data=artifact(result,out);safe_output(out)
  require(data['schema_version']==1 and all(isinstance(data[k],list) for k in ('runner_calls','core_runs','core_events','git','cycles')),'projection schema absent')
  call=next(c for c in data['runner_calls'] if c.get('kind')=='backend:worker');require(call['exit_code']==124 and call['reason']=='timeout' and call['started']==100.0 and call['duration']==2.0 and call['total_deadline']==102.0 and call['pid_identity_verified'] is True,'terminal/PID evidence lost');require(call['cwd_sha256']==hashlib.sha256(str(project).encode()).hexdigest() and call['log_sha256']==hashlib.sha256(log.read_bytes()).hexdigest() and call['log_bytes']==log.stat().st_size,'cwd/log evidence unbound')
  core=next(c for c in data['core_runs'] if c.get('state')=='running');require(core['started']==100.0 and core['lease_until']==130.0 and core['deadline']==140.0,'Core lease evidence lost');require(data['core_events'],'Core event evidence missing')
  git=next(g for g in data['git'] if g.get('initial_head')==head);require(git['final_head']==head and git['status_entries']>=1 and isinstance(git['cas_events'],list),'Git HEAD/status/CAS evidence absent')
  require(before=={str(p.relative_to(fixture)):hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.rglob('*') if p.is_file()},'projection changed raw fixture')
  first=path.read_bytes();again=m.retain_failure(fixture,out,error=AssertionError(SECRET),initial_git={str(project):head});other,_=artifact(again,out);require(other!=path and path.read_bytes()==first,'second capture overwrote first');safe_output(out)

def lifecycle(m):
 with tempfile.TemporaryDirectory(prefix='p14-lifecycle-') as name:
  out=Path(name)/'out';seen=[]
  def good(root):
   root=Path(root);seen.append(root);(root/'private.txt').write_text(SECRET)
  result=m.run_fixture(good,out);require(result['passed'] is True and result.get('artifact') is None and not seen[0].exists(),'success fixture/artifact not cleaned');require(not out.exists() or not list(out.iterdir()),'success emitted failure evidence')
  def bad(root):
   root=Path(root);seen.append(root);make_fixture(root);raise AssertionError(SECRET)
  result=m.run_fixture(bad,out);path,data=artifact(result,out);require(result['passed'] is False and not seen[1].exists() and data['schema_version']==1,'failure raw root not cleaned after capture');safe_output(out)

def boundaries(m):
 with tempfile.TemporaryDirectory(prefix='p14-bounds-') as name:
  root=Path(name);fixture=root/'fixture';out=root/'out';project,head,ident,log=make_fixture(fixture);outside=root/'outside';outside.mkdir();sentinel=outside/'secret.txt';sentinel.write_text(SECRET)
  (fixture/'escape').symlink_to(sentinel);rejects(lambda:m.retain_failure(fixture,out,error=AssertionError('failure')),'descendant symlink followed');(fixture/'escape').unlink()
  linked=root/'linked';linked.symlink_to(fixture,target_is_directory=True);rejects(lambda:m.retain_failure(linked,out,error=AssertionError('failure')),'input-root symlink followed')
  linkout=root/'linkout';linkout.symlink_to(outside,target_is_directory=True);rejects(lambda:m.retain_failure(fixture,linkout,error=AssertionError('failure')),'output-root symlink followed')
  rejects(lambda:m.retain_failure(fixture,fixture/'output',error=AssertionError('failure')),'overlapping output accepted');rejects(lambda:m.retain_failure(fixture,out,error=AssertionError('failure'),initial_git={str(outside):head}),'escaped initial Git followed')
  rejects(lambda:m.retain_failure(fixture,out,error=AssertionError('failure'),max_bytes=1),'meaningless byte budget accepted');rejects(lambda:m.retain_failure(fixture,out,error=AssertionError('failure'),max_files=0),'zero file budget accepted')
  # A stored output reference is untrusted data; it must never cause an outside read.
  d=sqlite3.connect(fixture/'state/runner.sqlite');term=json.loads(d.execute('SELECT result FROM calls').fetchone()[0]);term['output']=str(sentinel);d.execute('UPDATE calls SET result=?',(json.dumps(term),));d.commit();d.close();read=Path.read_bytes
  def bounded(p,*a,**k):
   require(p.resolve()!=sentinel.resolve(),'escaped terminal log read');return read(p,*a,**k)
  with patch('pathlib.Path.read_bytes',bounded):result=m.retain_failure(fixture,out,error=AssertionError('failure'))
  _,data=artifact(result,out);safe_output(out);require((not data['runner_calls']) or data['runner_calls'][0].get('pid_identity_verified') is not True,'escaped log became verified proof')
  # Size/file limits may reject, or retain a genuinely bounded explicitly incomplete record.
  for kwargs in ({'max_files':1},{'max_bytes':2048}):
   limited=root/('limited-'+next(iter(kwargs)))
   try:
    result=m.retain_failure(fixture,limited,error=AssertionError(SECRET),**kwargs);p,data=artifact(result,limited);files=safe_output(limited);require(len(files)<=kwargs.get('max_files',64) and sum(f.stat().st_size for f in files)<=kwargs.get('max_bytes',262144) and data.get('complete') is False,'limit produced oversized or falsely complete evidence')
   except (ValueError,TypeError,OSError,RuntimeError):
    require(not limited.exists() or not any(p.is_file() for p in limited.rglob('*')),'rejected bounded capture left partial files')
  require(sentinel.read_text()==SECRET,'outside sentinel changed')

def native_timeout(m):
 from common import REPO
 with tempfile.TemporaryDirectory(prefix='p14-native-') as name:
  out=Path(name)/'out';trusted=ROOT/'phase4/trusted';pins={n:hashlib.sha256((trusted/n).read_bytes()).hexdigest() for n in ('target.py','common.py','native_backend.py')};before=source_digest(REPO)
  result=m.run_native_target(trusted,REPO,out,delay_seconds=3);path,data=artifact(result,out);require(result['passed'] is False,'injected timeout passed original target assertion');safe_output(out)
  worker=next(c for c in data['runner_calls'] if c.get('kind')=='backend:worker');require(worker['exit_code']==124 and worker['reason']=='timeout' and worker['pid_identity_verified'] is True and worker['log_bytes']>0,'native failure reason/PID/raw observation absent');require(not any(c.get('kind')=='backend:reviewer' for c in data['runner_calls']),'timeout dispatched native reviewer')
  require(any(c.get('phase')=='failed' and c.get('failure_class')=='backend_failure' for c in data['cycles']),'cycle result not retained before CAS assertion');require(any(g.get('initial_head')==g.get('final_head') and g.get('initial_head') for g in data['git']),'unchanged target HEAD not captured');require(before==source_digest(REPO) and all(hashlib.sha256((trusted/n).read_bytes()).hexdigest()==v for n,v in pins.items()),'subject or protected original target changed');require(all(p.suffix=='.json' for p in out.rglob('*') if p.is_file()),'raw fixture exported')

run('iterail-failure-evidence-retention','diagnostics',[('allowlisted_projection_and_unique_records',projection),('deterministic_success_and_failure_cleanup',lifecycle),('bounds_secret_canaries_symlinks_and_path_escape',boundaries),('unchanged_native_target_timeout_retains_causal_evidence',native_timeout)])
