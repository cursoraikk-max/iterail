"""Normal callback completion still must respect late owner stop before schedule commit."""
import ast,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'phase5/trusted'))
from common import run,require
base=Path(__file__).resolve().parents[1]/'trusted/check-v2.py';tree=ast.parse(base.read_text());tree.body=tree.body[:-1];h={'__file__':str(base)};exec(compile(tree,str(base),'exec'),h)

def normal_callback_stop(m):
 live=[True];imported={}
 def sink(p):h['inert'](p);imported.setdefault(p['external_key'],p);live[0]=False
 tmp,root,src,p,clock,s=h['setup'](m,sink=sink,gate=lambda:live[0])
 try:
  before=s.status();require(s.tick()['phase']=='paused','normal callback late stop committed cadence');after=s.status();require(len(imported)==1 and after['next_due']==before['next_due'] and after['failure_count']==before['failure_count'],'normal callback late stop advanced due/backoff or lost import');h['done'](s);s=m.UIUXMaintenance(root/'state',p,src,task_sink=sink,clock=lambda:clock[0],gate=lambda:live[0]);require(s.tick()['phase']=='paused' and len(imported)==1,'restart bypassed closed ownergate')
 finally:h['done'](s);tmp.cleanup()

run('p6-uiux-postexport-stop','uiux_maintenance',[('normal_callback_gate_closure_no_schedule_commit',normal_callback_stop)])
