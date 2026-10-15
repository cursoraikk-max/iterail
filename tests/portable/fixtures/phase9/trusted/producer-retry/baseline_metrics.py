"""Read-only fixed-parent evidence summaries. No scheduling or state migration."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from .receipt import build_receipt, canonical_hash


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
