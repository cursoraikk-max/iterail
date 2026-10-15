"""Trusted SO001 contract tests: native fixtures, zero external backend calls."""
import argparse,copy,hashlib,importlib,json,os,subprocess,sys,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);a=p.parse_args();repo=Path(a.repo).resolve();sys.path.insert(0,str(repo))
from agent_workflow.core import digest
before=digest(repo);cases=[]
def case(name,fn):
 try:fn();cases.append({'case':name,'exit_code':0})
 except Exception as e:cases.append({'case':name,'exit_code':1,'error':type(e).__name__+': '+str(e)})
def require(v,msg='assertion failed'):
 if not v:raise AssertionError(msg)
def rejects(fn):
 try:fn()
 except (ValueError,TypeError):return
 raise AssertionError('invalid evidence accepted')
def canon(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
try:m=importlib.import_module('agent_workflow.receipt')
except ImportError as e:
 cases.append({'case':'receipt_module_exists','exit_code':1,'error':str(e)});m=None
if m:
 def events():
  stream=[{'type':'thread.started','thread_id':'fixture-thread'},{'type':'turn.started'},{'type':'item.completed','item':{'type':'agent_message','text':'tests:pass'}},{'type':'turn.completed','usage':{'input_tokens':1}}]
  r=m.normalize_exec_events('\n'.join(map(json.dumps,stream)))
  require(r['protocol']=='exec-v1' and r['valid'] is True and r['model_completed'] is True and r['thread_ids']==['fixture-thread'])
  require(not r.get('accepted',False),'model event grants acceptance')
  for bad in [[stream[-1]],stream+[stream[-1]],stream+[{'type':'unknown.trust-me'}],stream+[stream[0]]]:
   require(m.normalize_exec_events('\n'.join(map(json.dumps,bad)))['valid'] is False)
  require(m.normalize_exec_events(json.dumps(stream[0])+'\n{"type":"turn.completed"')['valid'] is False)
  require(not m.normalize_exec_events('tests:pass')['model_completed'])
 case('versioned_exec_order_duplicate_unknown_truncation',events)
 def manifest_and_receipt():
  with tempfile.TemporaryDirectory(prefix='so001-trusted-') as t:
   r=Path(t)/'repo';r.mkdir()
   def git(*v):return subprocess.check_output(['git','-C',str(r),*v],text=True).strip()
   git('init','-q');(r/'old.txt').write_text('move me\n');(r/'delete.txt').write_text('delete\n');(r/'binary.bin').write_bytes(b'\x00old');git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','base');head=git('rev-parse','HEAD');baseline=digest(r)
   (r/'old.txt').rename(r/'new.txt');(r/'delete.txt').unlink();(r/'binary.bin').write_bytes(b'\x00new');(r/'fresh.txt').write_text('untracked\n');git('add','-A')
   manifest=m.diff_manifest(r,head,max_bytes=1048576)
   require(manifest['complete'] and not manifest['truncated']);paths={x['path'] for x in manifest['entries']};require({'new.txt','delete.txt','binary.bin','fresh.txt'}<=paths);require(any(x.get('binary') is True for x in manifest['entries'] if x['path']=='binary.bin'))
   require(any(x.get('old_path')=='old.txt' for x in manifest['entries']),'rename identity absent')
   small=m.diff_manifest(r,head,max_bytes=1);require(not small['complete'] and small['truncated'])
   (r/'bad-link').symlink_to('fresh.txt');rejects(lambda:m.diff_manifest(r,head));(r/'bad-link').unlink()
   candidate=digest(r);h='a'*64;policy={'acceptance':{'require_tests':True},'permissions':{'publish':False}};ph=canon(policy)
   b={'task_id':'task-fixture','run_id':'run-fixture','baseline':baseline,'base_head':head,'candidate':candidate,'head':head,'workflow':h,'runtime':h,'policy':ph,'checker':h,'manifest':manifest,'reviewer':{'approved':True,'candidate':candidate,'version':'native-fixture','log_sha256':h},'tests':[{'argv':['python','fixture.py'],'exit_code':0,'log_sha256':h}],'cas':{'phase':'done','expected':head,'candidate':head},'core_state':{'state':'accepted','evidence':{'baseline':baseline,'candidate':candidate,'workflow':h,'policy':policy},'policy_sha256':ph},'main_digest':candidate,'backend':{'protocol':'exec-v1','valid':True,'version':'codex-exec','model_completed':True}}
   require(m.build_receipt(b)['complete'] is True)
   for key in b:
    z=copy.deepcopy(b);z.pop(key);rejects(lambda z=z:m.build_receipt(z))
   mutations=[('tests', [{'argv':['python'],'exit_code':1,'log_sha256':h}]),('manifest',small),('main_digest','b'*64),('policy','b'*64),('candidate','b'*64),('reviewer',{'approved':False,'candidate':candidate,'version':'native','log_sha256':h}),('cas',{'phase':'intent','expected':head,'candidate':head}),('core_state',{'state':'verified','evidence':b['core_state']['evidence'],'policy_sha256':ph}),('backend',{'protocol':'exec-v1','valid':False,'version':'codex-exec','model_completed':True})]
   for k,v in mutations:z=copy.deepcopy(b);z[k]=v;rejects(lambda z=z:m.build_receipt(z))
   z=copy.deepcopy(b);z['core_state']['evidence']['workflow']='b'*64;rejects(lambda:m.build_receipt(z))
   z=copy.deepcopy(b);z['core_state']['evidence']['policy']['permissions']['publish']=True;rejects(lambda:m.build_receipt(z))
   z=copy.deepcopy(b);z['tests'][0].pop('log_sha256');rejects(lambda:m.build_receipt(z))
 case('complete_diff_manifest_and_receipt_mutation_matrix',manifest_and_receipt)
after=digest(repo);ok=bool(cases) and all(c['exit_code']==0 for c in cases) and before==after
print(json.dumps({'id':'so001-trusted','evidence_mode':'native-fixture-contract','backend_calls':0,'source_digest':before,'source_stable':before==after,'passed':ok,'cases':cases}));sys.exit(0 if ok else 1)
