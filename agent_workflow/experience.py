"""Bounded local observations. Knowledge never grants workflow authority."""
import base64
import hashlib
import json
from pathlib import Path
import re
import time

from .core import WorkflowError, digest

CAPACITY = 128
MAX_TEXT = 2048
MAX_ENTRY = 8192
MAX_QUERY = 32768
MAX_TTL = 604800
MAX_LOGS = 512
MAX_LOG_BYTES = 65536


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def public_size(value):
    """Budget default public JSON plus normal CLI/MCP content framing.

    JSON-RPC request IDs are caller data; 128 bytes reserve ordinary RPC framing.
    No compact-JSON shortcut is used for the public response budget.
    """
    text=json.dumps(value)
    return max(len(text.encode()),
               len(json.dumps({'ok':True,'result':value}).encode()),
               len(json.dumps({'content':[{'type':'text','text':text}],'isError':False}).encode())) + 128


def init(engine):
    engine.db.execute('CREATE TABLE IF NOT EXISTS experience_entries '
                      '(id TEXT PRIMARY KEY,body TEXT NOT NULL,body_sha256 TEXT NOT NULL,polluted INTEGER NOT NULL DEFAULT 0)')


def integer(value, maximum):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise WorkflowError('experience bounds require decimal strings')
    number = int(value)
    if not 1 <= number <= maximum:
        raise WorkflowError('experience bound exceeded')
    return number


def event(engine, run_id, seq):
    number = integer(seq, 2**63-1)
    row = engine.db.execute('SELECT * FROM events WHERE seq=? AND run_id=?', (number,run_id)).fetchone()
    if not row or row['kind'] not in ('verification_passed','verification_failed','verification_expired'):
        raise WorkflowError('explicit canonical machine verification proof required')
    return row, json.loads(row['data'])


def fingerprint(engine, run_id, before=None):
    query = "SELECT data FROM events WHERE run_id=? AND kind IN ('verification_failed','verification_expired')"
    args = [run_id]
    if before is not None:
        query += ' AND seq<?'
        args.append(before)
    row = engine.db.execute(query+' ORDER BY seq DESC LIMIT 1',args).fetchone()
    if not row:return ''
    data=json.loads(row[0])
    failures=[{key:command[key] for key in ('argv','exit_code','timeout','internal_error','capture_error','cleanup_error')
               if key in command} for command in data.get('results',[])
              if command.get('exit_code')!=0 or command.get('execution_error')]
    return sha(canonical({'commands':failures,'source_error':data.get('source_error'),
                          'source_unchanged':data.get('source_unchanged')}).encode())


def verify_proof(engine, run, row, data, require_current):
    policy=json.loads(run['policy'])
    if (data.get('baseline')!=run['baseline'] or data.get('workflow')!=run['workflow']
            or data.get('task_revision')!=run['revision'] or data.get('policy')!=policy
            or policy!=engine.config or row['task_id']!=run['task_id']
            or not isinstance(data.get('candidate'),str)
            or re.fullmatch('[0-9a-f]{64}',data['candidate']) is None
            or not isinstance(data.get('results'),list)):
        raise WorkflowError('machine proof identity mismatch')
    task=engine.db.execute('SELECT revision FROM tasks WHERE id=?',(run['task_id'],)).fetchone()
    if not task or task[0]!=run['revision']:raise WorkflowError('task revision changed')
    commands=data['results']
    expected=policy['tests']
    acceptance=data.get('task_acceptance')
    binding=None
    if require_current or acceptance:
        from .producer import task_check
        binding=task_check(engine,run['task_id'])
    if binding is not None:
        if not isinstance(acceptance,dict) or acceptance.get('binding')!=binding:
            raise WorkflowError('producer proof binding changed')
        expected=expected+[binding['argv']]
    elif acceptance:
        raise WorkflowError('unexpected producer proof binding')
    if [x.get('argv') for x in commands]!=expected:
        raise WorkflowError('machine proof command mismatch')
    if require_current:
        if (row['kind']!='verification_passed' or run['state'] not in ('verified','ready_review','accepted')
                or data!=json.loads(run['evidence'] or '{}') or data['candidate']!=run['candidate']
                or data['candidate']!=digest(policy['repo']) or data.get('source_unchanged') is not True
                or data.get('ownership_valid_at_finish') is not True
                or not commands or any(type(x.get('exit_code')) is not int or x['exit_code']!=0
                                       or x.get('execution_error') for x in commands)
                or (acceptance and acceptance.get('passed') is not True)):
            raise WorkflowError('current passing machine evidence required')
        if binding is not None:
            from .producer import check_passed
            if not check_passed(binding,commands[-1],data['candidate']):
                raise WorkflowError('current owner task check did not pass')
    return {'seq':row['seq'],'event_sha256':sha(row['data'].encode()),'kind':row['kind'],
            'task_id':run['task_id'],'run_id':run['id'],'workflow':run['workflow'],
            'revision':run['revision'],'policy_sha256':sha(canonical(policy).encode()),
            'commands':[{'argv':x['argv'],'exit_code':x['exit_code']} for x in commands]}


def raw_logs(engine, data):
    logs=[]
    commands=list(enumerate(data.get('results',[])))
    commands.sort(key=lambda pair: (pair[1].get('exit_code')==0 and not pair[1].get('execution_error'),pair[0]))
    for index, command in commands:
        for stream in ('stdout','stderr'):
            if len(logs)>=4:return logs
            encoded=command.get(stream+'_base64','')
            if not isinstance(encoded,str) or len(encoded)>((MAX_LOG_BYTES+2)//3)*4:
                raise WorkflowError('machine log encoding exceeds experience bound')
            raw=base64.b64decode(encoded,validate=True)
            if len(raw)>MAX_LOG_BYTES:raise WorkflowError('machine log prefix exceeds experience bound')
            # Include empty streams too: their exact empty-byte hash is evidence.
            value=sha(raw)
            logs.append(({'path':str(engine.state/'experience-logs'/(value+'.log')),
                          'sha256':value,'bytes':len(raw),'stream':stream,'command_index':index,
                          'source_total_bytes':command.get(stream+'_total_bytes',len(raw)),
                          'source_truncated':bool(command.get(stream+'_truncated',False))},raw))
    return logs


def record(engine, kind, run_id, statement='', proof_seq='', expires_seconds='86400'):
    init(engine)
    if kind not in ('fact','hypothesis','verified') or not isinstance(run_id,str):
        raise WorkflowError('invalid experience kind or run')
    if not isinstance(statement,str) or len(statement.encode())>MAX_TEXT:
        raise WorkflowError('experience statement exceeds bound')
    ttl=integer(expires_seconds,MAX_TTL)
    if not isinstance(proof_seq,str):raise WorkflowError('proof_seq must be a string')
    if kind=='verified' and (statement or not proof_seq):
        raise WorkflowError('verified conclusions require explicit machine proof, no arbitrary statement')
    if kind=='hypothesis' and proof_seq:raise WorkflowError('hypotheses cannot claim machine proof')
    run=engine.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not run:raise WorkflowError('experience run not found')
    if any(row[0] and row[0] in statement for row in engine.db.execute('SELECT token FROM runs')):
        raise WorkflowError('fencing tokens cannot be experience text')
    proof=None;logs=[];source=run['candidate'] or run['baseline']
    if proof_seq:
        row,data=event(engine,run_id,proof_seq)
        proof=verify_proof(engine,run,row,data,kind=='verified')
        source=data['candidate'];logs=raw_logs(engine,data)
    fp=fingerprint(engine,run_id,int(proof_seq)+1 if proof_seq else None)
    statement='configured_tests_passed' if kind=='verified' else statement
    trust={'fact':'reported_unverified','hypothesis':'untrusted_hypothesis','verified':'machine_verified'}[kind]
    identity={'kind':kind,'run_id':run_id,'statement':statement,'source_digest':source,
              'failure_fingerprint':fp,'proof':proof}
    ident='experience-'+sha(canonical(identity).encode())
    now=time.time()
    body=dict(identity,id=ident,trust=trust,created_at=now,expires_at=now+ttl,
              log_refs=[ref for ref,_ in logs],stale=False,
              omitted_log_streams=max(0,len(data['results'])*2-len(logs)) if proof_seq else 0)
    serialized=canonical(body)
    if len(json.dumps(body).encode())>MAX_ENTRY-256:raise WorkflowError('experience entry exceeds bound')
    with engine.tx():
        old=engine.db.execute('SELECT * FROM experience_entries WHERE id=?',(ident,)).fetchone()
        if old:return {'deduplicated':True,'entry':current_entry(engine,old)}
        if engine.db.execute('SELECT count(*) FROM experience_entries').fetchone()[0]>=CAPACITY:
            raise WorkflowError('experience capacity exhausted')
        directory=engine.state/'experience-logs'
        if directory.is_symlink():raise WorkflowError('experience log directory symlink')
        directory.mkdir(exist_ok=True)
        for ref,raw in logs:
            path=Path(ref['path'])
            if path.is_symlink():raise WorkflowError('experience log symlink')
            if path.exists():
                if path.stat().st_size>MAX_LOG_BYTES or sha(path.read_bytes())!=ref['sha256']:
                    raise WorkflowError('experience retained log polluted')
            else:
                if len(list(directory.iterdir()))>=MAX_LOGS:raise WorkflowError('experience log capacity exhausted')
                with path.open('xb') as file:file.write(raw)
                path.chmod(0o600)
        engine.db.execute('INSERT INTO experience_entries VALUES(?,?,?,0)',(ident,serialized,sha(serialized.encode())))
        engine.event(run['task_id'],run_id,'experience_recorded',{'id':ident,'kind':kind,'trust':trust,'source':source})
    return {'deduplicated':False,'entry':body}


def current_entry(engine, row, current=None, now=None):
    """Single trust predicate for query and dedupe; pollution is irreversible."""
    if current is None:current=digest(engine.config['repo'])
    if now is None:now=time.time()
    if len(row['body'].encode())>MAX_ENTRY:raise WorkflowError('stored experience exceeds bound')
    body=json.loads(row['body']);reasons=[]
    polluted=bool(row['polluted']) or sha(row['body'].encode())!=row['body_sha256']
    if body['source_digest']!=current:reasons.append('source_version_changed')
    if now>=body['expires_at']:reasons.append('expired')
    proof=body.get('proof')
    if proof:
        try:
            original,data=event(engine,body['run_id'],str(proof['seq']))
            if sha(original['data'].encode())!=proof['event_sha256']:polluted=True
            run=engine.db.execute('SELECT * FROM runs WHERE id=?',(body['run_id'],)).fetchone()
            rebuilt=verify_proof(engine,run,original,data,body['kind']=='verified')
            if rebuilt!=proof:polluted=True
        except (WorkflowError,ValueError,TypeError,KeyError):
            reasons.append('proof_no_longer_current')
    for ref in body.get('log_refs',[]):
        expected=engine.state/'experience-logs'/(str(ref.get('sha256'))+'.log')
        try:
            if (re.fullmatch('[0-9a-f]{64}',str(ref.get('sha256'))) is None
                    or ref.get('path')!=str(expected) or expected.parent.is_symlink() or expected.is_symlink()
                    or expected.stat().st_size>MAX_LOG_BYTES or sha(expected.read_bytes())!=ref['sha256']):
                polluted=True
        except OSError:polluted=True
    if polluted:
        reasons.append('evidence_polluted')
        engine.db.execute('UPDATE experience_entries SET polluted=1 WHERE id=?',(row['id'],))
    body['stale']=bool(reasons);body['stale_reasons']=reasons
    if body['kind']=='verified' and reasons:body['trust']='stale_machine_evidence'
    return body


def query(engine, source_digest='', failure_fingerprint='', include_stale='false', limit='20'):
    init(engine)
    if include_stale not in ('true','false'):raise WorkflowError('include_stale requires true/false string')
    bound=integer(limit,20)
    for value in (source_digest,failure_fingerprint):
        if not isinstance(value,str) or (value and re.fullmatch('[0-9a-f]{64}',value) is None):
            raise WorkflowError('experience filter requires digest')
    current=digest(engine.config['repo']);now=time.time();entries=[];omitted=0
    result={'entries':entries,'omitted':0,'limits':{'capacity':CAPACITY,'query_bytes':MAX_QUERY,'entry_bytes':MAX_ENTRY,
                                                'query_framing':'default_json_cli_mcp_content_plus_128_bytes'}}
    for row in engine.db.execute('SELECT * FROM experience_entries ORDER BY rowid DESC').fetchall():
        body=current_entry(engine,row,current,now)
        if source_digest and body.get('source_digest')!=source_digest:continue
        if failure_fingerprint and body.get('failure_fingerprint')!=failure_fingerprint:continue
        if body['stale'] and include_stale=='false':continue
        if len(entries)>=bound:
            omitted+=1;continue
        trial=dict(result,entries=entries+[body],omitted=CAPACITY)
        if public_size(trial)>MAX_QUERY:
            omitted+=1;continue
        entries.append(body)
    result['omitted']=omitted
    return result
