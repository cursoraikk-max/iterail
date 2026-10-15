"""Independent native fixtures for real existing auth/metrics feature gaps."""
import hashlib,json,sys
from pathlib import Path
from common import fixture,command,commit
HERE=Path(__file__).resolve().parent

def target(m,*,failure=None):
 tmp,root,c=fixture(m,max_calls=8,wall=600);project=root/'target';project.mkdir();(project/'app.sh').write_text('#!/bin/sh\nexit 1\n');command(['git','init','-q',str(project)]);commit(project);Path(c['checks'][0]['argv'][1]).write_text((HERE/'target_check.py').read_text());backend=Path(c['backend'])
 if failure is None:body=(HERE/'native_backend.py').read_text()
 else:
  message={'auth':'Authentication failed','quota':'Usage limit reached','unknown':'NATIVE_UNCLASSIFIED_BACKEND_FAILURE'}[failure]
  body='import json,sys\nprint(json.dumps({"type":"thread.started","thread_id":"native-error-fixture"}),flush=True)\nprint(json.dumps({"type":"turn.started"}),flush=True)\nprint(json.dumps({"type":"error","message":'+repr(message)+',"token":"NATIVE_PRIVATE_SENTINEL"}),flush=True)\nraise SystemExit(1)\n'
 backend.write_text('#!'+sys.executable+'\n'+body);backend.chmod(0o700);c.update(repo=str(project),project={'id':'native-future-target','kind':'target'},allowlist=['app.sh'],self_update=[]);c['budget']['call_seconds']=5
 return tmp,root,c

def controller(m):
 tmp,root,c=fixture(m,max_calls=8,wall=600);script=Path(c['checks'][0]['argv'][1]);script.write_text((HERE/'controller_check.py').read_text());backend=Path(c['backend']);backend.write_text('#!'+sys.executable+'\n'+(HERE/'controller_backend.py').read_text());backend.chmod(0o700);c['backend_adapter']['argv'].append('normal');c['checks']=[{'id':ident,'argv':[sys.executable,str(script),'{repo}',ident],'repair':'Synthetic native fixture '+ident,'finding_contract':'strict-v1'} for ident in ('native-b','native-c')];c['tests']=[[sys.executable,str(script),'{repo}','native-compile']];c['health']=[[sys.executable,str(script),'{repo}','native-compile']];c['allowlist']=['agent_workflow/runner.py'];c['self_update']=['agent_workflow/runner.py'];c['budget']['call_seconds']=5
 return tmp,root,c
