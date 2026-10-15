#!/usr/bin/env python3
"""Native fake actor: authentic workflow/release path, no provider or product improvement."""
import json,subprocess,sys
from pathlib import Path
kind,repo,slot,verdict,mode=sys.argv[1:6];repo=Path(repo);slot=Path(slot);spec=json.loads((slot/'job.json').read_text());sys.path.insert(0,spec['controller'])
from agent_workflow.core import digest
print(json.dumps({'type':'thread.started','thread_id':'native-release-'+kind}),flush=True);print(json.dumps({'type':'turn.started'}),flush=True)
def ctl(action,args=None):
 argv=[sys.executable,str(slot/'jobctl.py'),action]
 if args is not None:argv.append(json.dumps(args))
 p=subprocess.run(argv,cwd=repo,capture_output=True,text=True);print(json.dumps({'type':'item.completed','item':{'type':'command_execution','aggregated_output':p.stdout+p.stderr,'exit_code':p.returncode}}),flush=True)
 if p.returncode:raise SystemExit(p.returncode)
 return json.loads(p.stdout)
if kind=='worker':
 ctl('claim');ctl('progress',{'note':'Synthetic native controller transition only, no provider/model'});r=ctl('baseline')
 if r.get('result',{}).get('passed') is not False:raise SystemExit('real failed native baseline required')
 path=repo/'agent_workflow/runner.py';text=path.read_text();marker='#NATIVE_RELEASE_B' if '#NATIVE_RELEASE_B' not in text else '#NATIVE_RELEASE_C';path.write_text(text+'\n'+marker+'\n');ctl('change')
elif kind=='reviewer':
 approve=not (mode=='reject_first' and slot.name=='1');Path(verdict).write_text(json.dumps({'approved':approve,'candidate':digest(repo),'reason':'Explicit native fixture reject-first policy' if not approve else 'Independent native fixture marker/control review'}))
else:raise SystemExit('unknown role')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':0,'output_tokens':0}}),flush=True)
