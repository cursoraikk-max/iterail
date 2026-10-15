"""Read-only lifecycle observations. Advice never grants execution or acceptance."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

_STATES = {'queued','running','changed','verifying','verified','ready_review','accepted','failed','expired','invalidated'}
_ACTIVE = {'running','changed','verifying','verified','ready_review'}
_MAX_JSON = 8388608
_ERROR = 'invalid or unavailable guidance request'


def _object(raw):
    if not isinstance(raw,str) or len(raw)>_MAX_JSON: return None
    try:
        value=json.loads(raw)
        return value if isinstance(value,dict) else None
    except (ValueError,RecursionError): return None


def _same(a,b):
    try:
        return json.dumps(a,sort_keys=True,separators=(',',':'),allow_nan=False)==json.dumps(b,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (ValueError,TypeError): return False


def _hash(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def _source(repo):
    """Same content framing as Core, with bounded traversal and input reads."""
    root=Path(repo).resolve();stack=[root];files=[];nodes=0;total=0
    if not root.is_dir(): raise ValueError(_ERROR)
    while stack:
        directory=stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                nodes+=1
                if nodes>20000: raise ValueError(_ERROR)
                path=Path(entry.path)
                if entry.name in {'.git','.workflow','__pycache__'} or path.suffix=='.pyc': continue
                if entry.is_symlink(): raise ValueError(_ERROR)
                if entry.is_dir(follow_symlinks=False): stack.append(path)
                elif entry.is_file(follow_symlinks=False): files.append(path)
    h=hashlib.sha256()
    for path in sorted(files):
        h.update(str(path.relative_to(root)).encode()+b'\0')
        with path.open('rb') as stream:
            while chunk:=stream.read(65536):
                total+=len(chunk)
                if total>67108864: raise ValueError(_ERROR)
                h.update(chunk)
        h.update(b'\0')
    return h.hexdigest()


def _proof(run,task,policy,evidence,bound,source_match):
    if not evidence or source_match is not True: return False
    if type(run['revision']) is not int or type(task['revision']) is not int or run['revision']!=task['revision']: return False
    if type(evidence.get('task_revision')) is not int or evidence['task_revision']!=run['revision']: return False
    if not all(_hash(run[k]) and evidence.get(k)==run[k] for k in ('baseline','candidate','workflow')): return False
    if not _same(evidence.get('policy'),policy): return False
    if evidence.get('source_unchanged') is not True or evidence.get('ownership_valid_at_finish') is not True: return False
    if any(evidence.get(k) for k in ('source_error','execution_error','internal_error','capture_error')): return False
    expected=policy.get('tests')
    if not isinstance(expected,list): return False
    expected=list(expected)
    if bound is not None:
        acceptance=evidence.get('task_acceptance')
        if not isinstance(bound,dict) or not isinstance(bound.get('argv'),list): return False
        if not isinstance(acceptance,dict) or acceptance.get('passed') is not True or not _same(acceptance.get('binding'),bound): return False
        expected.append(bound['argv'])
    results=evidence.get('results')
    if not isinstance(results,list) or len(results)!=len(expected): return False
    errors=('execution_error','cleanup_error','internal_error','capture_error','stream_cleanup_error','guardian_cleanup_error','timeout')
    return all(isinstance(result,dict) and _same(result.get('argv'),argv)
               and type(result.get('exit_code')) is int and result['exit_code']==0
               and not any(result.get(key) for key in errors) for result,argv in zip(results,expected))


def _legacy_next_step(state,workflow,task_id='',run_id='',**extra):
    """Inspect one existing state snapshot; never initialize/reconcile/mutate it."""
    from .core import WorkflowError
    selectors=(task_id,run_id)
    if (extra or any(not isinstance(value,str) for value in selectors)
            or sum(bool(value) for value in selectors)!=1
            or any(value and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value) for value in selectors)):
        raise WorkflowError(_ERROR)
    db=None
    try:
        directory=Path(state)
        path=directory/'tasks.sqlite'
        if directory.is_symlink() or path.is_symlink() or not path.is_file(): raise ValueError(_ERROR)
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        steps=[0]
        def bounded():
            steps[0]+=1
            return steps[0]>1000
        db.set_progress_handler(bounded,1000)
        fields="id,task_id,state,revision,workflow,baseline,candidate,lease_until,deadline,CASE WHEN length(policy)<=8388608 THEN policy END AS policy,CASE WHEN length(evidence)<=8388608 THEN evidence END AS evidence"
        if run_id:
            run=db.execute('SELECT '+fields+' FROM runs WHERE id=? LIMIT 1',(run_id,)).fetchone()
            if run is None: raise ValueError(_ERROR)
            task_id=run['task_id']
        else:
            run=db.execute('SELECT '+fields+' FROM runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1',(task_id,)).fetchone()
        task=db.execute('SELECT id,state,revision FROM tasks WHERE id=? LIMIT 1',(task_id,)).fetchone()
        if task is None or not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',task_id): raise ValueError(_ERROR)
        current=db.execute('SELECT CASE WHEN length(body)<=8388608 THEN body END FROM policy WHERE id=1').fetchone()
        current_policy=_object(current[0]) if current else None
        policy=_object(run['policy']) if run else current_policy
        count=db.execute('SELECT count(*) FROM runs WHERE task_id=?',(task_id,)).fetchone()[0]
        bound=None
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='producer_task_checks'").fetchone():
            row=db.execute('SELECT CASE WHEN length(body)<=8388608 THEN body END FROM producer_task_checks WHERE task_id=? LIMIT 1',(task_id,)).fetchone()
            if row: bound=_object(row[0]) or False
        if bound is not None:
            # Reuse the producer's read-only owner-policy/source identity check;
            # this is a data view, not an Engine or initialized producer.
            from types import SimpleNamespace
            from .producer import policy as producer_policy, binding as producer_binding
            try:
                view=SimpleNamespace(config=current_policy,db=db)
                registry=producer_policy(view)[0]
                expected_bound=producer_binding(view,bound['check_id'],registry)
                if not _same(bound,expected_bound): bound=False
            except (ValueError,TypeError,OSError,sqlite3.Error,KeyError): bound=False
        stored=run['state'] if run else task['state']
        stored=stored if stored in _STATES else 'stale'
        result={'schema_version':1,'task_id':task_id,'run_id':run['id'] if run else None,
                'stored_state':stored,'effective_state':stored,'source_match':None,'evidence_valid':False,
                'next_action':'owner_reassess','reason':'evidence_invalid','requires_token':False,
                'automatic_execution':False,'published':False,'authority':'advisory_only','deployment':'not_recorded'}
        def advice(action,reason,effective=None):
            result.update(next_action=action,reason=reason,requires_token=action in {'continue_work','change','verify','ready_review'})
            if effective: result['effective_state']=effective
            return result
        if not isinstance(policy,dict) or not isinstance(current_policy,dict) or not _same(policy,current_policy): return advice('owner_reassess','evidence_invalid','stale')
        maximum=policy.get('budget',{}).get('max_attempts')
        if type(maximum) is not int or maximum<=0: return advice('owner_reassess','evidence_invalid','stale')
        if not run:
            if bound is False: return advice('owner_reassess','evidence_invalid','evidence_invalid')
            return advice('claim','no_attempt') if count<maximum else advice('owner_reassess','attempts_exhausted','budget_exhausted')
        if not isinstance(run['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',run['id']): raise ValueError(_ERROR)
        try:
            current_source=_source(policy['repo'])
            reference=run['baseline'] if stored=='running' and not run['candidate'] else run['candidate']
            result['source_match']=current_source==reference if _hash(reference) else None
        except (ValueError,OSError,TypeError): pass
        evidence=_object(run['evidence'])
        result['evidence_valid']=_proof(run,task,policy,evidence,bound,result['source_match'])
        if type(run['revision']) is not int or type(task['revision']) is not int or run['revision']!=task['revision']:
            result['evidence_valid']=False
            return advice('owner_reassess','evidence_invalid','stale')
        if bound is False: return advice('owner_reassess','evidence_invalid','evidence_invalid')
        if stored=='accepted':
            if result['source_match'] is not True: return advice('owner_reassess','source_changed','source_changed')
            return advice('done','current_accepted') if result['evidence_valid'] else advice('owner_reassess','evidence_invalid','evidence_invalid')
        times=(run['lease_until'],run['deadline'])
        if not all(type(t) in (int,float) and math.isfinite(t) for t in times): return advice('owner_reassess','evidence_invalid','stale')
        now=time.time()
        expired=stored=='expired' or stored in _ACTIVE and now>=min(times)
        if expired or stored in {'failed','invalidated'}:
            reason='deadline_exhausted' if now>=run['deadline'] else 'lease_expired' if expired else 'failed_attempt'
            return advice('claim',reason,'expired' if expired else stored) if count<maximum else advice('owner_reassess','attempts_exhausted','budget_exhausted')
        if stored=='verifying': return advice('wait','verification_running')
        if stored in _ACTIVE and run['workflow']!=workflow: return advice('use_claimed_controller','controller_mismatch','controller_mismatch')
        if stored not in _ACTIVE: return advice('owner_reassess','evidence_invalid','stale')
        if result['source_match'] is not True:
            return advice('change','candidate_missing' if not run['candidate'] and stored!='running' else 'source_changed','source_changed')
        if stored=='running': return advice('continue_work','work_in_progress')
        if stored=='changed': return advice('verify','verification_required')
        if not result['evidence_valid']: return advice('change','evidence_invalid','evidence_invalid')
        return advice('ready_review','ready_for_review') if stored=='verified' else advice('await_independent_review','independent_review_required')
    except (ValueError,TypeError,OSError,sqlite3.Error,KeyError,RecursionError):
        raise WorkflowError(_ERROR) from None
    finally:
        if db is not None: db.close()


def next_step(state,workflow,task_id='',run_id='',**extra):
    result=_legacy_next_step(state,workflow,task_id,run_id,**extra)
    db=sqlite3.connect((Path(state)/'tasks.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        db.row_factory=sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        config=_object(db.execute('SELECT body FROM policy WHERE id=1').fetchone()[0])
        if config and 'quality_policy' in config:
            from .quality import project
            result['quality']=project(config,db,workflow)[0]
        return result
    finally: db.close()
