#!/usr/bin/env python3
"""Explicit local fake actor for fixed control fixtures, never a provider/model."""
import hashlib, json, subprocess, sys
from pathlib import Path
kind,repo,slot,verdict=sys.argv[1:5];repo=Path(repo);slot=Path(slot)
spec=json.loads((slot/'job.json').read_text());sys.path.insert(0,spec['controller'])
from agent_workflow.core import digest
print(json.dumps({'type':'thread.started','thread_id':'native-fixture-'+kind}),flush=True)
print(json.dumps({'type':'turn.started'}),flush=True)

def ctl(action,args=None):
 argv=[sys.executable,str(slot/'jobctl.py'),action]
 if args is not None:argv.append(json.dumps(args))
 p=subprocess.run(argv,cwd=repo,capture_output=True,text=True)
 print(json.dumps({'type':'item.completed','item':{'type':'command_execution','aggregated_output':p.stdout+p.stderr,'exit_code':p.returncode}}),flush=True)
 if p.returncode:raise SystemExit(p.returncode)
 return json.loads(p.stdout)

if kind=='worker':
 ctl('claim');ctl('progress',{'note':'Native fixture actor, no provider/model'})
 baseline=ctl('baseline')
 if baseline.get('result',{}).get('passed') is not False:raise SystemExit('fixture requires real failed machine baseline')
 path=repo/'app.sh';text=path.read_text()
 if 'exit 1' not in text:raise SystemExit('fixture baseline defect missing')
 path.write_text(text.replace('exit 1','exit 0'))
 ctl('change')
elif kind=='reviewer':
 p=subprocess.run(['sh',str(repo/'app.sh')],cwd=repo,capture_output=True,text=True)
 Path(verdict).write_text(json.dumps({'approved':p.returncode==0,'candidate':digest(repo),'reason':'Independent local native fixture actor checked shell exit; no model verdict'}))
else:raise SystemExit('unknown fixture actor role')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':0,'output_tokens':0}}),flush=True)
