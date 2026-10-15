"""Trusted fixed fixtures. Task/source feedback cannot supply code or policy."""
import argparse, json, pathlib, subprocess, sys, tempfile
p=argparse.ArgumentParser();p.add_argument('check',choices=['import-body','boolean-policy']);p.add_argument('--repo',required=True);a=p.parse_args()
repo=pathlib.Path(a.repo).resolve();sys.path.insert(0,str(repo))
from agent_workflow.core import digest
before=digest(repo)
infrastructure=[]
with tempfile.TemporaryDirectory() as d:
 root=pathlib.Path(d);fixture=root/'repo';fixture.mkdir();subprocess.run(['git','init','-q',str(fixture)],check=True,capture_output=True);(fixture/'code.py').write_text('x=1\n')
 c={'repo':str(fixture),'tests':[[sys.executable,'-c','print(1)']],'sources':['local'],'budget':{'lease_seconds':20,'run_seconds':30,'test_seconds':10,'max_attempts':2},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'publish':False,'network':False}}
 def cli(state,action,args=None,config=None):
  cmd=[sys.executable,'-m','agent_workflow','--state',str(state)]
  if config:cmd+=['--config',str(config)]
  cmd+=[action]
  if args is not None:cmd+=['--args',json.dumps(args)]
  try:
   r=subprocess.run(cmd,cwd=repo,capture_output=True,text=True,timeout=15)
  except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
   infrastructure.append({'action':action,'error':type(exc).__name__,'detail':str(exc)})
   return {'command':cmd,'exit_code':None,'stdout':'','stderr':str(exc),'parsed':None}

  try:out=json.loads(r.stdout)
  except ValueError:out=None
  if not (isinstance(out,dict) and type(out.get('ok')) is bool):
   # Only this specific known SQLite binding failure is a product finding.
   known_binding = action=='import' and isinstance((args or {}).get('body'),dict) and r.returncode==1 and 'sqlite3.ProgrammingError: Error binding parameter 5' in r.stderr
   if not known_binding: infrastructure.append({'action':action,'error':'invalid CLI envelope','exit_code':r.returncode})
  elif (r.returncode==0) != out['ok']:
   infrastructure.append({'action':action,'error':'inconsistent exit/envelope'})
  return {'command':cmd,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'parsed':out}
 cp=root/'config.json';cp.write_text(json.dumps(c));observed=[]
 if a.check=='import-body':
  state=root/'state';init=cli(state,'init',config=cp);
  if init['exit_code']!=0: infrastructure.append({'action':'init','error':'valid fixture rejected'})
  for body in ({'feedback':'data'},123):
   r=cli(state,'import',{'external_key':str(type(body).__name__),'title':'Fixture','body':body});observed.append(r)
  status=cli(state,'status');observed.append(status)
  passed=all(r['exit_code']!=0 and isinstance(r['parsed'],dict) and r['parsed'].get('ok') is False for r in observed[:2]) and isinstance(status['parsed'],dict) and isinstance(status['parsed'].get('result'),dict) and status['parsed']['result'].get('tasks')==[]
  valid=cli(state,'import',{'external_key':'valid','title':'Valid fixture','body':'Untrusted but textual feedback'});observed.append(valid)
  passed=passed and valid['exit_code']==0 and isinstance(valid['parsed'],dict) and valid['parsed'].get('ok') is True
  title='Reject non-string imported body consistently with structured CLI errors'
  repair='Validate body as a string in shared import core before SQLite writes; CLI must emit ok:false for invalid JSON field types; preserve valid imports and MCP parity.'
 else:
  valid=cli(root/'valid','init',config=cp);
  if valid['exit_code']!=0: infrastructure.append({'action':'init','error':'valid fixture rejected'})
  for field in ('acceptance','permissions'):
   bad=json.loads(json.dumps(c));bad[field]={k:int(v) for k,v in bad[field].items()};cp.write_text(json.dumps(bad));r=cli(root/field,'init',config=cp);observed.append(r)
  passed=all(r['exit_code']!=0 and isinstance(r['parsed'],dict) and r['parsed'].get('ok') is False for r in observed)
  title='Require actual boolean values in acceptance and permissions config'
  repair='Reject integer 0/1 and other non-booleans in mandatory acceptance/permission fields while preserving exact approved boolean policy; do not relax allowed policy values.'
 after=digest(repo)
 report={'id':'next-'+a.check,'title':title,'repair':repair,'exactsourceSHA':before,'source_stable':before==after,'passed':passed and before==after and not infrastructure,'classification':'infrastructure_error' if infrastructure or before!=after else ('passed' if passed else 'product_defect'),'infrastructure_errors':infrastructure,'evidence':observed}
 print(json.dumps(report,sort_keys=True));sys.exit(2 if report['classification']=='infrastructure_error' else (0 if report['passed'] else 1))
