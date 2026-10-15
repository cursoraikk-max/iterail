"""Budgeted inert context. Handoffs never transfer ownership or acceptance."""
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from .core import WorkflowError, digest
from .experience import canonical, public_size, verify_proof, fingerprint

MAX_BUDGET = 65536
CAPACITY = 128
MAX_LOGS = 512
TTL_SECONDS = 86400


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def tokens(engine):
    return [r[0] for r in engine.db.execute('SELECT token FROM runs') if r[0]]


def scrub(value, secrets, encoding_depth=0):
    """Sanitize complete data before budget selection, including nested encodings."""
    if isinstance(value, dict):
        return {scrub(k,secrets,encoding_depth):scrub(v,secrets,encoding_depth) for k,v in value.items()}
    if isinstance(value,list):return [scrub(x,secrets,encoding_depth) for x in value]
    if not isinstance(value,str):return value
    for token in secrets:
        raw=token.encode()
        forms={token,base64.b64encode(raw).decode(),base64.urlsafe_b64encode(raw).decode(),
               base64.b64encode(raw).decode().rstrip('='),raw.hex(),
               ''.join('\\u%04x'%ord(c) for c in token),''.join('\\x%02x'%c for c in raw),
               ''.join('%%%02x'%c for c in raw)}
        for form in sorted(forms,key=len,reverse=True):
            value=re.sub(re.escape(form),'[REDACTED]',value,flags=re.IGNORECASE)
    try:
        nested=json.loads(value)
        if isinstance(nested,(dict,list)) or (isinstance(nested,str) and nested!=value):
            cleaned=scrub(nested,secrets,encoding_depth)
            if cleaned!=nested:value=json.dumps(cleaned,ensure_ascii=True)
    except (ValueError,RecursionError):pass
    decoded_escape=re.sub(r'\\u([0-9a-fA-F]{4})',lambda m:chr(int(m.group(1),16)),value)
    decoded_escape=re.sub(r'\\x([0-9a-fA-F]{2})',lambda m:chr(int(m.group(1),16)),decoded_escape)
    decoded_escape=re.sub(r'%([0-9a-fA-F]{2})',lambda m:chr(int(m.group(1),16)),decoded_escape)
    if decoded_escape!=value and any(secret.lower() in decoded_escape.lower() for secret in secrets):
        return '[REDACTED_ENCODED_CONTEXT]'
    if encoding_depth < 4:
        def encoded(match):
            text=match.group(0)
            if len(text)>131072:return text
            decoded=[]
            try:decoded.append(base64.b64decode(text+'='*((-len(text))%4),altchars=b'-_',validate=True).decode('utf-8'))
            except (ValueError,UnicodeError):pass
            if len(text)%2==0 and re.fullmatch('[0-9a-fA-F]+',text):
                try:decoded.append(bytes.fromhex(text).decode('utf-8'))
                except (ValueError,UnicodeError):pass
            for original in decoded:
                if scrub(original,secrets,encoding_depth+1)!=original:
                    return '[REDACTED_ENCODED_CONTEXT]'
            return text
        value=re.sub(r'[A-Za-z0-9_+/=-]{20,}',encoded,value)
    return value


def bound(value):
    if isinstance(value,str) and value.isascii() and value.isdecimal():value=int(value)
    if type(value) is not int or not 1<=value<=MAX_BUDGET:
        raise WorkflowError('handoff budget must be 1..65536 bytes')
    return value


def init(engine):
    engine.db.execute('CREATE TABLE IF NOT EXISTS handoff_entries '
                      '(id TEXT PRIMARY KEY,body TEXT NOT NULL,body_sha256 TEXT NOT NULL,polluted INTEGER NOT NULL DEFAULT 0)')


def logs(engine,data):
    refs=[];raws=[]
    failed=[(i,r) for i,r in enumerate(data.get('results',[])) if r.get('exit_code')!=0 or r.get('execution_error')]
    if not failed:failed=list(enumerate(data.get('results',[])))
    for index,result in failed:
        for stream in ('stdout','stderr'):
            encoded=result.get(stream+'_base64','')
            if not isinstance(encoded,str) or len(encoded)>87384:raise WorkflowError('handoff log encoding exceeds bound')
            try:raw=base64.b64decode(encoded,validate=True)
            except ValueError as error:raise WorkflowError('invalid machine log encoding') from error
            if len(raw)>65536:raise WorkflowError('handoff raw prefix exceeds bound')
            value=sha(raw)
            refs.append({'path':str(engine.state/'handoff-logs'/(value+'.log')),'sha256':value,
                         'bytes':len(raw),'command_index':index,'stream':stream,
                         'source_total_bytes':result.get(stream+'_total_bytes',len(raw)),
                         'source_truncated':bool(result.get(stream+'_truncated',False)),
                         'retention':'original_raw_prefix'})
            raws.append(raw)
    return refs,raws


def fits(bundle,budget):
    # Reserve read-time reason/quarantine metadata, using public default JSON and
    # CLI/MCP content framing rather than compact serialization as a shortcut.
    return public_size(bundle)+1024<=budget


def build(engine,run_id,token,budget_bytes='16384'):
    budget=bound(budget_bytes);init(engine)
    with engine.tx():
        run=engine.owned(run_id,token)
        task=engine.db.execute('SELECT * FROM tasks WHERE id=?',(run['task_id'],)).fetchone()
        row=engine.db.execute("SELECT * FROM events WHERE run_id=? AND kind IN ('verification_failed','verification_expired') ORDER BY seq DESC LIMIT 1",(run_id,)).fetchone()
        if not row:raise WorkflowError('canonical failed verification event required')
        data=json.loads(row['data']);proof=verify_proof(engine,run,row,data,False)
        from .producer import task_check
        owner_binding=task_check(engine,run['task_id'])
        refs,raws=logs(engine,data);secret_values=tokens(engine)
        policy=json.loads(run['policy']);source=digest(policy['repo']);now=time.time()
        mandatory={key:policy[key] for key in ('permissions','budget','acceptance','tests')}
        if owner_binding:mandatory['producer_task_check']=owner_binding
        if scrub(mandatory,secret_values)!=mandatory:
            raise WorkflowError('mandatory rules contain a fencing secret; handoff cannot rewrite rules')
        failure={'event_seq':row['seq'],'fingerprint':fingerprint(engine,run_id,row['seq']+1),
                 'event_sha256':sha(row['data'].encode()),'source_digest':data['candidate'],
                 'workflow':data['workflow'],'task_revision':data['task_revision'],
                 'policy_sha256':proof['policy_sha256'],'source_unchanged':data.get('source_unchanged'),
                 'results':[{k:r[k] for k in ('argv','exit_code','timeout','execution_error','internal_error','capture_error','cleanup_error','guardian_cleanup_error','stream_cleanup_error') if k in r} for r in data['results']],
                 'log_refs':refs,'producer_task_evidence_complete':not owner_binding or data.get('task_acceptance',{}).get('binding')==owner_binding}
        if data.get('source_error'):failure['source_error']=data['source_error']
        bundle={'schema_version':1,'handoff_id':'','task_id':run['task_id'],'run_id':run_id,
                'source_digest':source,'baseline':run['baseline'],'workflow':run['workflow'],
                'task_revision':run['revision'],'policy_sha256':proof['policy_sha256'],
                'created_at':now,'expires_at':now+TTL_SECONDS,'budget_bytes':budget,
                'mandatory':mandatory,'failure':failure,'context':{},'omitted':[],
                'ownership_transferred':False,'requires_fresh_claim':True,'requires_fresh_verify':True,
                'acceptance_transferred':False,'stale':False,'stale_reasons':[]}
        bundle=scrub(bundle,secret_values)
        # Reserve the fixed-size identifier and omission metadata before adding context.
        bundle['handoff_id']='handoff-'+'0'*64
        if not fits(dict(bundle,omitted=[{'field':'optional_context','reason':'budget'}]),budget):
            raise WorkflowError('handoff budget cannot contain mandatory rules and failure evidence')
        omitted=[]
        def optional(name,value):
            proposed=dict(bundle,context=dict(bundle['context'],**{name:scrub(value,secret_values)}),omitted=[{'field':'optional_context','reason':'budget'}])
            if fits(proposed,budget):bundle['context'][name]=proposed['context'][name]
            else:omitted.append(name)
        # The raw prefix reference/hash above remains complete even when the
        # readable, sanitized preview cannot fit. Never call it a full log tail.
        previews=[]
        for ref,raw in zip(refs,raws):
            previews.append({'log_sha256':ref['sha256'],'command_index':ref['command_index'],'stream':ref['stream'],
                             'text':raw.decode('utf-8',errors='replace'),'source_truncated':ref['source_truncated'],
                             'retention':'original_prefix_sanitized_preview'})
        optional('failure_prefixes',previews)
        optional('task',{'title':task['title'],'body':task['body'],'trust':'untrusted_task_data'})
        notes=[{'seq':r['seq'],'note':json.loads(r['data']).get('note',''),'trust':'untrusted_progress'} for r in engine.db.execute("SELECT seq,data FROM events WHERE run_id=? AND kind='progress' ORDER BY seq DESC LIMIT 10",(run_id,))]
        optional('notes',notes)
        from .experience import query
        experiences=query(engine,include_stale='true',limit='20')
        grouped={'facts':[],'hypotheses':[],'verified_refs':[]}
        for entry in experiences['entries']:
            key={'fact':'facts','hypothesis':'hypotheses','verified':'verified_refs'}[entry['kind']]
            grouped[key].append(entry)
        optional('experience',dict(grouped,omitted_entries=experiences['omitted'],authority='inert_context_only'))
        if omitted:bundle['omitted']=[{'field':'optional_context','reason':'budget','fields':omitted}]
        identity=dict(bundle);identity.pop('handoff_id');identity.pop('created_at');identity.pop('expires_at')
        ident='handoff-'+sha(canonical(identity).encode());bundle['handoff_id']=ident
        if not fits(bundle,budget):raise WorkflowError('handoff metadata cannot fit requested budget')
        old=engine.db.execute('SELECT * FROM handoff_entries WHERE id=?',(ident,)).fetchone()
        if old:return read(engine,ident,include_stale='true')
        if engine.db.execute('SELECT count(*) FROM handoff_entries').fetchone()[0]>=CAPACITY:raise WorkflowError('handoff capacity exhausted')
        directory=engine.state/'handoff-logs'
        if directory.is_symlink():raise WorkflowError('handoff log directory symlink')
        directory.mkdir(exist_ok=True)
        for ref,raw in zip(refs,raws):
            path=Path(ref['path'])
            if path.is_symlink():raise WorkflowError('handoff log symlink')
            if path.exists():
                if path.stat().st_size>65536 or sha(path.read_bytes())!=ref['sha256']:raise WorkflowError('handoff log polluted')
            else:
                if len(list(directory.iterdir()))>=MAX_LOGS:raise WorkflowError('handoff log capacity exhausted')
                with path.open('xb') as file:file.write(raw)
                path.chmod(0o600)
        body=canonical(bundle);engine.db.execute('INSERT INTO handoff_entries VALUES(?,?,?,0)',(ident,body,sha(body.encode())))
        engine.event(run['task_id'],run_id,'handoff_built',{'id':ident,'source':source,'failure_seq':row['seq'],'bytes':public_size(bundle)})
    return read(engine,ident,include_stale='true')


def read(engine,handoff_id,include_stale='false'):
    if include_stale not in ('true','false'):raise WorkflowError('include_stale requires true/false string')
    if not isinstance(handoff_id,str) or re.fullmatch('handoff-[0-9a-f]{64}',handoff_id) is None:raise WorkflowError('invalid handoff ID')
    init(engine);row=engine.db.execute('SELECT * FROM handoff_entries WHERE id=?',(handoff_id,)).fetchone()
    if not row:raise WorkflowError('handoff not found')
    if len(row['body'].encode())>MAX_BUDGET:raise WorkflowError('stored handoff exceeds bound')
    bundle=json.loads(row['body']);reasons=[];polluted=bool(row['polluted']) or sha(row['body'].encode())!=row['body_sha256']
    if polluted:
        engine.db.execute('UPDATE handoff_entries SET polluted=1 WHERE id=?',(handoff_id,))
        if include_stale=='false':raise WorkflowError('stale handoff; bundle polluted')
        # Do not return forged mandatory rules or notes from a quarantined body.
        return {'schema_version':1,'handoff_id':handoff_id,'stale':True,'stale_reasons':['bundle_polluted'],'ownership_transferred':False,'acceptance_transferred':False,'requires_fresh_claim':True,'requires_fresh_verify':True}
    if digest(engine.config['repo'])!=bundle['source_digest']:reasons.append('source_version_changed')
    if time.time()>=bundle['expires_at']:reasons.append('expired')
    if engine.workflow!=bundle['workflow']:reasons.append('controller_version_changed')
    run=engine.db.execute('SELECT * FROM runs WHERE id=?',(bundle['run_id'],)).fetchone()
    task=engine.db.execute('SELECT revision FROM tasks WHERE id=?',(bundle['task_id'],)).fetchone()
    if not run or not task or task[0]!=bundle['task_revision']:reasons.append('task_revision_changed')
    if bundle['failure']['source_digest']!=bundle['source_digest']:reasons.append('failure_is_historical')
    if not bundle['failure']['producer_task_evidence_complete']:reasons.append('producer_task_proof_incomplete')
    if run:
        expected={key:json.loads(run['policy'])[key] for key in ('permissions','budget','acceptance','tests')}
        from .producer import task_check
        try:
            binding=task_check(engine,run['task_id'])
            if binding:expected['producer_task_check']=binding
            if scrub(expected,tokens(engine))!=bundle['mandatory']:reasons.append('mandatory_rules_changed')
        except (WorkflowError,OSError,ValueError,KeyError):reasons.append('owner_binding_changed')
    event=engine.db.execute('SELECT * FROM events WHERE seq=? AND run_id=?',(bundle['failure']['event_seq'],bundle['run_id'])).fetchone()
    if not event or sha(event['data'].encode())!=bundle['failure']['event_sha256']:
        polluted=True;reasons.append('failure_event_polluted')
    for ref in bundle['failure']['log_refs']:
        expected=engine.state/'handoff-logs'/(str(ref.get('sha256'))+'.log')
        try:
            if ref['path']!=str(expected) or re.fullmatch('[0-9a-f]{64}',ref['sha256']) is None or expected.parent.is_symlink() or expected.is_symlink() or expected.stat().st_size>65536 or sha(expected.read_bytes())!=ref['sha256']:
                polluted=True
        except OSError:polluted=True
    if polluted:
        engine.db.execute('UPDATE handoff_entries SET polluted=1 WHERE id=?',(handoff_id,));reasons.append('log_polluted')
    experience=bundle.get('context',{}).get('experience',{})
    from .experience import current_entry
    for group in ('facts','hypotheses','verified_refs'):
        updated=[]
        for entry in experience.get(group,[]):
            original=engine.db.execute('SELECT * FROM experience_entries WHERE id=?',(entry['id'],)).fetchone()
            try:
                if not original:raise WorkflowError('experience missing')
                present=current_entry(engine,original)
                if present.get('stale'):reasons.append('experience_context_stale')
                updated.append(present)
            except (WorkflowError,ValueError,TypeError,KeyError,OSError):
                reasons.append('experience_context_polluted')
                updated.append({'id':entry.get('id'),'stale':True,'trust':'untrusted_context','stale_reasons':['context_invalid']})
        if group in experience:experience[group]=updated
    reasons=list(dict.fromkeys(reasons))
    bundle['stale']=bool(reasons);bundle['stale_reasons']=reasons
    if reasons and include_stale=='false':raise WorkflowError('stale handoff; use include_stale=true for historical context only')
    bundle=scrub(bundle,tokens(engine))
    if public_size(bundle)>bundle['budget_bytes']:raise WorkflowError('handoff read exceeds saved public budget')
    return bundle
