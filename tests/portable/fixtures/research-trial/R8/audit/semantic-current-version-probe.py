"""Native same-source/package physical-version fence regression; no DB edits."""
import argparse,importlib.util,json,shutil,sys,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve();root=Path(__file__).resolve().parents[3];sys.path.insert(0,str(repo))
from agent_workflow.hostbridge import HostBridge
from agent_workflow.core import digest
spec=importlib.util.spec_from_file_location('version_fence_fixture',root/'research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);write=m.helper.write_json
m.helper.write_json=lambda p,v:write(p,{**v,'schema_version':2} if isinstance(v,dict) and 'assessment_path' in v else v)
s=digest(repo)
try:
 with tempfile.TemporaryDirectory(prefix='semantic-version-') as d:
  c=m.Cases(repo,Path(d).resolve());f=c.fixture('same-content-new-controller');seed=c.q.baseline(f);b=HostBridge(f['c']);ticket=b.reserve();ticket=b.begin(ticket['request_id']);oldv=f['c'].status()['active_version']
  rows=lambda:[{'goal_id':g,'status':'achieved','reason':'Independent finite native goal review','evidence':['native:exact-current-source/'+g],'task_id':None} for g in f['policy']['goals']]
  c.q.assessment(f,seed,[],schema_version=2,goal_coverage=rows());oldassessment=f['assessment_path'].read_bytes();actual=f['c'].resolve_quality_review;called=[]
  def lost(*args,**kw):
   result=actual(*args,**kw);called.append(result['request'] is None);raise ValueError('controlled reply loss after actual semantic success')
  f['c'].resolve_quality_review=lost
  try:b.reconcile(ticket['request_id'])
  except ValueError:pass
  assert called==[True] and f['c'].status()['request'] is None
  f['c'].resolve_quality_review=actual
  tid=c.q.task(f,'real-no-source-change-work');candidate=c.q.candidate(tid,finding='d'*64);coverage=rows();coverage[0].update(status='actionable',task_id=tid);c.q.assessment(f,seed,[candidate],schema_version=2,goal_coverage=coverage);f['clock'][0]+=6;worker=f['c'].tick();assert worker['phase']=='worker_requested'
  run=f['c']._core('claim',{'worker':'native-current-version-worker','task_id':tid},oldv);own=c.q.owned(run);f['c'].bind_run(worker['request']['id'],run['id']);f['c']._core('change',own,oldv);assert f['c']._core('verify',own,oldv)['passed'];f['c']._core('transition',{**own,'target':'ready_review'},oldv);review=f['c'].tick();c.q.assessment(f,run['id'],[],schema_version=2,goal_coverage=rows());f['c']._core('transition',{**own,'target':'accepted'},oldv)
  receipt={'schema_version':1,'approved':True,'reviewer':m.helper.REVIEWER,'run_id':run['id'],'core_state':f['p']['core_state'],'source_root':f['p']['repo'],'source_sha256':digest(f['repo']),'controller_package_sha256':run['workflow']};rp=f['root']/'receipt.json';write(rp,receipt);successor=f['root']/'runtime/same-content-v2';successor.mkdir();shutil.copytree(f['repo']/'agent_workflow',successor/'agent_workflow');f['c'].promote(review['request']['id'],str(rp),m.helper.sha(rp.read_bytes()),str(successor),oldv['id']);newv=f['c'].status()['active_version'];assert newv['id']!=oldv['id'] and newv['package_sha256']==oldv['package_sha256'] and newv['source_sha256']==oldv['source_sha256']
  f['assessment_path'].write_bytes(oldassessment);c.reopen(f);b=HostBridge(f['c']);before=b.status();count=f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0];resolver_calls=[]
  def forbidden(*args,**kw):resolver_calls.append(True);raise AssertionError('must not replay semantic resolver')
  f['c'].resolve_quality_review=forbidden
  try:result=b.reconcile(ticket['request_id']);ack=result['phase']=='completed';error=None
  except Exception as ex:ack=False;error=type(ex).__name__+': '+str(ex)
  after=b.status();assert before['started']==after['started'] and before['deadline']==after['deadline'] and before['requests_spent']==after['requests_spent'];assert count==f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0] and not resolver_calls and digest(repo)==s
  value={'passed':not ack,'source_sha256':s,'source_unchanged':True,'same_source_and_package':True,'different_current_physical_version':True,'old_version':oldv['id'],'current_version':newv['id'],'acknowledged_old_semantic_event':ack,'reconciliation_error':error,'same_original_resources':True,'resolver_replayed':False,'db_tampering':False,'native_fixture':True,'controlled_outer_reply_loss':True,'model_calls':0};print(json.dumps(value));sys.exit(0 if value['passed'] else 1)
except Exception as ex:
 print(json.dumps({'passed':False,'source_sha256':s,'error':type(ex).__name__+': '+str(ex),'db_tampering':False}));sys.exit(2)
