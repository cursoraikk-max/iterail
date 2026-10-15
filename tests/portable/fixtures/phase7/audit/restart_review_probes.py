"""Independent phase7 typed restart probes; owned fixtures only."""
import copy,hashlib,json,sqlite3,sys,tempfile,traceback
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];SOURCE=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(ROOT/'phase7/audit'));import audit_common as a
from agent_workflow import evolution,uiux,uiux_maintenance,core

def pending_corruption():
    tmp,raw,c=a.common.fixture(evolution,wall=600)
    owner=None
    try:
        root=raw.resolve();c['repo']=str(Path(c['repo']).resolve());p=a.policy(root,c['project']['id']);file=root/'owner-uiux.json';file.write_bytes(uiux.encoded(p));c['project']['uiux']={'path':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
        clock=[1000.0];Base=uiux_maintenance.UIUXMaintenance
        class Clocked(Base):
            def __init__(self,*args,**kwargs):super().__init__(*args,clock=lambda:clock[0],**kwargs)
        with patch.object(uiux_maintenance,'UIUXMaintenance',Clocked):
            owner=evolution.Supervisor(root/'owner',c)
            with patch.object(core.Engine,'_uiux_task',side_effect=ValueError('native transient import ValueError')):
                first=owner.maintenance_tick()
            assert first['phase']=='backoff' and first['failure_count']==1
            ledger=root/'owner/uiux-core/uiux-maintenance.sqlite';db=sqlite3.connect(ledger);before=dict(zip([x[0] for x in db.execute('SELECT * FROM meta').description],db.execute('SELECT * FROM meta').fetchone()))
            pending=uiux.strict_json(before['pending']);pending['proposal']['title']='Corrupted owned pending title';body=uiux.encoded(pending).decode();db.execute('UPDATE meta SET pending=? WHERE id=1',(body,));db.commit();db.close()
            baseline=copy.deepcopy(owner.status()['meta']);owner.db.close();owner=None;clock[0]=1031.0;owner=evolution.Supervisor(root/'owner');result=owner.maintenance_tick()
            db=sqlite3.connect(ledger);after=dict(zip([x[0] for x in db.execute('SELECT * FROM meta').description],db.execute('SELECT * FROM meta').fetchone()));db.close()
            now=owner.status()['meta'];obs={'first_phase':first['phase'],'result_after_restart':result,'before_next_due':before['next_due'],'after_next_due':after['next_due'],'before_failure_count':before['failure_count'],'after_failure_count':after['failure_count'],'corrupt_pending_preserved':after['pending']==body,'owner_calls_preserved':baseline['calls']==now['calls'],'owner_deadline_preserved':baseline['deadline']==now['deadline']}
            return obs,result['phase']=='blocked' and result.get('failure_class')=='integrity' and after['next_due']==before['next_due'] and after['failure_count']==before['failure_count'] and obs['corrupt_pending_preserved'] and obs['owner_calls_preserved'] and obs['owner_deadline_preserved']
    finally:
        if owner:owner.db.close()
        tmp.cleanup()

def callback_valueerror():
    with tempfile.TemporaryDirectory(prefix='p7-retry-valueerror-',dir=ROOT/'phase7/audit') as raw:
        root=Path(raw).resolve();source=root/'source';source.mkdir();(source/'app.txt').write_text('native source');p=a.policy(root);clock=[1000.0];attempts=[];fail=[True]
        def sink(proposal):
            attempts.append(copy.deepcopy(proposal))
            if fail[0]:raise ValueError('native transient callback ValueError')
        s=uiux_maintenance.UIUXMaintenance(root/'state',p,source,clock=lambda:clock[0],task_sink=sink)
        try:
            first=s.tick();before=s.status();s.db.close();s=None;clock[0]=1031.0
            s=uiux_maintenance.UIUXMaintenance(root/'state',p,source,clock=lambda:clock[0],task_sink=sink);second=s.tick();middle=s.status();s.db.close();s=None;clock[0]=1092.0;fail[0]=False
            s=uiux_maintenance.UIUXMaintenance(root/'state',p,source,clock=lambda:clock[0],task_sink=sink);third=s.tick();after=s.status();obs={'phases':[first['phase'],second['phase'],third['phase']],'failure_counts':[before['failure_count'],middle['failure_count'],after['failure_count']],'next_due':[before['next_due'],middle['next_due'],after['next_due']],'attempts':len(attempts),'same_intent':len(attempts)==3 and attempts[0]==attempts[1]==attempts[2],'visual_verified':third['production_visual_verified']}
            return obs,obs['phases']==['backoff','backoff','due'] and obs['failure_counts']==[1,2,0] and obs['next_due']==[1030.0,1091.0,1152.0] and obs['same_intent'] and obs['visual_verified'] is False
        finally:
            if s is not None and s.db is not None:s.db.close()

before=a.common.source_digest(SOURCE);results=[]
for label,fn in [('corrupt_pending_blocked_no_retry_mutation',pending_corruption),('callback_ValueError_remains_same_intent_backoff',callback_valueerror)]:
    try:
        observation,passed=fn();results.append({'case':label,'passed':passed,'observations':observation})
    except Exception as error:results.append({'case':label,'passed':False,'error':type(error).__name__+': '+str(error),'traceback':traceback.format_exc()})
after=a.common.source_digest(SOURCE);passed=all(r['passed'] for r in results) and before==after
print(json.dumps({'id':'p7-independent-restart-probes','passed':passed,'exact_source_sha256':before,'source_unchanged':before==after,'external_provider_calls':0,'network_calls':0,'evidence_kind':'native_fixture','cases':results},sort_keys=True,indent=2));raise SystemExit(0 if passed else 1)
