"""Second clean non-Python project fixed full native closed-loop fixture."""
import json, shutil, sqlite3, sys
from pathlib import Path
from common import run, fixture, require, meta, close, command, commit, source_digest

CHECK=r'''import hashlib,json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
def digest(root):
 h=hashlib.sha256()
 for p in sorted(root.rglob('*')):
  rel=p.relative_to(root)
  if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or p.suffix=='.pyc':continue
  if p.is_symlink():raise ValueError('symlink')
  if p.is_file():h.update(str(rel).encode()+b'\0'+p.read_bytes()+b'\0')
 return h.hexdigest()
before=digest(root);p=subprocess.run(['sh',str(root/'app.sh')],cwd=root,capture_output=True,text=True);after=digest(root)
r={'id':'fixture-check','passed':p.returncode==0,'classification':'passed' if p.returncode==0 else 'product_defect','source_stable':before==after,'exactsourceSHA':before,'evidence':[{'argv':['sh','app.sh'],'exit_code':p.returncode,'evidence_kind':'native_fixture'}]}
print(json.dumps(r));raise SystemExit(0 if r['passed'] else 1)
'''

def native_target(m):
 tmp,root,c=fixture(m,wall=600)
 try:
  # A separate clean project contains only shell and text, no controller package.
  target=root/'shell-project';target.mkdir();(target/'app.sh').write_text('#!/bin/sh\necho local-native-target\nexit 1\n')
  (target/'README.txt').write_text('Native fixture project, no actual provider or model.\n')
  command(['git','init','-q',str(target)]);commit(target,'native shell fixture baseline')
  check=Path(c['checks'][0]['argv'][1]);check.write_text(CHECK)
  backend=Path(c['backend']);backend.write_text('#!'+sys.executable+'\n'+(Path(__file__).parent/'native_backend.py').read_text());backend.chmod(0o700)
  c['repo']=str(target);c['project']={'id':'native-shell-target','kind':'target'};c['self_update']=[];c['allowlist']=['app.sh']
  s=m.Supervisor(root/'owner',c);before=meta(s);head=command(['git','-C',str(target),'rev-parse','HEAD']);source=source_digest(target)
  require(not (target/'agent_workflow').exists(),'target fixture accidentally contains controller')
  result=s.cycle()
  require(isinstance(result,dict),'step result is untyped')
  require(command(['git','-C',str(target),'rev-parse','HEAD'])!=head,'native target did not integrate local CAS')
  require(source_digest(target)!=source and 'exit 0' in (target/'app.sh').read_text(),'native target candidate was not actually repaired')
  require(meta(s)['active']==before['active'] and meta(s)['epoch']==before['epoch'],'target fix activated a controller release')
  require(meta(s)['calls']==2,'native worker/reviewer pair not charged exactly once')
  require(not (target/'agent_workflow').exists(),'target integration polluted project with controller')
  states=list((root/'owner').rglob('tasks.sqlite'));require(states,'native target missing actual Core state')
  accepted=[];baseline_failures=[]
  for state in states:
   db=sqlite3.connect('file:'+str(state)+'?mode=ro',uri=True)
   try:
    accepted+=db.execute("SELECT id FROM runs WHERE state='accepted'").fetchall()
    baseline_failures+=db.execute("SELECT data FROM events WHERE kind='verification_failed'").fetchall()
   finally:db.close()
  require(len(accepted)==1,'fixture did not traverse exactly one actual accepted Core run')
  require(any(json.loads(row[0]).get('baseline')==source==json.loads(row[0]).get('candidate') for row in baseline_failures),'exact failed baseline missing')
  receipts=list((root/'owner').rglob('receipts/*.json'));require(receipts,'actual machine/review/CAS completion receipt missing')
  # Next empty cycle runs the same trusted active tool; it must not invent work.
  s.cycle();require(meta(s)['calls']==2,'empty follow-up dispatched new backend')
  close(s)
 finally:tmp.cleanup()

run('p4-target','evolution',[('independent_non_python_target_closed_loop',native_target)])
