"""Trusted fixed denominator metrics contract; local immutable fixture evidence."""
import argparse,copy,hashlib,importlib,json,sys,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);a=p.parse_args();repo=Path(a.repo).resolve();sys.path.insert(0,str(repo))
from agent_workflow.core import digest
before=digest(repo);cases=[]
def require(v,msg='assertion failed'):
 if not v:raise AssertionError(msg)
def case(name,fn):
 try:fn();cases.append({'case':name,'exit_code':0})
 except Exception as e:cases.append({'case':name,'exit_code':1,'error':type(e).__name__+': '+str(e)})
def rejects(fn):
 try:fn()
 except (ValueError,TypeError):return
 raise AssertionError('invalid metrics data accepted')
def canon(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
try:m=importlib.import_module('agent_workflow.metrics')
except ImportError as e:m=None;cases.append({'case':'metrics_module_exists','exit_code':1,'error':str(e)})
if m:
 def fixtures():
  from agent_workflow.receipt import build_receipt
  with tempfile.TemporaryDirectory(prefix='so003-trusted-') as t:
   log=Path(t)/'raw.log';log.write_bytes(b'fixture command exit0\n');lh=hashlib.sha256(log.read_bytes()).hexdigest();h='a'*64;head='b'*40;policy={'permissions':{'publish':False}};ph=canon(policy)
   manifest={'entries':[{'path':'code.py','status':'M','binary':False}],'complete':True,'truncated':False};manifest['digest']=canon(manifest)
   bundle={'task_id':'task-fixture','run_id':'run-fixture','baseline':'c'*64,'base_head':head,'candidate':h,'head':head,'workflow':h,'runtime':h,'policy':ph,'checker':h,'manifest':manifest,'reviewer':{'approved':True,'candidate':h,'version':'native-fixture','log_sha256':lh},'tests':[{'argv':['python','fixture.py'],'exit_code':0,'log_sha256':lh}],'cas':{'phase':'done','expected':head,'candidate':head},'core_state':{'state':'accepted','evidence':{'baseline':'c'*64,'candidate':h,'workflow':h,'policy':policy},'policy_sha256':ph},'main_digest':h,'backend':{'protocol':'exec-v1','valid':True,'version':'native-fixture','model_completed':True}}
   receipt=build_receipt(bundle);cohort={'id':'frozen-cohort','parent_goal_ids':['goal-a','goal-b','goal-c']}
   def event(i,k,parent='goal-a',attempt='attempt-a'):return {'id':i,'kind':k,'parent_goal_id':parent,'attempt_id':attempt,'source':h}
   events=[event('call-a','call_reserved'),event('done-a','completed'),event('call-b','call_reserved','goal-b','attempt-b'),event('timeout-b','attempt_failed','goal-b','attempt-b'),event('reject-b','review_rejected','goal-b','attempt-b'),event('stop-b','stopped','goal-b','attempt-b'),event('old-b','superseded','goal-b','attempt-b')]
   events[1].update(receipt=receipt,logs=[{'path':str(log),'sha256':lh}]);original=copy.deepcopy((cohort,events));r=m.build_metrics(cohort,events)
   require((cohort,events)==original,'metrics mutates source data');require(r['totals']=={'registered':3,'succeeded':1,'failed':1,'not_completed':1,'unknown':0});require(r['attempts']==2 and r['calls']==2)
   require(r['interventions']=={'status':'unknown','count':None});require(r['cost']=={'currency':None,'amount':None,'status':'unknown'});require(r['by_kind']['superseded']==1 and r['by_kind']['review_rejected']==1)
   duplicate=m.build_metrics(cohort,events+[copy.deepcopy(events[1])]);require(duplicate==r,'replayed receipt changes metrics')
   z=copy.deepcopy(events);e=copy.deepcopy(events[1]);e['source']='d'*64;z.append(e);rejects(lambda:m.build_metrics(cohort,z))
   z=copy.deepcopy(events);z[1]['source']='d'*64;r2=m.build_metrics(cohort,z);require(r2['totals']['succeeded']==0,'stale receipt counted')
   z=copy.deepcopy(events);z[1]['receipt']['complete']=False;r2=m.build_metrics(cohort,z);require(r2['totals']['succeeded']==0)
   z=copy.deepcopy(events);z[1]['receipt']['tests'][0]['exit_code']=1;r2=m.build_metrics(cohort,z);require(r2['totals']['succeeded']==0,'tampered receipt counted')
   log.unlink();r2=m.build_metrics(cohort,events);require(r2['totals']['unknown']>=1 and r2['totals']['succeeded']==0 and r2['complete'] is False,'missing log omitted')
   z=copy.deepcopy(events);z.append(event('split','completed','unregistered-child'));rejects(lambda:m.build_metrics(cohort,z))
   require(r2['totals']['registered']==3,'missing evidence changes denominator')
 case('fixed_cohort_duplicates_failures_unknown_cost_log_and_version_binding',fixtures)
after=digest(repo);passed=all(c['exit_code']==0 for c in cases) and before==after
print(json.dumps({'id':'so003-trusted','evidence_mode':'native-golden-fixture','backend_calls':0,'source_digest':before,'source_stable':before==after,'passed':passed,'cases':cases}));sys.exit(0 if passed else 1)
