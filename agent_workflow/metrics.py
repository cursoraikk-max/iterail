"""Read-only fixed-parent evidence summaries. No scheduling or state migration."""
import argparse
import contextlib
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from .receipt import build_receipt, canonical_hash, normalize_exec_events


KINDS={'call_reserved','attempt_failed','budget_rejected','review_rejected','stopped','superseded','completed'}


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(65536),b''):h.update(chunk)
    return h.hexdigest()


def valid_completion(event):
    receipt=event.get('receipt')
    if not isinstance(receipt,dict) or type(receipt.get('schema_version')) is not int or receipt['schema_version']!=1 or receipt.get('complete') is not True:return False,'missing or unsupported complete receipt'
    try:
        original={k:v for k,v in receipt.items() if k not in ('schema_version','complete','receipt_id')}
        rebuilt=build_receipt(original)
        if rebuilt['receipt_id']!=receipt.get('receipt_id'):return False,'receipt identity changed'
        if event['source']!=receipt['candidate']:return False,'receipt source version mismatch'
        required={x['log_sha256'] for x in receipt['tests']}
        required.add(receipt['reviewer']['log_sha256'])
        for value in (receipt['backend'].get('log_sha256'),receipt['reviewer'].get('verdict_sha256')):
            if value:required.add(value)
        logs=event.get('logs')
        if not isinstance(logs,list) or not logs:return False,'raw log references missing'
        observed=set()
        for log in logs:
            if not isinstance(log,dict) or not isinstance(log.get('path'),str) or not isinstance(log.get('sha256'),str):return False,'invalid log reference'
            if file_hash(log['path'])!=log['sha256']:return False,'raw log hash mismatch'
            observed.add(log['sha256'])
        if not required<=observed:return False,'receipt raw evidence missing'
    except (ValueError,TypeError,KeyError,OSError):return False,'invalid or unavailable receipt evidence'
    return True,None


def build_metrics(cohort,events):
    if not isinstance(cohort,dict) or not isinstance(cohort.get('id'),str) or not cohort['id']:raise ValueError('frozen cohort id required')
    parents=cohort.get('parent_goal_ids')
    if not isinstance(parents,list) or any(not isinstance(p,str) or not p for p in parents) or len(set(parents))!=len(parents):raise ValueError('unique frozen parent goals required')
    if not isinstance(events,list):raise ValueError('event list required')
    seen={};states={p:'not_completed' for p in parents};successes={};attempts=set();by_kind=Counter();findings=[];roles=Counter();calls=0;complete=True
    for event in events:
        if not isinstance(event,dict) or not isinstance(event.get('id'),str) or not event['id']:raise ValueError('event identity required')
        identity=canonical_hash(event)
        if event['id'] in seen:
            if seen[event['id']]!=identity:raise ValueError('conflicting duplicate event')
            continue
        seen[event['id']]=identity
        parent=event.get('parent_goal_id');kind=event.get('kind')
        if parent not in states:raise ValueError('event parent outside frozen cohort')
        if kind not in KINDS:raise ValueError('unknown metric event kind')
        if not isinstance(event.get('source'),str) or not re.fullmatch('[0-9a-f]{64}',event['source']):raise ValueError('event version identity required')
        attempt=event.get('attempt_id')
        if attempt is not None:
            if not isinstance(attempt,str) or not attempt:raise ValueError('invalid attempt identity')
            attempts.add(attempt)
        by_kind[kind]+=1
        if kind=='call_reserved':
            calls+=1;call_kind=event.get('call_kind','')
            roles['backend' if call_kind.startswith('backend:') else ('local' if call_kind else 'unclassified')]+=1
        elif kind=='completed':
            valid,reason=valid_completion(event)
            if valid:states[parent]='succeeded';successes[parent]={'attempt':attempt,'source':event['source']}
            else:
                complete=False
                if states[parent]!='succeeded':states[parent]='unknown'
                findings.append({'event_id':event['id'],'parent_goal_id':parent,'classification':'unverified_completion','reason':reason})
        elif kind in ('attempt_failed','budget_rejected','review_rejected','stopped'):
            prior=successes.get(parent)
            regression=(states[parent]=='succeeded' and prior is not None
                        and not (kind=='stopped' and attempt is None)
                        and (event['source']!=prior['source'] or (attempt is not None and attempt!=prior['attempt'])))
            if regression:
                states[parent]='unknown';complete=False
                findings.append({'event_id':event['id'],'parent_goal_id':parent,'classification':'post_success_failure','kind':kind})
            elif states[parent] not in ('succeeded','unknown'):states[parent]='failed'
    totals={'registered':len(parents),**{k:sum(v==k for v in states.values()) for k in ('succeeded','failed','not_completed','unknown')}}
    return {'schema_version':1,'cohort_id':cohort['id'],'parent_goal_ids':list(parents),'totals':totals,
            'attempts':len(attempts),'calls':calls,'calls_by_role':{k:roles[k] for k in ('backend','local','unclassified')},
            'by_kind':dict(sorted(by_kind.items())),'cost':{'currency':None,'amount':None,'status':'unknown'},
            'interventions':{'status':'unknown','count':None},'historical_verified_parents':len(successes),'complete':complete,'findings':findings}


def read_state(state,cohort=None):
    """Open existing runner SQLite with mode=ro; never construct Runner or rewrite files."""
    root=Path(state).resolve();database=root if root.is_file() else root/'runner.sqlite'
    db=sqlite3.connect('file:'+quote(str(database),safe='/')+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN')
        meta=dict(db.execute('SELECT * FROM meta').fetchone())
        config=json.loads(meta['config']);jobs=[dict(x) for x in db.execute('SELECT * FROM jobs ORDER BY rowid')]
        calls=[dict(x) for x in db.execute('SELECT * FROM calls ORDER BY rowid')]
        raw=[dict(x) for x in db.execute('SELECT * FROM events ORDER BY seq')]
    finally:db.close()
    if cohort is None:
        parents=[c['id'] for c in config['checks']]
        cohort={'id':'runner-'+canonical_hash({'config':config,'started':meta['started']}),'parent_goal_ids':parents}
    parents=set(cohort['parent_goal_ids']);lookup={j['id']:j for j in jobs};events=[];current_attempt={};current_source={};log_refs=[]
    for call in calls:
        try:result=json.loads(call['result'] or '{}')
        except ValueError:continue
        if isinstance(result,dict) and isinstance(result.get('output'),str):
            expected=result.get('saved_sha256') or (result.get('output_sha256') if not result.get('truncated') else None)
            if expected:log_refs.append({'path':result['output'],'sha256':expected})
    for row in raw:
        try:data=json.loads(row['data'])
        except ValueError:data={}
        job=lookup.get(row['job_id']);kind=row['kind']
        if job is None:continue
        parent=job['check_id']
        if parent not in parents:raise ValueError('runner job outside frozen cohort')
        source=current_source.get(job['id'],job['baseline'])
        if kind=='attempt_prepared':current_attempt[job['id']]=job['id']+':'+str(Path(data.get('slot','unknown')).name)
        if kind in ('attempt_prepared','failed_candidate_retained','source_patch_qualified','completion_receipt','review_verdict','complete'):
            observed=data.get('candidate') or data.get('digest') or data.get('baseline')
            if isinstance(observed,str) and re.fullmatch('[0-9a-f]{64}',observed):source=observed;current_source[job['id']]=source
        mapped={'call_reserved':'call_reserved','attempt_failed':'attempt_failed','admission_budget_blocked':'budget_rejected',
                'queued_baseline_superseded':'superseded','complete':'completed','integration_recovered':'completed'}.get(kind)
        if kind=='review_verdict' and data.get('approved') is False:mapped='review_rejected'
        if not mapped:continue
        event={'id':'seq:'+str(row['seq']),'kind':mapped,'parent_goal_id':parent,'attempt_id':current_attempt.get(job['id']),'source':source}
        if mapped=='call_reserved':event['call_kind']=data.get('kind','')
        if mapped=='completed':
            receipt_path=database.parent/'receipts'/(job['id']+'.json')
            try:receipt=json.loads(receipt_path.read_text())
            except (ValueError,OSError):receipt=None
            if isinstance(receipt,dict):
                if any(receipt.get(k)!=job.get(k) for k in ('task_id','run_id','baseline','candidate')) or receipt.get('head')!=job.get('candidate_commit') or receipt.get('runtime')!=meta.get('runtime'):
                    receipt=None
            event['receipt']=receipt;event['logs']=[]
            if isinstance(receipt,dict):
                required={x.get('log_sha256') for x in receipt.get('tests',[]) if isinstance(x,dict)}
                reviewer=receipt.get('reviewer',{})
                required.add(reviewer.get('log_sha256'));required.add(receipt.get('backend',{}).get('log_sha256'))
                event['logs']=[ref for ref in log_refs if ref['sha256'] in required]
                if reviewer.get('artifact_path') and reviewer.get('verdict_sha256'):event['logs'].append({'path':reviewer['artifact_path'],'sha256':reviewer['verdict_sha256']})
        events.append(event)
    if meta.get('stopped'):
        for parent in cohort['parent_goal_ids']:
            events.append({'id':'meta:stop:'+parent,'kind':'stopped','parent_goal_id':parent,'attempt_id':None,'source':canonical_hash(config)})
    summary=build_metrics(cohort,events)
    backend=[c for c in calls if c['kind'].startswith('backend:')];local=[c for c in calls if not c['kind'].startswith('backend:')]
    summary.update(calls=len(backend),local_calls=len(local),total_process_reservations=len(calls),calls_scope='backend_session_reservations',
                   calls_by_role={'backend':len(backend),'local':len(local),'unclassified':0},
                   attempts=sum(j['attempts'] for j in jobs),attempts_scope='recorded_runner_attempts',
                   historical={'completed_jobs':sum(j['phase']=='complete' for j in jobs),'legacy_reported':sum(j['phase']=='complete' and not (database.parent/'receipts'/(j['id']+'.json')).exists() for j in jobs),'registered_jobs':len(jobs)})
    summary['by_kind']['superseded_jobs']=sum(j['phase']=='superseded' for j in jobs)
    durations={'backend_seconds':0.0,'local_seconds':0.0,'unknown_durations':0,'timeouts':0,'usage_missing':0}
    for call in calls:
        try:result=json.loads(call['result'] or '{}')
        except ValueError:result={}
        duration=result.get('duration')
        if type(duration) in (int,float):durations['backend_seconds' if call['kind'].startswith('backend:') else 'local_seconds']+=duration
        else:durations['unknown_durations']+=1
        if result.get('exit_code')==124 or result.get('reason')=='timeout':durations['timeouts']+=1
    usage_events=[json.loads(e['data']) for e in raw if e['kind']=='backend_identity']
    durations['usage_missing']=max(0,len(backend)-sum(isinstance(e.get('usage'),list) and bool(e['usage']) for e in usage_events))
    summary['observations']=durations
    summary['observations']['duration_scope']='sum of recorded process durations; unknown durations counted separately'
    summary['ledger']={'reserved_backend_calls':meta.get('calls'),'stopped':bool(meta.get('stopped')),'runtime':meta.get('runtime'),'started':meta.get('started')}
    if meta.get('calls')!=len(backend):summary['complete']=False;summary['findings'].append({'classification':'backend_ledger_count_mismatch'})
    return summary


def _metric_require(value, message):
    if not value: raise ValueError(message)


def _metric_object(raw):
    def pairs(items):
        result={}
        for key,value in items:
            _metric_require(key not in result,'duplicate evidence member')
            result[key]=value
        return result
    def constant(value): raise ValueError('nonfinite evidence value')
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    _metric_require(isinstance(value,dict),'object evidence required')
    return value


def _owner_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class _EvolutionSnapshot:
    """Pin existing database snapshots; all connections are closed by the caller."""
    def __init__(self,root):
        self.root=root;self.connections={};self.stack=contextlib.ExitStack()

    def __enter__(self): return self

    def __exit__(self,*args): return self.stack.__exit__(*args)

    def scoped(self,path):
        path=Path(path)
        _metric_require(path.is_absolute() and path.is_relative_to(self.root),'evidence path outside owner')
        current=self.root
        for part in path.relative_to(self.root).parts:
            current=current/part
            _metric_require(not current.is_symlink(),'evidence symlink unsupported')
        _metric_require(path.resolve()==path and path.is_file(),'existing regular evidence required')
        return path

    def raw(self,path):
        path=self.scoped(path)
        _metric_require(path.stat().st_size<=8*1024*1024,'evidence byte ceiling exceeded')
        raw=path.read_bytes()
        _metric_require(len(raw)<=8*1024*1024,'evidence byte ceiling exceeded')
        return raw

    def rows(self,path,sql,args=()):
        path=self.scoped(path)
        if path not in self.connections:
            db=sqlite3.connect('file:'+quote(str(path),safe='/')+'?mode=ro',uri=True)
            self.stack.callback(db.close);db.row_factory=sqlite3.Row
            db.execute('BEGIN');self.connections[path]=db
        return [dict(row) for row in self.connections[path].execute(sql,args)]

    def one(self,path,sql,args=()):
        rows=self.rows(path,sql,args)
        _metric_require(len(rows)==1,'unique persisted evidence required')
        return rows[0]


def _evolution_process(snapshot,state,call):
    """A finished guardian/PID journal proves a process; budget counters do not."""
    _metric_require(call['phase']=='finished','process not terminal')
    result=_metric_object(call['result'])
    _metric_require(re.fullmatch(r'call-[0-9a-f]{12}',call['id']) is not None,'invalid process identity')
    output=Path(state)/'logs'/(call['id']+'.log')
    pid=_metric_object(snapshot.raw(Path(state)/'logs'/(call['id']+'.pid')))
    _metric_require(result.get('call_id')==call['id']==pid.get('call_id')
                    and type(result.get('child_pid')) is int and result['child_pid']>0
                    and type(result.get('guardian_pid')) is int and result['guardian_pid']>0
                    and result['child_pid']==pid.get('pid') and result['guardian_pid']==pid.get('guardian')
                    and result.get('started')==pid.get('started') and result.get('group_termination_confirmed') is True,
                    'process identity/group observation unconfirmed')
    _metric_require(result.get('output')==str(output),'protected process output identity changed')
    raw=snapshot.raw(output)
    _metric_require(hashlib.sha256(raw).hexdigest()==result.get('saved_sha256'),'protected raw process hash mismatch')
    return result,raw


def _evolution_source(root):
    """Observe the owner-declared current project without Git subprocesses."""
    root=Path(root)
    _metric_require(root.is_absolute() and root.is_dir() and root.resolve()==root and not root.is_symlink(),'owner project source unavailable')
    h=hashlib.sha256()
    for path in sorted(root.rglob('*')):
        rel=path.relative_to(root)
        if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or path.suffix=='.pyc':continue
        _metric_require(not path.is_symlink(),'owner project source symlink')
        if path.is_file():
            h.update(str(rel).encode()+b'\0')
            with path.open('rb') as stream:
                while chunk:=stream.read(65536):h.update(chunk)
            h.update(b'\0')
    return h.hexdigest()


def _evolution_completion(snapshot,record,release,config,meta,owner_events):
    state=Path(record['child_state']);db=state/'runner.sqlite'
    job=snapshot.one(db,'SELECT * FROM jobs WHERE id=?',(record['job'],))
    child_meta=snapshot.one(db,'SELECT * FROM meta')
    _metric_require(job['check_id'] in {check['id'] for check in config['checks']},'completion outside original parent goals')
    path=state/'receipts'/(job['id']+'.json');raw=snapshot.raw(path);receipt=_metric_object(raw)
    rebuilt=build_receipt({key:value for key,value in receipt.items() if key not in {'schema_version','complete','receipt_id'}})
    _metric_require(rebuilt==receipt and child_meta['runtime']==release['fingerprint']==receipt['runtime'],
                    'canonical receipt/release identity mismatch')
    result=_metric_object(record['result'])
    _metric_require(record['phase']=='done' and result.get('phase')=='complete' and result.get('job_id')==job['id']
                    and result.get('active_fingerprint')==release['fingerprint'] and result.get('receipt')==receipt,
                    'owner completion is not the recorded canonical receipt')
    for key in ('task_id','run_id','baseline','candidate'):
        _metric_require(job.get(key)==receipt.get(key),'receipt/job identity mismatch')
    _metric_require(job['phase']=='complete' and job['base_head']==receipt['base_head']
                    and job['candidate_commit']==receipt['head'],'receipt/job commit mismatch')
    integration=snapshot.one(db,'SELECT * FROM integration WHERE job_id=?',(job['id'],))
    _metric_require(integration['phase']=='done' and integration['expected']==receipt['base_head']
                    and integration['candidate']==receipt['head'],'canonical local CAS evidence absent')
    slot=Path(job['slot']);snapshot.scoped(slot/'core-state/tasks.sqlite')
    _metric_require(slot.is_relative_to(state),'Core artifact outside bound child')
    core=slot/'core-state/tasks.sqlite'
    run=snapshot.one(core,'SELECT * FROM runs WHERE id=?',(receipt['run_id'],))
    task=snapshot.one(core,'SELECT * FROM tasks WHERE id=?',(receipt['task_id'],))
    evidence=_metric_object(run['evidence']);core_policy=_metric_object(snapshot.one(core,'SELECT body FROM policy WHERE id=1')['body'])
    _metric_require(run['state']=='accepted' and task['state']=='accepted' and run['task_id']==receipt['task_id']
                    and run['workflow']==receipt['workflow'] and run['baseline']==receipt['baseline']
                    and run['candidate']==receipt['candidate'] and _metric_object(run['policy'])==core_policy
                    and evidence==receipt['core_state']['evidence'] and evidence.get('policy')==core_policy
                    and evidence.get('source_unchanged') is True and evidence.get('ownership_valid_at_finish') is True,
                    'canonical accepted Core evidence mismatch')
    job_spec=_metric_object(snapshot.raw(slot/'job.json'))
    controller=Path(job_spec['controller'])
    expected_controller=Path(release['path']) if config['project']['kind']=='target' else slot/'controller'
    _metric_require(controller==expected_controller and job_spec.get('state')==str(slot/'core-state'), 'frozen job controller/Core scope changed')
    snapshot.scoped(controller/'agent_workflow'/'__init__.py')
    _metric_require(_evolution_source(slot/'candidate')==receipt['candidate']
                    and _evolution_source(controller/'agent_workflow')==receipt['workflow'],
                    'recorded candidate/controller source drift')
    failures=snapshot.rows(core,"SELECT data FROM events WHERE run_id=? AND kind='verification_failed'",(run['id'],))
    _metric_require(any((value:=_metric_object(row['data'])).get('baseline')==receipt['baseline']==value.get('candidate')
                        for row in failures),'exact-source failed baseline evidence absent')
    prepared=snapshot.rows(db,"SELECT at,data FROM events WHERE job_id=? AND kind='attempt_prepared' ORDER BY seq",(job['id'],))
    times=[row['at'] for row in prepared if _metric_object(row['data']).get('slot')==str(slot)]
    _metric_require(times,'canonical attempt preparation absent');since=max(times)
    check=next(check for check in config['checks'] if check['id']==job['check_id'])
    expected=[[arg.replace('{repo}',str(slot/'candidate')) for arg in argv] for argv in config['tests']+[check['argv']]]
    machine=[];backends={}
    for call in snapshot.rows(db,'SELECT * FROM calls WHERE job_id=? ORDER BY rowid',(job['id'],)):
        if call['kind'] not in {'machine-test','backend:worker','backend:reviewer'}:continue
        observed=_metric_object(call['result'] or '{}')
        if observed.get('started',0)<since:continue
        observed,raw_log=_evolution_process(snapshot,state,call)
        _metric_require(type(observed.get('exit_code')) is int and observed['exit_code']==0
                        and observed.get('truncated') is False
                        and len(raw_log)==observed.get('saved_bytes')==observed.get('output_bytes'),
                        'completion process evidence unsuccessful or incomplete')
        log_sha=hashlib.sha256(raw_log).hexdigest()
        if call['kind']=='machine-test':machine.append((observed,log_sha))
        else:
            _metric_require(call['kind'] not in backends,'duplicate backend completion identity')
            protocol=normalize_exec_events(raw_log.decode('utf-8'))
            _metric_require(protocol['valid'] and protocol['model_completed'],'valid completed backend protocol absent')
            backends[call['kind']]=(observed,log_sha)
    _metric_require(set(backends)=={'backend:worker','backend:reviewer'},'independent backend observations absent')
    _metric_require([item[0]['argv'] for item in machine]==expected==[item['argv'] for item in receipt['tests']]
                    and expected==[item['argv'] for item in evidence['results']]
                    and len(machine)==len(evidence['results'])
                    and all(type(item.get('exit_code')) is int and item['exit_code']==0 and not item.get('execution_error')
                            for item in evidence['results']), 'fixed owner machine/Core commands mismatch')
    for (_,log_sha),test in zip(machine,receipt['tests']):
        _metric_require(type(test.get('exit_code')) is int and test['exit_code']==0 and test['log_sha256']==log_sha,
                        'canonical machine raw hash mismatch')
    worker,worker_sha=backends['backend:worker'];reviewer,reviewer_sha=backends['backend:reviewer']
    _metric_require(receipt['backend']['log_sha256']==worker_sha and receipt['backend']['version']==worker.get('executed_version')
                    and receipt['reviewer']['log_sha256']==reviewer_sha and receipt['reviewer']['version']==reviewer.get('executed_version'),
                    'executed backend version/raw identity mismatch')
    verdict_path=slot/'verdict.json';verdict_raw=snapshot.raw(verdict_path);verdict=_metric_object(verdict_raw)
    _metric_require(hashlib.sha256(verdict_raw).hexdigest()==receipt['reviewer']['verdict_sha256']
                    and receipt['reviewer'].get('artifact_path')==str(verdict_path)
                    and verdict.get('approved') is True and verdict.get('candidate')==receipt['candidate'],
                    'independent reviewer artifact mismatch')
    proofs=[event for event in owner_events if event['kind']=='independent_proof']
    proved=False
    for event in proofs:
        proof=_metric_object(event['data'])
        if proof.get('active_release')!=release['id'] or proof.get('receipt_sha256')!=hashlib.sha256(raw).hexdigest():continue
        _metric_require(proof.get('proof_sha256')==_owner_hash({key:value for key,value in proof.items() if key!='proof_sha256'})
                        and proof.get('active_epoch')==record['epoch'] and proof.get('owner_config_sha256')==meta['config_sha']
                        and proof.get('receipt')==receipt,'owner independent proof identity mismatch')
        proved=True;break
    _metric_require(proved,'bound owner independent proof absent')
    return {'parent_goal_id':job['check_id'],'job_id':job['id'],'run_id':run['id'],'source_sha256':receipt['candidate'],
            'head':receipt['head'],'receipt_id':receipt['receipt_id'],'receipt_sha256':hashlib.sha256(raw).hexdigest(),
            'release_id':release['id'],'fingerprint':release['fingerprint'],'epoch':record['epoch']}


def evolution_summary(state):
    """Read existing Evolution history without initialization, dispatch or mutation.

    Totals describe verified original-parent engineering history. Explicit source
    provenance prevents that history from asserting acceptance of changed source.
    """
    path=Path(state).absolute()
    _metric_require(not path.is_symlink(),'owner state symlink unsupported')
    database=path if path.is_file() else path/'evolution.sqlite';root=database.parent.resolve()
    _metric_require(database.name=='evolution.sqlite' and database.is_file() and database.resolve()==database,
                    'existing Evolution owner ledger required')
    with _EvolutionSnapshot(root) as snapshot:
        meta=snapshot.one(database,'SELECT * FROM meta WHERE id=1');config=_metric_object(meta['config'])
        _metric_require(_owner_hash(config)==meta['config_sha'],'original owner policy identity mismatch')
        parents=[check['id'] for check in config['checks']]
        _metric_require(all(isinstance(parent,str) and parent for parent in parents) and len(set(parents))==len(parents),
                        'unique fixed owner parent goals required')
        project=config['project'];_metric_require(project['kind'] in {'controller','target'} and isinstance(project['id'],str),'owner ProjectSpec invalid')
        releases=snapshot.rows(database,'SELECT * FROM releases ORDER BY rowid')
        reservations=snapshot.rows(database,'SELECT * FROM reservations ORDER BY rowid')
        events=snapshot.rows(database,'SELECT * FROM events ORDER BY seq')
        completions=snapshot.rows(database,'SELECT * FROM completions ORDER BY rowid')
        activations=snapshot.rows(database,'SELECT * FROM activation ORDER BY rowid')
        by_release={row['id']:{'release_id':row['id'],'fingerprint':row['fingerprint'],'epochs':set(),
                              'reserved_calls':0,'actual_calls':0,'completed_goals':set(),'failed_goals':set(),
                              'unknown_goals':set(),'accepted_sources':[]} for row in releases}
        release_rows={row['id']:row for row in releases};states={parent:'not_completed' for parent in parents}
        findings=[];accepted=[];seen_calls=set();actual=0;unknown_calls=0;exec_turns=0;unknown_turns=0
        if releases:by_release[releases[0]['id']]['epochs'].add(0)
        def unknown(reason,*,release_id=None,parent=None,identity=None):
            item={'classification':reason}
            if release_id is not None:item['release_id']=release_id
            if parent is not None:item['parent_goal_id']=parent
            if identity is not None:item['identity']=identity
            findings.append(item)
            if parent in states:
                if states[parent]!='succeeded':states[parent]='unknown'
                if release_id in by_release:by_release[release_id]['unknown_goals'].add(parent)
        for intent in activations:
            if intent['phase']=='done' and intent['candidate'] in by_release and type(intent['epoch']) is int:
                by_release[intent['candidate']]['epochs'].add(intent['epoch']+1)
        for row in reservations:
            rid=row['release_id'];release=by_release.get(rid)
            if release is None:unknown('reservation_release_missing',identity=row['id']);continue
            if type(row['calls']) is not int or row['calls']<0:unknown('reserved_call_count_unknown',release_id=rid,identity=row['id']);continue
            release['reserved_calls']+=row['calls']
            if type(row['active_epoch']) is int:release['epochs'].add(row['active_epoch'])
            try:
                facts=_metric_object(row['result'] or '{}')
                if row['kind'].startswith('supervised:backend:'):
                    state_root=Path(row['child_state']);child_meta=snapshot.one(state_root/'runner.sqlite','SELECT * FROM meta')
                    _metric_require(child_meta['runtime']==release['fingerprint'],'backend child/release fingerprint mismatch')
                    call=snapshot.one(state_root/'runner.sqlite','SELECT * FROM calls WHERE id=?',(facts['runner_call'],))
                    _metric_require(row['kind']=='supervised:'+call['kind'] and call['kind'] in {'backend:worker','backend:reviewer'},'backend kind identity mismatch')
                    observed,raw=_evolution_process(snapshot,state_root,call)
                    _metric_require(facts.get('terminal')==observed,'owner/child terminal evidence mismatch')
                    identity=(str(state_root),call['id'])
                    if identity in seen_calls:continue
                    seen_calls.add(identity);actual+=1;release['actual_calls']+=1
                    if (observed.get('truncated') is False and len(raw)==observed.get('output_bytes')==observed.get('saved_bytes')
                            and type(observed.get('exit_code')) is int):
                        protocol=normalize_exec_events(raw.decode('utf-8'))
                        if observed['exit_code']==0 and protocol['valid'] and protocol['model_completed']:exec_turns+=1
                        elif not protocol['valid']:unknown_turns+=1
                    else:unknown_turns+=1
                if row['kind']=='attempt' and row['phase']=='finished':
                    child=facts.get('child_result')
                    if isinstance(child,dict) and child.get('phase') in {'failed','blocked'}:
                        parent=child.get('check_id') or row['check_id']
                        _metric_require(parent in states and child.get('active_fingerprint')==release['fingerprint'],'failed attempt parent/release unknown')
                        if states[parent] not in {'succeeded','unknown'}:states[parent]='failed'
                        release['failed_goals'].add(parent)
            except (ValueError,TypeError,KeyError,OSError,sqlite3.Error,RecursionError):
                if row['kind'].startswith('supervised:backend:'):unknown_calls+=1;unknown_turns+=1
                unknown('reservation_evidence_unknown',release_id=rid,parent=row['check_id'] if row['kind']=='attempt' else None,identity=row['id'])
        for record in completions:
            rid=record['release_id'];parent=None
            try:
                _metric_require(rid in release_rows,'completion release missing')
                job=snapshot.one(Path(record['child_state'])/'runner.sqlite','SELECT * FROM jobs WHERE id=?',(record['job'],))
                parent=job['check_id'];proof=_evolution_completion(snapshot,record,release_rows[rid],config,meta,events)
                states[parent]='succeeded';by_release[rid]['completed_goals'].add(parent)
                by_release[rid]['accepted_sources'].append(proof);accepted.append(proof)
            except (ValueError,TypeError,KeyError,OSError,sqlite3.Error,RecursionError):
                if parent is None:
                    try:parent=_metric_object(record['result']).get('check_id')
                    except (ValueError,TypeError,RecursionError):pass
                unknown('completion_evidence_unknown',release_id=rid,parent=parent,identity=record['job'])
        # Child completion flags with no corresponding owner proof remain unknown.
        completion_keys={(row['child_state'],row['job']) for row in completions}
        child_states=sorted({row['child_state'] for row in reservations if isinstance(row['child_state'],str)})
        for child in child_states:
            try:
                for job in snapshot.rows(Path(child)/'runner.sqlite',"SELECT id,check_id FROM jobs WHERE phase='complete' ORDER BY rowid"):
                    if (child,job['id']) not in completion_keys:unknown('unbound_child_completion',parent=job['check_id'],identity=job['id'])
            except (ValueError,TypeError,OSError,sqlite3.Error):unknown('child_history_unavailable',identity=child)
        current=None;current_parents=[]
        try:
            current=_evolution_source(config['repo'])
            current_parents=[parent for parent in parents if any(item['parent_goal_id']==parent and item['source_sha256']==current for item in accepted)]
        except (ValueError,TypeError,OSError):unknown('current_source_unavailable')
        if accepted and not current_parents:unknown('current_source_has_no_matching_receipt')
        if type(meta['calls']) is not int or sum(item['reserved_calls'] for item in by_release.values())!=meta['calls']:
            unknown('reserved_call_ledger_mismatch')
        if unknown_turns:unknown('backend_model_protocol_unknown')
        output_releases=[]
        for release in by_release.values():
            output_releases.append(dict(release,epochs=sorted(release['epochs']),
                                        **{name:[parent for parent in parents if parent in release[name]]
                                           for name in ('completed_goals','failed_goals','unknown_goals')}))
        mode=config.get('execution_mode');native=mode=='native_fixture'
        return {'schema_version':1,'evidence_kind':'readonly_evolution_ledger','project_id':project['id'],'project_kind':project['kind'],
                'parent_goal_ids':parents,'totals':{'registered':len(parents),**{name:sum(value==name for value in states.values())
                    for name in ('succeeded','failed','unknown','not_completed')}},
                'ledger':{'reserved_calls':meta['calls'],'actual_calls':actual,**{key:meta[key] for key in ('started','deadline','blocked','active','epoch')},
                          'stopped':bool(meta['stopped']),'unknown_actual_calls':unknown_calls},
                'by_release':output_releases,'cost':{'amount':None,'currency':None,'status':'unknown'},'complete':not findings,'findings':findings,
                'current_source':{'sha256':current,'verified_parent_goal_ids':current_parents,'acceptance_scope':'matching recorded receipt source only'},
                'provenance':{'execution_mode':mode,'native_fixture':native,'model_benefit':False,
                              'provider_integration_verified':mode=='provider' and bool(accepted) and exec_turns>0},
                'observations':{'completed_exec_turns':exec_turns,'native_simulated_turns':exec_turns if native else 0,
                                'successful_model_turns':exec_turns if mode=='provider' else 0,'unknown_model_turns':unknown_turns}}


def main():
    p=argparse.ArgumentParser(description='Read-only fixed-cohort evidence summary')
    p.add_argument('--state');p.add_argument('--cohort');p.add_argument('--events');args=p.parse_args()
    try:
        cohort=json.loads(Path(args.cohort).read_text()) if args.cohort else None
        if args.state:result=read_state(args.state,cohort)
        elif cohort is not None and args.events:result=build_metrics(cohort,json.loads(Path(args.events).read_text()))
        else:raise ValueError('use --state or --cohort and --events')
        print(json.dumps({'ok':True,'result':result},sort_keys=True))
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error) as error:
        print(json.dumps({'ok':False,'error':str(error)}));raise SystemExit(1)


if __name__=='__main__':main()
