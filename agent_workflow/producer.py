"""Owner-bound local feedback candidates; only fixed machine reproduction admits work."""
import hashlib
import contextlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from .core import WorkflowError, digest, _OutputDrain, core_process


def sha(data):
    return hashlib.sha256(data).hexdigest()


def number(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or value <= 0:
        raise WorkflowError('positive integer required')
    return value


def policy(engine):
    descriptor = engine.config.get('producer_policy')
    if not descriptor:
        raise WorkflowError('producer disabled without owner descriptor')
    path = Path(descriptor['path'])
    if path.is_symlink() or sha(path.read_bytes()) != descriptor['sha256']:
        raise WorkflowError('owner producer policy changed')
    p = json.loads(path.read_bytes())
    if set(p) != {'schema_version', 'allowed_sources', 'registry', 'queue'} or type(p['schema_version']) is not int or p['schema_version'] != 1:
        raise WorkflowError('invalid producer policy')
    if p['allowed_sources'] != ['failed_log', 'unresolved_review', 'owner_check']:
        raise WorkflowError('invalid producer sources')
    q = p['queue']
    if set(q) != {'max_open','max_new_per_cycle','wip','aging_seconds','repro_seconds','log_bytes'} or any(type(v) is not int or v <= 0 for v in q.values()) or q['wip'] != 1 or q['log_bytes'] > 65536:
        raise WorkflowError('invalid producer queue bounds')
    if not isinstance(p['registry'], dict) or not p['registry']:
        raise WorkflowError('empty registry')
    identities = {}
    for ident, entry in p['registry'].items():
        if not isinstance(ident, str) or not ident or not isinstance(entry, dict) or set(entry) != {'argv','acceptance'}:
            raise WorkflowError('invalid registry')
        argv = entry['argv']
        if not isinstance(argv,list) or not argv or any(not isinstance(a,str) or not a for a in argv):
            raise WorkflowError('invalid fixed argv')
        if entry['acceptance'] != {'exit_code':1,'classification':'product_defect','source_stable':True} or type(entry['acceptance']['exit_code']) is not int or type(entry['acceptance']['source_stable']) is not bool:
            raise WorkflowError('mandatory reproduction acceptance')
        for a in argv:
            f = Path(a)
            if f.is_file():
                if f.is_symlink() and a != argv[0]:
                    raise WorkflowError('trusted script symlink')
                identities[str(f.resolve())] = sha(f.read_bytes())
                if f.suffix == '.py':
                    for helper in f.parent.glob('*.py'):
                        if helper.is_symlink():
                            raise WorkflowError('trusted helper symlink')
                        identities[str(helper.resolve())] = sha(helper.read_bytes())
    row = engine.db.execute('SELECT identities FROM producer_binding WHERE id=1').fetchone()
    if row and json.loads(row[0]) != identities:
        raise WorkflowError('trusted reproducer source changed')
    return p, identities


def init(engine):
    engine.db.executescript('''CREATE TABLE IF NOT EXISTS producer_binding(id INTEGER PRIMARY KEY CHECK(id=1),identities TEXT);
    CREATE TABLE IF NOT EXISTS producer_candidates(id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE,payload TEXT,created REAL,status TEXT,task_id TEXT,evidence TEXT);
    CREATE TABLE IF NOT EXISTS producer_findings(fingerprint TEXT PRIMARY KEY,task_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS producer_task_checks(task_id TEXT PRIMARY KEY,body TEXT NOT NULL);''')
    p, identities = policy(engine)
    with (contextlib.nullcontext() if engine.db.in_transaction else engine.tx()):
        engine.db.execute('INSERT OR IGNORE INTO producer_binding VALUES(1,?)',(json.dumps(identities,sort_keys=True),))
        # Explicit upgrade of earlier controller-created admitted candidates;
        # task text is never used as the source of acceptance authority.
        for row in engine.db.execute("SELECT * FROM producer_candidates WHERE task_id IS NOT NULL AND evidence IS NOT NULL").fetchall():
            data=json.loads(row['payload']);ev=json.loads(row['evidence'])
            ident=data['check_id'];report=ev.get('report',{})
            if ev.get('policy_sha256')==engine.config['producer_policy']['sha256'] and ident in p['registry'] and ev.get('result',{}).get('argv')==binding(engine,ident,p)['argv'] and report.get('id')==ident and report.get('classification')=='product_defect' and report.get('passed') is False and report.get('source_stable') is True and report.get('exactsourceSHA')==ev.get('source') and ev.get('result',{}).get('exit_code')==1:
                bind_task(engine,row['task_id'],ident,ev['source'],p)
    return {'initialized':True,'policy_sha256':engine.config['producer_policy']['sha256']}


def ready(engine):
    init(engine)
    return policy(engine)[0]


def offer(engine,source,external_key,check_id,title,body='',source_ref=''):
    p = ready(engine)
    payload = dict(source=source,external_key=external_key,check_id=check_id,title=title,body=body,source_ref=source_ref)
    if any(not isinstance(v,str) for v in payload.values()) or not external_key or not title or source not in p['allowed_sources'] or check_id not in p['registry'] or any(len(v.encode())>8192 for v in payload.values()):
        raise WorkflowError('invalid candidate data')
    encoded = json.dumps(payload,sort_keys=True)
    fingerprint = sha(json.dumps([source,external_key,check_id]).encode())
    with engine.tx():
        old = engine.db.execute('SELECT * FROM producer_candidates WHERE fingerprint=?',(fingerprint,)).fetchone()
        if old:
            if old['payload'] != encoded:
                raise WorkflowError('candidate fingerprint conflicts')
            return {'id':old['id'],'deduplicated':True}
        if engine.db.execute("SELECT count(*) FROM producer_candidates WHERE status='pending'").fetchone()[0] >= p['queue']['max_open']:
            raise WorkflowError('candidate queue full')
        ident = 'candidate-'+uuid.uuid4().hex[:12]
        engine.db.execute('INSERT INTO producer_candidates VALUES(?,?,?,?,?,?,?)',(ident,fingerprint,encoded,time.time(),'pending',None,None))
        engine.event(None,None,'producer_offered',{'candidate_id':ident,'fingerprint':fingerprint,'source':source})
    return {'id':ident,'deduplicated':False}


def binding(engine,ident,p):
    return {'check_id':ident,'argv':[a.replace('{repo}',engine.config['repo']) for a in p['registry'][ident]['argv']],
            'expected_exit_code':0,'policy_sha256':engine.config['producer_policy']['sha256'],
            'registry_sha256':sha(json.dumps(p['registry'],sort_keys=True,separators=(',',':')).encode())}


def bind_task(engine,tid,ident,source,p):
    bound=binding(engine,ident,p)
    semantic=sha(json.dumps([ident,source,bound['registry_sha256']]).encode())
    old=engine.db.execute('SELECT task_id FROM producer_findings WHERE fingerprint=?',(semantic,)).fetchone()
    engine.db.execute('INSERT OR IGNORE INTO producer_task_checks VALUES(?,?)',(tid,json.dumps(bound,sort_keys=True)))
    if old:return old[0]
    engine.db.execute('INSERT INTO producer_findings VALUES(?,?)',(semantic,tid))
    engine.event(tid,None,'producer_acceptance_bound',{'finding_fingerprint':semantic,'binding':bound})
    return tid


def task_check(engine,tid):
    exists=engine.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='producer_task_checks'").fetchone()
    if not exists:return None
    row=engine.db.execute('SELECT body FROM producer_task_checks WHERE task_id=?',(tid,)).fetchone()
    if not row:return None
    p=policy(engine)[0];bound=json.loads(row[0])
    if bound != binding(engine,bound['check_id'],p):raise WorkflowError('producer task acceptance changed')
    return bound


def check_passed(bound,result,candidate):
    try:report=json.loads(result['stdout'])
    except (ValueError,KeyError,TypeError):return False
    return (isinstance(report,dict) and result['exit_code']==0 and not result.get('stdout_truncated') and not result.get('execution_error') and report.get('id')==bound['check_id'] and report.get('passed') is True and report.get('classification')=='passed' and report.get('source_stable') is True and report.get('exactsourceSHA')==candidate)


def score(row,p,now):
    weight=1  # Claimed source labels confer no trusted priority.
    age=max(0,int((now-row['created'])/p['queue']['aging_seconds']))
    return {'value':age*4+weight,'age_buckets':age,'source_weight':weight,'tie_break':'created_then_id','provenance':'claimed_unverified' if row['status']=='pending' else row['status']}


def queue(engine):
    p=ready(engine);now=time.time();rows=engine.db.execute('SELECT * FROM producer_candidates ORDER BY created,id').fetchall()
    return {'candidates':[dict(id=r['id'],task_id=r['task_id'],status=r['status'],score=score(r,p,now)) for r in rows], 'limits':p['queue'],'policy_sha256':engine.config['producer_policy']['sha256']}


def reproduce(engine,argv,p):
    env={'PATH':os.defpath+':/opt/homebrew/bin:/usr/local/bin','PYTHONPATH':str(Path(__file__).parent.parent),'LANG':'en_US.UTF-8'}
    process=None;guardian=None;drains=[];result={'argv':argv,'exit_code':127}
    try:
        cutoff=time.time()+p['queue']['repro_seconds']
        process,guardian=core_process.launch(argv,engine.config['repo'],env,cutoff)
        drains=[_OutputDrain(process.stdout),_OutputDrain(process.stderr)]
        process.stdout=process.stderr=None
        for d in drains:d.thread.start()
        try:
            process.communicate(timeout=max(.001,cutoff-time.time()));result['exit_code']=process.returncode
        except subprocess.TimeoutExpired:
            result.update(exit_code=124,timeout=True)
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)
    finally:
        if process is not None:
            try:engine.stop_test_process(process);process.communicate()
            except Exception as error:result['cleanup_error']=str(error)
        for d in drains:
            d.thread.join();d.stream.close()
        if guardian is not None:guardian.close()
    for name,d in zip(('stdout','stderr'),drains):
        raw=bytes(d.prefix[:p['queue']['log_bytes']]);log_sha=sha(raw)
        log_dir=engine.state/'producer-logs';log_dir.mkdir(exist_ok=True);log_path=log_dir/(log_sha+'.log')
        if log_path.exists() and (log_path.is_symlink() or sha(log_path.read_bytes())!=log_sha):
            raise WorkflowError('producer log content corrupted')
        if not log_path.exists():log_path.write_bytes(raw)
        result[name+'_log_ref']='producer-logs/'+log_path.name
        result[name]=raw.decode('utf-8',errors='replace');result[name+'_sha256']=sha(raw);result[name+'_total_bytes']=d.total;result[name+'_truncated']=d.total>len(raw)
    return result


def scan(engine,limit=None):
    p=ready(engine);limit=p['queue']['max_new_per_cycle'] if limit is None else number(limit)
    if limit>p['queue']['max_new_per_cycle']:raise WorkflowError('cycle admission limit exceeded')
    rows=engine.db.execute("SELECT * FROM producer_candidates WHERE status='pending'").fetchall();now=time.time()
    rows=sorted(rows,key=lambda r:(-score(r,p,now)['value'],r['created'],r['id']))
    admitted=[];observed=[];processed=0
    for row in rows:
        if processed>=limit:break
        processed+=1
        data=json.loads(row['payload']);before=digest(engine.config['repo']);argv=[a.replace('{repo}',engine.config['repo']) for a in p['registry'][data['check_id']]['argv']]
        result=reproduce(engine,argv,p);after=digest(engine.config['repo']);report=None
        try:report=json.loads(result.get('stdout',''))
        except (ValueError,TypeError):pass
        good=(result['exit_code']==1 and not result.get('error') and not result.get('cleanup_error') and not result.get('stdout_truncated') and isinstance(report,dict) and report.get('id')==data['check_id'] and report.get('passed') is False and report.get('classification')=='product_defect' and report.get('source_stable') is True and report.get('exactsourceSHA')==before==after)
        evidence=dict(source=before,report=report,result=result,policy_sha256=engine.config['producer_policy']['sha256'],provenance='machine_reproduced' if good else 'reproduction_rejected')
        evidence['evidence_sha256']=sha(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode())
        # Recheck authority and source before the transaction; serialize admission
        # and Core import together so concurrent entry points cannot duplicate work.
        policy(engine)
        with engine.tx():
            current=engine.db.execute('SELECT * FROM producer_candidates WHERE id=?',(row['id'],)).fetchone()
            if current['status']!='pending':continue
            status='rejected'
            if good and digest(engine.config['repo'])==before:
                bound=binding(engine,data['check_id'],p)
                semantic=sha(json.dumps([data['check_id'],before,bound['registry_sha256']]).encode())
                prior=engine.db.execute('SELECT task_id FROM producer_findings WHERE fingerprint=?',(semantic,)).fetchone()
                if prior:
                    engine.db.execute("UPDATE producer_candidates SET status='deduplicated',task_id=?,evidence=? WHERE id=?",(prior[0],json.dumps(evidence,sort_keys=True),row['id']))
                    engine.event(prior[0],None,'producer_finding_deduplicated',{'candidate_id':row['id'],'finding_fingerprint':semantic,'evidence':evidence})
                    observed.append({'id':row['id'],'status':'deduplicated','task_id':prior[0]});continue
                active=engine.db.execute("SELECT count(*) FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review') AND lease_until>? AND deadline>?",(time.time(),time.time())).fetchone()[0]
                count=engine.db.execute("SELECT count(*) FROM tasks WHERE state NOT IN ('accepted','failed')").fetchone()[0]
                if active>=p['queue']['wip'] or count>=p['queue']['max_open']:
                    engine.event(None,None,'producer_blocked',{'candidate_id':row['id'],'reason':'wip_or_queue','evidence':evidence});observed.append({'id':row['id'],'status':'blocked'});continue
                tid='task-'+uuid.uuid4().hex[:12];key='producer:'+row['fingerprint']
                body=json.dumps({'candidate_id':row['id'],'feedback':data,'reproduction':evidence},sort_keys=True)
                engine.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,1,?)',(tid,'local',key,data['title'],body,'queued'))
                engine.event(tid,None,'imported',{'source':'local','external_key':key,'producer_candidate':row['id'],'evidence':evidence})
                bind_task(engine,tid,data['check_id'],before,p)
                engine.db.execute('UPDATE producer_candidates SET task_id=? WHERE id=?',(tid,row['id']));admitted.append(tid);status='admitted'
            engine.db.execute('UPDATE producer_candidates SET status=?,evidence=? WHERE id=?',(status,json.dumps(evidence,sort_keys=True),row['id']))
            engine.event(None,None,'producer_reproduced',{'candidate_id':row['id'],'status':status,'evidence':evidence});observed.append({'id':row['id'],'status':status})
    return {'admitted':admitted,'observed':observed,'processed':processed,'remaining_pending':engine.db.execute("SELECT count(*) FROM producer_candidates WHERE status='pending'").fetchone()[0]}


def retry_owner_infrastructure(engine,candidate_id,ident,source,p):
    """Reopen only a proven current owner-check infrastructure observation.

    Passing/admitted findings and malformed or ambiguous rejection remain cached.
    The original reproduction stays in immutable events and content-addressed logs.
    """
    with engine.tx():
        row=engine.db.execute('SELECT * FROM producer_candidates WHERE id=?',(candidate_id,)).fetchone()
        if row is None or row['status']!='rejected' or row['task_id'] is not None:return 'cached'
        try:
            data=json.loads(row['payload']);evidence=json.loads(row['evidence'])
            require_evidence=(isinstance(data,dict) and data.get('source')=='owner_check' and data.get('check_id')==ident
                              and isinstance(evidence,dict) and evidence.get('source')==source
                              and evidence.get('policy_sha256')==engine.config['producer_policy']['sha256']
                              and evidence.get('provenance')=='reproduction_rejected')
            if not require_evidence:return 'cached'
            original={key:value for key,value in evidence.items() if key!='evidence_sha256'}
            if evidence.get('evidence_sha256')!=sha(json.dumps(original,sort_keys=True,separators=(',',':')).encode()):return 'cached'
            result=evidence.get('result');report=evidence.get('report');bound=binding(engine,ident,p)
            if not (isinstance(result,dict) and isinstance(report,dict)
                    and type(result.get('exit_code')) is int and result['exit_code']==2
                    and not result.get('error') and not result.get('cleanup_error') and not result.get('timeout')
                    and result.get('stdout_truncated') is False and result.get('stderr_truncated') is False
                    and result.get('argv')==bound['argv']
                    and report.get('id')==ident and report.get('passed') is False
                    and report.get('classification')=='infrastructure_error' and report.get('source_stable') is True
                    and report.get('exactsourceSHA')==source):return 'cached'
        except (ValueError,TypeError,KeyError):return 'cached'
        if engine.db.execute("SELECT count(*) FROM producer_candidates WHERE status='pending'").fetchone()[0]>=p['queue']['max_open']:
            engine.event(None,None,'producer_retry_deferred',{'candidate_id':candidate_id,'reason':'candidate_queue_full',
                                                           'previous_evidence_sha256':evidence['evidence_sha256']})
            return 'deferred'
        engine.db.execute("UPDATE producer_candidates SET status='pending' WHERE id=? AND status='rejected'",(candidate_id,))
        engine.event(None,None,'producer_infrastructure_retry',{'candidate_id':candidate_id,'check_id':ident,
                                                              'source':source,'binding':bound,
                                                              'previous_evidence_sha256':evidence['evidence_sha256']})
        return 'pending'


def discover(engine,limit=None):
    """One bounded cycle from the owner registry; no agent-supplied execution."""
    p=ready(engine);limit=p['queue']['max_new_per_cycle'] if limit is None else number(limit)
    if limit>p['queue']['max_new_per_cycle']:raise WorkflowError('cycle admission limit exceeded')
    source=digest(engine.config['repo']);registry_sha=sha(json.dumps(p['registry'],sort_keys=True,separators=(',',':')).encode())
    discovered=[];deferred=[]
    for ident in sorted(p['registry']):
        try:
            item=offer(engine,source='owner_check',external_key='owner-registry:'+registry_sha+':'+source+':'+ident,
                       check_id=ident,title='Owner fixed check: '+ident,body='One-shot owner registry discovery; machine reproduction required.',
                       source_ref='owner-policy-sha256:'+engine.config['producer_policy']['sha256'])
            discovered.append(item)
            if item['deduplicated'] and retry_owner_infrastructure(engine,item['id'],ident,source,p)=='deferred':
                deferred.append(ident)
        except WorkflowError as error:
            if str(error)!='candidate queue full':raise
            deferred.append(ident)
    result=scan(engine,limit)
    result.update(discovered=discovered,deferred_registry=deferred,discovery_source=source,registry_sha256=registry_sha)
    return result
