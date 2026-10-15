"""Owner-bound semantic admission; machine evidence remains Core's proof."""
import hashlib
import json
import math
import os
import stat
from types import SimpleNamespace
from pathlib import Path
import time
from .guidance import _hash, _source, _proof, _object, _same

DIMS = {'goal_alignment','complexity','architecture','safety','documentation','debt','ui'}


def strict(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('duplicate member')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite')))


def keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected): raise ValueError('invalid shape')


def text(value):
    if not isinstance(value,str) or not value.strip() or len(value)>4096: raise ValueError('invalid text')


def strings(value, nonempty=True):
    if not isinstance(value,list) or len(value)>128 or (nonempty and not value): raise ValueError('invalid list')
    for item in value: text(item)


def external(path, repo, must_exist=True):
    if not isinstance(path,str) or not Path(path).is_absolute(): raise ValueError('external file required')
    file=Path(path); resolved=file.resolve()
    if file.is_symlink() or resolved==repo or repo in resolved.parents or must_exist and not stat.S_ISREG(file.stat().st_mode): raise ValueError('external regular file required')
    return file


def read_regular(file,maximum=1048576):
    """Reject special files even if replaced between stat and open."""
    fd=os.open(file,os.O_RDONLY | os.O_NONBLOCK | getattr(os,'O_NOFOLLOW',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>maximum: raise ValueError('bounded regular file required')
        chunks=[]; remaining=maximum+1
        while remaining:
            chunk=os.read(fd,min(65536,remaining))
            if not chunk: break
            chunks.append(chunk); remaining-=len(chunk)
        raw=b''.join(chunks)
        if len(raw)>maximum: raise ValueError('input too large')
        return raw
    finally: os.close(fd)


def load(config):
    descriptor=config['quality_policy']; keys(descriptor, {'path','sha256'})
    if not _hash(descriptor['sha256']): raise ValueError('invalid hash')
    repo=Path(config['repo']).resolve(); file=external(descriptor['path'],repo)
    raw=read_regular(file)
    if len(raw)>1048576 or hashlib.sha256(raw).hexdigest()!=descriptor['sha256']: raise ValueError('policy changed')
    p=strict(raw); keys(p, {'schema_version','goals','assessment_path','trusted_reviewers','max_assessment_age_seconds','allowed_advisories','ui_required','bootstrap'})
    if type(p['schema_version']) is not int or p['schema_version'] not in {1,2}: raise ValueError('version')
    for field in ('goals','trusted_reviewers'):
        strings(p[field])
        if len(set(p[field]))!=len(p[field]): raise ValueError('duplicate identity')
    strings(p['allowed_advisories'],False)
    if len(set(p['allowed_advisories']))!=len(p['allowed_advisories']) or set(p['allowed_advisories'])-{'complexity','documentation','debt'}: raise ValueError('advisories')
    if type(p['ui_required']) is not bool or type(p['max_assessment_age_seconds']) is not int or p['max_assessment_age_seconds']<=0: raise ValueError('policy types')
    external(p['assessment_path'],repo,must_exist=False)
    keys(p['bootstrap'],{'external_key','source_sha256'}); text(p['bootstrap']['external_key'])
    if not _hash(p['bootstrap']['source_sha256']): raise ValueError('bootstrap hash')
    return p


def payload(task):
    return hashlib.sha256(json.dumps({k:task[k] for k in ('source','external_key','title','body','revision')},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def assessment(p, config, db, source, worker=None):
    raw=read_regular(external(p['assessment_path'],Path(config['repo']).resolve()))
    if len(raw)>1048576: raise ValueError('assessment size')
    a=strict(raw); expected={'schema_version','source_sha256','proof_run_id','reviewer','created_at','dimensions','candidates'}
    if p['schema_version']==2: expected.add('goal_coverage')
    keys(a,expected)
    if type(a['schema_version']) is not int or a['schema_version']!=p['schema_version'] or not _hash(a['source_sha256']): raise ValueError('assessment version')
    text(a['proof_run_id']); text(a['reviewer'])
    stamp=a['created_at']
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp>time.time()+60: raise ValueError('assessment time')
    if time.time()-stamp>p['max_assessment_age_seconds'] or a['source_sha256']!=source: raise LookupError('assessment stale')
    run=db.execute('SELECT * FROM runs WHERE id=?',(a['proof_run_id'],)).fetchone()
    if not run or a['reviewer'] not in p['trusted_reviewers'] or a['reviewer']==run['worker'] or a['reviewer']==worker: raise ValueError('independence')
    for active in db.execute("SELECT worker FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review')"):
        if active['worker']==a['reviewer']: raise ValueError('independence')
    keys(a['dimensions'],DIMS)
    for name,dim in a['dimensions'].items():
        keys(dim,{'status','reason','evidence'}); text(dim['reason']); strings(dim['evidence'],False)
        if dim['status'] not in {'pass','block','advisory','unknown','not_applicable'}: raise ValueError('status')
        if dim['status'] in {'pass','block','advisory'} and not dim['evidence']: raise ValueError('evidence')
        if name=='ui':
            if dim['status']=='not_applicable' and p['ui_required']: raise ValueError('ui required')
        elif dim['status']=='not_applicable': raise ValueError('mandatory dimension')
    if not isinstance(a['candidates'],list) or len(a['candidates'])>128: raise ValueError('candidates')
    seen=set()
    for c in a['candidates']:
        keys(c,{'task_id','task_revision','task_payload_sha256','goal_id','finding_id','kind','rationale','evidence'})
        for name in ('task_id','goal_id','rationale'): text(c[name])
        strings(c['evidence'])
        task=db.execute('SELECT * FROM tasks WHERE id=?',(c['task_id'],)).fetchone()
        if (not task or type(c['task_revision']) is not int or c['task_revision']<=0 or c['task_revision']!=task['revision'] or not _hash(c['task_payload_sha256']) or c['task_payload_sha256']!=payload(task) or c['goal_id'] not in p['goals'] or not _hash(c['finding_id']) or c['kind'] not in {'feature','repair'} or c['task_id'] in seen): raise ValueError('candidate identity')
        seen.add(c['task_id'])
    if p['schema_version']==2:
        rows=a['goal_coverage']
        if not isinstance(rows,list) or len(rows)!=len(p['goals']): raise ValueError('goal coverage')
        covered=set(); candidates={c['task_id']:c for c in a['candidates']}; mapped=set()
        for row in rows:
            keys(row,{'goal_id','status','reason','evidence','task_id'})
            text(row['goal_id']); text(row['reason']); strings(row['evidence'])
            if row['goal_id'] not in p['goals'] or row['goal_id'] in covered or row['status'] not in {'actionable','needs_research','needs_capability','needs_approval','needs_validation','achieved'}: raise ValueError('goal status')
            covered.add(row['goal_id'])
            tid=row['task_id']
            if tid is not None:
                text(tid)
                if row['status']!='actionable' or tid not in candidates or candidates[tid]['goal_id']!=row['goal_id']: raise ValueError('goal task binding')
                mapped.add(tid)
        if mapped!=set(candidates): raise ValueError('unmapped candidate')
    if not run['evidence']:
        prior=db.execute("SELECT data FROM events WHERE run_id=? AND kind IN ('verification_passed','verification_failed','verification_expired') ORDER BY seq DESC LIMIT 1",(run['id'],)).fetchone()
        if prior:
            run=dict(run); run['evidence']=prior[0]
    return a,hashlib.sha256(raw).hexdigest(),run


def machine(db,run,config,source):
    task=db.execute('SELECT * FROM tasks WHERE id=?',(run['task_id'],)).fetchone()
    from .producer import task_check
    bound=task_check(SimpleNamespace(config=config,db=db),task['id'])
    return _same(_object(run['policy']),config) and _proof(run,task,config,_object(run['evidence']),bound,run['candidate']==source)


def recovery(disposition,reason):
    goal_actions={
        'goal_planning_required':'Independent reviewer must plan the recorded actionable owner goal and admit its exact task; do not invent a task merely to continue.',
        'goal_research_required':'Host must collect bounded evidence for the recorded owner goal and obtain independent assessment; research text grants no permission.',
        'goal_validation_required':'Host must perform the recorded goal-specific validation within existing authority and obtain fresh independent assessment.',
        'goal_capability_required':'Owner must reconcile the recorded missing host capability; this wait grants no deployment or configuration authority.',
        'goal_approval_required':'Owner must explicitly approve the recorded restricted action and budget before execution; existing authorization is not refreshed.',
        'goal_task_unavailable':'Host must reconcile the recorded goal task state, attempt allowance and admission; do not alias or reset it.',
        'all_owner_goals_assessed_achieved':'All pinned owner goals are currently assessed achieved; this is not proof of unlisted goals or future operational reliability.'}
    if reason in goal_actions: return [goal_actions[reason]]
    if reason=='verification_running': return ['Wait for the recorded verifier to finish or recover after its original lease expires.']
    if disposition=='wait_owner': return ['Owner must restore the frozen policy, checker and controller identities and reconcile remaining resources; create separately reviewed state for policy changes.']
    if disposition=='wait_review': return ['Host must obtain a current source-bound assessment from an allowlisted independent reviewer and complete the pending Core review.']
    if disposition=='repair_or_tidy': return ['Host must admit an independent current repair finding; worker must restore passing source-bound proof and obtain fresh independent assessment before acceptance.']
    if disposition=='stop_no_value': return ['Supply a new current independently justified goal finding under unchanged owner policy, admission history and resource limits.']
    return ['Host must reconcile actual claims and dispatch the eligible task within the original lease, attempt and controller constraints.']


def project(config,db,workflow, worker=None, task_id=None):
    source=None; ah=None; selected=None; admission=None; active_id=None; p=None; goal_statuses=[]; achieved=False
    ph=config['quality_policy'].get('sha256')
    def result(disposition,reason,allowed=False):
        tid=selected['task_id'] if selected else None
        intent='worker' if allowed else 'reviewer' if disposition=='wait_review' and reason!='verification_running' else None
        identity={'source':source,'policy':ph,'assessment':ah,'workflow':workflow,'task':tid,'run':active_id,'kind':intent,'reason':reason}
        request=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        projection={'disposition':disposition,'reason':reason,'resume_conditions':recovery(disposition,reason), 'source_sha256':source,'policy_sha256':ph,'assessment_sha256':ah,'next_task_id':tid,'claim_allowed':allowed,'request_id':request,'host_request':{'kind':intent,'request_id':request,'task_id':tid,'run_id':active_id} if intent else None}
        if p is not None and p['schema_version']==2:
            projection.update(scope='owner_goals',goal_statuses=goal_statuses,all_owner_goals_assessed_achieved=achieved)
        return projection,admission
    try:
        p=load(config); source=_source(config['repo'])
        if config.get('producer_policy'):
            from .producer import policy as producer_policy
            producer_policy(SimpleNamespace(config=config,db=db))
        for active in db.execute("SELECT * FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review')"):
            if active['workflow']!=workflow: return result('wait_owner','controller_mismatch')
            if active['state'] in {'verifying','verified','ready_review'} and time.time()<min(active['lease_until'],active['deadline']):
                active_id=active['id']; selected={'task_id':active['task_id']}
                return result('wait_review','verification_running' if active['state']=='verifying' else 'independent_review_required')
        # The narrowly frozen seed only captures evidence, never source changes.
        tasks=db.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY rowid").fetchall()
        for task in tasks:
            if task_id and task['id']!=task_id: continue
            count=db.execute('SELECT count(*) FROM runs WHERE task_id=?',(task['id'],)).fetchone()[0]
            if task['external_key']==p['bootstrap']['external_key'] and source==p['bootstrap']['source_sha256'] and count==0:
                selected={'task_id':task['id']}; admission={'bootstrap':True,'source':source,'policy':ph}
                return result('continue','bootstrap_evidence',True)
        try: a,ah,proof=assessment(p,config,db,source,worker)
        except (FileNotFoundError,LookupError):
            historical=db.execute('SELECT candidate FROM runs WHERE evidence IS NOT NULL ORDER BY rowid DESC LIMIT 1').fetchone()
            return result('repair_or_tidy','source_or_proof_changed') if historical and historical[0]!=source else result('wait_review','assessment_required')
        if p['schema_version']==2:
            goal_statuses=[{'goal_id':r['goal_id'],'status':r['status']} for r in a['goal_coverage']]
        passing=machine(db,proof,config,source)
        blocks=any(d['status']=='block' or (d['status']=='advisory' and name not in p['allowed_advisories']) for name,d in a['dimensions'].items())
        unknown=any(d['status']=='unknown' for d in a['dimensions'].values())
        if unknown: return result('wait_review','assessment_incomplete')
        if not p['ui_required'] and a['dimensions']['ui']['status']!='not_applicable': return result('wait_review','assessment_incomplete')
        history=[]
        for row in db.execute("SELECT data FROM events WHERE kind='quality_admitted'"):
            history.append(strict(row[0]))
        for c in a['candidates']:
            if task_id and c['task_id']!=task_id: continue
            task=db.execute('SELECT * FROM tasks WHERE id=?',(c['task_id'],)).fetchone()
            count=db.execute('SELECT count(*) FROM runs WHERE task_id=?',(task['id'],)).fetchone()[0]
            if task['state']!='queued' or count>=config['budget']['max_attempts']: continue
            if any(h.get('source')==source and h.get('finding')==c['finding_id'] and h.get('task')!=task['id'] for h in history): continue
            if c['kind']=='feature' and (not passing or blocks): continue
            if c['kind']=='repair' and passing and not blocks: continue
            # Nonpassing repair needs an actual prior verifier, never a mere assertion.
            if not passing and not proof['evidence']: continue
            selected=c; admission={'source':source,'finding':c['finding_id'],'goal':c['goal_id'],'task':task['id'],'policy':ph,'bootstrap':False}
            return result('repair_or_tidy' if c['kind']=='repair' else 'continue','reviewed_repair' if c['kind']=='repair' else 'novel_reviewed_value',True)
        if task_id:
            target=db.execute('SELECT id FROM tasks WHERE id=?',(task_id,)).fetchone()
            count=db.execute('SELECT count(*) FROM runs WHERE task_id=?',(task_id,)).fetchone()[0]
            if not target or count>=config['budget']['max_attempts']: return result('wait_owner','attempts_exhausted')
        if not passing or blocks: return result('repair_or_tidy','source_or_semantics_blocked')
        if p['schema_version']==2:
            remaining=[r for r in a['goal_coverage'] if r['status']!='achieved']
            for status,reason,disposition in (
                ('actionable','goal_planning_required','wait_review'),
                ('needs_research','goal_research_required','wait_review'),
                ('needs_validation','goal_validation_required','wait_review'),
                ('needs_capability','goal_capability_required','wait_owner'),
                ('needs_approval','goal_approval_required','wait_owner')):
                rows=[r for r in remaining if r['status']==status]
                if rows:
                    if status=='actionable' and any(r['task_id'] is not None for r in rows): return result('wait_owner','goal_task_unavailable')
                    return result(disposition,reason)
            achieved=True
            return result('stop_no_value','all_owner_goals_assessed_achieved')
        return result('stop_no_value','no_novel_reviewed_value')
    except (ValueError,TypeError,KeyError,OSError,RecursionError): return result('wait_owner','quality_integrity')


def guard_owned(engine,run):
    from .core import WorkflowError
    try:
        load(engine.config)
        if not _same(_object(run['policy']),engine.config): raise ValueError('policy changed')
        row=engine.db.execute("SELECT data FROM events WHERE run_id=? AND kind='quality_admitted'",(run['id'],)).fetchone()
        if not row: raise ValueError('missing admission')
        entry=strict(row[0])
        if entry['policy']!=engine.config['quality_policy']['sha256']: raise ValueError('policy identity')
        if entry['bootstrap'] and _source(engine.config['repo'])!=entry['source']: raise ValueError('bootstrap source changed')
    except (ValueError,TypeError,KeyError,OSError): raise WorkflowError('quality ownership fence') from None


def guard_accept(engine,run):
    from .core import WorkflowError
    try:
        entry=strict(engine.db.execute("SELECT data FROM events WHERE run_id=? AND kind='quality_admitted'",(run['id'],)).fetchone()[0])
        if entry['bootstrap']: return
        p=load(engine.config); source=_source(engine.config['repo'])
        a,_,proof=assessment(p,engine.config,engine.db,source,run['worker'])
        if not p['ui_required'] and a['dimensions']['ui']['status']!='not_applicable': raise ValueError('non-UI assessment required')
        if a['proof_run_id']!=run['id'] or not machine(engine.db,proof,engine.config,source): raise ValueError('fresh proof required')
        for name,dim in a['dimensions'].items():
            if dim['status'] in {'block','unknown'} or dim['status']=='advisory' and name not in p['allowed_advisories']: raise ValueError('review incomplete')
    except (ValueError,LookupError,TypeError,KeyError,OSError): raise WorkflowError('fresh independent quality assessment required') from None
