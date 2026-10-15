from common import *
import subprocess,tempfile,time

def native_artifact(context,code,variant):
 results=[];started=time.perf_counter()
 for ident in ['P2-producer-check','P2-memory-required','P2-handoff-rule']:
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'rules.txt').write_text('Mandatory trusted rules for '+ident+'\n')
   (root/'main.py').write_text('def repair():\n    return True\n')
   (root/'unrelated.txt').write_text(('Historical optional context '+ident+'\n')*30)
   required=['rules.txt','main.py']+(['unrelated.txt'] if variant=='full' else [])
   if variant=='failure': required=['main.py']
   b=context.retrieve_context(root,'repair',mandatory_paths=required,byte_budget=12000,max_file_bytes=1000,source_digest=source_digest(root))
   paths={x['path'] for x in b['files']};coverage=len(paths&{'rules.txt','main.py'})
   results.append({'goal_id':ident,'success':coverage==2,'coverage':coverage,'used_bytes':b['used_bytes']})
 return {'schema_version':1,'evaluator_id':'context-coverage-v1','code_version':code,'parent_goal':'local-phase3-bounded-context','goal_ids':[x['goal_id'] for x in results],'variant':variant,'results':results,'costs':{'elapsed_seconds':time.perf_counter()-started,'input_tokens':None,'output_tokens':None,'money':None,'retries':0,'verifier_seconds':0,'cleanup_seconds':0,'review_seconds':0,'discarded_attempts':0}}

def setup(m,root):
 context=load(sys.argv[1],'context');code=source_digest(sys.argv[1]);evidence=root/'evidence';evidence.mkdir()
 history=ROOT.parent/'phase2/evidence/final-receipt.json';(evidence/'history.json').write_bytes(history.read_bytes())
 refs=[{'path':'history.json','sha256':hashlib.sha256(history.read_bytes()).hexdigest()}]
 artifacts={};values={}
 for ref,variant in [('baseline.json','full'),('candidate.json','minimal'),('nogain1.json','full'),('nogain2.json','full'),('failure.json','failure')]:
  data=native_artifact(context,code,variant);values[ref]=data;(evidence/ref).write_text(json.dumps(data,sort_keys=True));artifacts[ref]=hashlib.sha256((evidence/ref).read_bytes()).hexdigest()
 policy={'schema_version':1,'parent_goal':'local-phase3-bounded-context','goal_ids':['P2-producer-check','P2-memory-required','P2-handoff-rule'],'constraints':{'network':False,'model_calls':0,'publish':False,'mandatory_rules':True},'objective':'bytes_no_coverage_regression','min_reduction':0.1,'evaluator_id':'context-coverage-v1','code_version':code,'history_refs':refs,'evidence_root':str(evidence),'artifact_registry':artifacts}
 return policy,values

def proposal(p,ident='test',ref='candidate.json'):
 return {k:copy.deepcopy(p[k]) for k in ['schema_version','parent_goal','goal_ids','constraints','objective','min_reduction','evaluator_id','code_version','history_refs']}|{'id':ident,'hypothesis':'Retain required rule and code coverage while omitting unrelated bulk context.','baseline_ref':'baseline.json','candidate_ref':ref}

def retain(m):
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,values=setup(m,root);store=m.StrategyStore(root/'store',trusted_policy=p);q=proposal(p);r=store.preregister(q)
  require(r['id']=='test' and r['proposal_sha256']==canonical(q) and r['policy_sha256']==canonical(p))
  require(store.history()==[],'evaluation happened before preregister')
  report=store.evaluate(r['id']);require(report['decision']=='retain' and report['model_benefit'] is False)
  require(report['denominator']==3 and report['paired_count']==3 and report['goal_ids']==p['goal_ids'])
  require(report['proposal_sha256']==canonical(q) and report['policy_sha256']==canonical(p) and report['code_version']==p['code_version'])
  require(report['cost_status']=='unknown' and report['baseline_costs']==values['baseline.json']['costs'] and report['candidate_costs']==values['candidate.json']['costs'])
  require(report['no_gain_count']==0 and report['evidence_refs'])
  require(store.evaluate(r['id'])==report and len(store.history())==1)
  require(m.StrategyStore(root/'store',trusted_policy=p).evaluate(r['id'])==report)
  rejects(lambda:store.preregister(proposal(p,'renamed')))
  altered=copy.deepcopy(q);altered['hypothesis']='changed';rejects(lambda:store.preregister(altered))
  for key,value in [('goal_ids',['P2-producer-check']),('constraints',{}),('code_version','0'*64),('hypothesis',''),('self_score',100),('baseline_ref','../escape')]:
   altered=proposal(p,'bad-'+key);altered[key]=value;rejects(lambda:store.preregister(altered))
  changed=copy.deepcopy(p);changed['min_reduction']=0
  rejects(lambda:m.StrategyStore(root/'store',trusted_policy=changed))
  (root/'evidence/candidate.json').write_text('{}');rejects(lambda:m.StrategyStore(root/'store',trusted_policy=p).evaluate(r['id']))

def stop(m):
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,_=setup(m,root);s=m.StrategyStore(root/'store',trusted_policy=p)
  a=s.preregister(proposal(p,'n1','nogain1.json'));r=s.evaluate(a['id']);require(r['decision']=='rollback' and r['no_gain_count']==1)
  require(s.evaluate(a['id'])==r and len(s.history())==1)
  b=s.preregister(proposal(p,'n2','nogain2.json'));r=s.evaluate(b['id']);require(r['decision']=='stop' and r['no_gain_count']==2)
  require(len(s.history())==2 and r['denominator']==3)
  try:
   c=s.preregister(proposal(p,'after-stop'));r=s.evaluate(c['id']);require(r['decision']=='stop','retained after stop')
  except ValueError:pass

def failures(m):
 for label,alter in [('goal_drop',lambda v:v['results'].pop()),('wrong_code',lambda v:v.update(code_version='0'*64)),('unknown_bytes',lambda v:v['results'][0].update(used_bytes=None)),('missing_cost',lambda v:v['costs'].pop('cleanup_seconds')),('manual_score',lambda v:v.update(self_score=100)),('missing_time',lambda v:v['costs'].update(elapsed_seconds=None)),('goal_dup',lambda v:v['results'].append(copy.deepcopy(v['results'][0])))]:
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);p,values=setup(m,root);v=values['candidate.json'];alter(v);f=root/'evidence/candidate.json';f.write_text(json.dumps(v));p['artifact_registry']['candidate.json']=hashlib.sha256(f.read_bytes()).hexdigest()
   try:
    s=m.StrategyStore(root/'store',trusted_policy=p);r=s.preregister(proposal(p));report=s.evaluate(r['id']);require(report['decision'] in ['rollback','stop'],label+' retained')
   except (ValueError,TypeError):pass
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,_=setup(m,root);s=m.StrategyStore(root/'store',trusted_policy=p);r=s.preregister(proposal(p,'failure','failure.json'));report=s.evaluate(r['id']);require(report['decision']=='rollback' and report['denominator']==3)
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);p,_=setup(m,root);s=m.StrategyStore(root/'store',trusted_policy=p);r=s.preregister(proposal(p));s.evaluate(r['id'])
  files=list((root/'store').rglob('*')); ledgers=[f for f in files if f.is_file() and 'ledger' in f.name]
  require(bool(ledgers),'missing persistent append ledger')
  ledgers[0].write_text('corrupted-history\n');rejects(lambda:m.StrategyStore(root/'store',trusted_policy=p))

def aggregate(m):
 for script,ident in [('appserver.py','p3-appserver'),('context.py','p3-context')]:
  p=subprocess.run([sys.executable,str(ROOT/'trusted'/script),sys.argv[1]],capture_output=True,text=True)
  require(p.returncode==0,ident+' aggregate regression: '+p.stdout[:1000]);result=json.loads(p.stdout);require(result['passed'] and result['exactsourceSHA']==source_digest(sys.argv[1]))
run('p3-strategy','strategy',['appserver','context'],[('preregister_retention_integrity',retain),('two_no_gain_stop',stop),('failure_cohort_cost_history',failures),('aggregate_contracts',aggregate)])
