"""Fixed read-only Evolution ledger interoperability, not inferred model benefit."""
import copy,hashlib,json,sqlite3,sys,tempfile
from pathlib import Path
from unittest.mock import patch
from common import run,require,rejects,meta,close
from fixtures import target,controller

def logical(root):
 result={}
 for path in root.rglob('*.sqlite'):
  db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
  try:result[str(path.relative_to(root))]=list(db.iterdump())
  finally:db.close()
 return result

def persistent_files(root):
 return {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob('*') if path.is_file() and not path.name.endswith(('-wal','-shm'))}

def readonly(m,state):
 require(callable(getattr(m,'evolution_summary',None)),'missing metrics.evolution_summary existing-owner API');before=logical(state);files=persistent_files(state);connect=sqlite3.connect;observed=[]
 def ro(db_uri,*args,**kwargs):
  require(isinstance(db_uri,str) and 'mode=ro' in db_uri and kwargs.get('uri') is True,'metrics did not open existing SQLite URI mode=ro');observed.append(db_uri);return connect(db_uri,*args,**kwargs)
 with patch('sqlite3.connect',side_effect=ro),patch('subprocess.run',side_effect=AssertionError('readonly summary launched process')),patch('subprocess.Popen',side_effect=AssertionError('readonly summary spawned process')):result=m.evolution_summary(state)
 require(observed,'summary did not read persisted owner');require(logical(state)==before and persistent_files(state)==files,'summary changed persisted policy/budget/stop/state/files')
 return result

def shape(summary,c,registered):
 require(summary['schema_version']==1 and summary['evidence_kind']=='readonly_evolution_ledger','summary schema/evidence label');require(summary['project_id']==c['project']['id'] and summary['project_kind']==c['project']['kind'],'summary ProjectSpec mismatch');require(summary['parent_goal_ids']==[x['id'] for x in c['checks']] and summary['totals']['registered']==registered,'summary inflated/dropped fixed owner parent denominator');require(summary['cost']=={'amount':None,'currency':None,'status':'unknown'},'unknown cost became zero/benefit')


def reservations_actual_and_readonly(m):
 require(callable(getattr(m,'evolution_summary',None)),'missing metrics.evolution_summary existing-owner API');from agent_workflow.evolution import Supervisor
 tmp,root,c=target(None,failure='unknown')
 try:
  s=Supervisor(root/'owner',c);s.cycle();s.reserve('attempt',seconds=5,job='fixture-check');s.set_stop(True);owner=meta(s);close(s)
  summary=readonly(m,root/'owner');shape(summary,c,1);ledger=summary['ledger'];require(ledger['reserved_calls']==4 and ledger['actual_calls']==1,'pair reservations/local calls/replayed child calls confused with actual backend launches');require(ledger['started']==owner['started'] and ledger['deadline']==owner['deadline'] and ledger['stopped'] and ledger['blocked']==owner['blocked'],'readonly ledger lost original limits/stop');require(summary['totals']['succeeded']==0 and summary['totals']['failed']==1,'failed native worker was counted success');again=readonly(m,root/'owner');require(again==summary,'repeat summary altered counts')
 finally:tmp.cleanup()


def real_receipt_and_missing_evidence(m):
 require(callable(getattr(m,'evolution_summary',None)),'missing metrics.evolution_summary existing-owner API');from agent_workflow.evolution import Supervisor
 tmp,root,c=target(None)
 try:
  s=Supervisor(root/'owner',c);require(s.cycle()['phase']=='complete','native successful target prerequisite failed');close(s);summary=readonly(m,root/'owner');shape(summary,c,1);require(summary['ledger']['reserved_calls']==summary['ledger']['actual_calls']==2 and summary['totals']['succeeded']==1,'actual complete receipt not summarized')
  receipts=list((root/'owner').rglob('receipts/*.json'));require(len(receipts)==1,'native receipt missing');receipts[0].write_text('{}');missing=readonly(m,root/'owner');shape(missing,c,1);require(missing['totals']['succeeded']==0 and missing['totals']['unknown']==1 and missing['complete'] is False and missing['findings'],'missing current machine/reviewer/CAS evidence claimed success')
 finally:tmp.cleanup()


def cross_release_parent_goals(m):
 require(callable(getattr(m,'evolution_summary',None)),'missing metrics.evolution_summary existing-owner API');from agent_workflow.evolution import Supervisor
 tmp,root,c=controller(None)
 try:
  s=Supervisor(root/'owner',c);require(s.cycle()['phase']=='complete','native A→B prerequisite failed');close(s);s=Supervisor(root/'owner');require(s.cycle()['phase']=='complete','native B→C prerequisite failed');owner=meta(s);releases=s.status()['releases'];close(s);summary=readonly(m,root/'owner');shape(summary,c,2);require(summary['ledger']['reserved_calls']==summary['ledger']['actual_calls']==4 and summary['ledger']['epoch']==2 and summary['ledger']['active']==owner['active'],'cross-release ledger reset or double count');require(summary['totals']['succeeded']==2 and len(summary['by_release'])==3,'cross-release provenance or fixed completed denominator missing');reported={x['release_id']:x for x in summary['by_release']};require(set(reported)=={x['id'] for x in releases},'release denominator invented or omitted');require(all(reported[x['id']]['fingerprint']==x['fingerprint'] for x in releases),'per-release source identity mismatch')
 finally:tmp.cleanup()


def missing_state_no_initialization(m):
 require(callable(getattr(m,'evolution_summary',None)),'missing metrics.evolution_summary existing-owner API')
 with tempfile.TemporaryDirectory(prefix='future-metrics-') as root:
  missing=Path(root)/'missing'
  try:m.evolution_summary(missing)
  except (ValueError,TypeError,OSError,sqlite3.Error):pass
  else:raise AssertionError('missing owner state was invented')
  require(not missing.exists(),'readonly summary created missing owner state')

run('p4-evolution-metrics','metrics',[('reserved_vs_actual_fixed_parent_readonly',reservations_actual_and_readonly),('current_complete_receipt_and_unknown_missing_evidence',real_receipt_and_missing_evidence),('cross_release_parent_denominator_and_provenance',cross_release_parent_goals),('missing_state_has_no_init_side_effect',missing_state_no_initialization)])
