"""Versioned observations and trusted local completion evidence, never model claims."""
import hashlib
import json
from pathlib import Path
import re
import subprocess


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def normalize_exec_events(text):
    known={'thread.started','turn.started','item.started','item.updated','item.completed','turn.completed','turn.failed','error'}
    events=[];threads=[];valid=True;started=False;turn=False;finished=False;completed=False;item_phases=set()
    for index,line in enumerate(text.splitlines()):
        try:event=json.loads(line)
        except (ValueError,RecursionError):
            if '"type"' in line and line.lstrip().startswith('{'):valid=False
            continue
        if not isinstance(event,dict) or 'type' not in event:continue
        kind=event['type']
        if kind not in known:valid=False;continue
        summary={'type':kind,'index':index}
        if kind=='thread.started':
            ident=event.get('thread_id')
            if started or not isinstance(ident,str) or not ident:valid=False
            else:threads.append(ident);started=True
            summary['thread_id']=ident
        elif kind=='turn.started':
            if not started or turn or finished:valid=False
            else:turn=True
        elif kind.startswith('item.'):
            if not turn or finished or not isinstance(event.get('item'),dict):valid=False
            item=event.get('item',{})
            ident=item.get('id')
            if isinstance(ident,str):
                phase=(ident,kind)
                if phase in item_phases:valid=False
                item_phases.add(phase)
            summary.update(item_id=item.get('id'),item_type=item.get('type'))
        elif kind=='turn.completed':
            if not started or not turn or finished:valid=False
            else:completed=True;finished=True
        elif kind in ('turn.failed','error'):
            completed=False;finished=True
        events.append(summary)
    return {'schema_version':1,'protocol':'exec-v1','events':events,'thread_ids':threads,
            'model_completed':completed and valid,'valid':valid and bool(events)}


def diff_manifest(repo,base_head,max_bytes=262144):
    repo=Path(repo).resolve()
    if type(max_bytes) is not int or max_bytes<0:raise ValueError('invalid manifest limit')
    for path in repo.rglob('*'):
        if '.git' not in path.relative_to(repo).parts and path.is_symlink():raise ValueError('manifest source symlink')
    def git(*args):
        p=subprocess.run(['git','-C',str(repo),*args],capture_output=True)
        if p.returncode:raise ValueError('manifest git command failed')
        return p.stdout
    raw=git('diff','--name-status','-z','--find-renames',base_head).split(b'\0')
    entries=[];index=0
    while index<len(raw) and raw[index]:
        status=raw[index].decode();index+=1;old=None
        if status.startswith(('R','C')):old=raw[index].decode('utf-8',errors='surrogateescape');index+=1
        path=raw[index].decode('utf-8',errors='surrogateescape');index+=1
        entries.append({'path':path,'status':status,**({'old_path':old} if old else {})})
    seen={e['path'] for e in entries}
    for rawpath in git('ls-files','--others','--exclude-standard','-z').split(b'\0'):
        if rawpath:
            path=rawpath.decode('utf-8',errors='surrogateescape')
            if path not in seen:entries.append({'path':path,'status':'A'});seen.add(path)
    for entry in entries:
        path=repo/entry['path']
        if path.is_symlink():raise ValueError('manifest source symlink')
        data=path.read_bytes() if path.is_file() else b''
        entry['binary']=b'\0' in data or b'-\t-' in git('diff','--numstat',base_head,'--',entry['path'])
        entry['sha256']=hashlib.sha256(data).hexdigest() if path.is_file() else None
    entries.sort(key=lambda e:e['path'])
    manifest={'entries':entries,'complete':True,'truncated':False}
    if len(json.dumps(manifest,ensure_ascii=True).encode())>max_bytes:
        manifest={'entries':[],'complete':False,'truncated':True}
        for entry in entries:
            candidate=dict(manifest,entries=manifest['entries']+[entry])
            if len(json.dumps(candidate).encode())>max_bytes:break
            manifest=candidate
    manifest['digest']=canonical_hash(manifest)
    return manifest


def build_receipt(bundle):
    required={'task_id','run_id','baseline','base_head','candidate','head','workflow','runtime','policy','checker','manifest','reviewer','tests','cas','core_state','main_digest','backend'}
    if not isinstance(bundle,dict) or not required<=set(bundle):raise ValueError('incomplete receipt evidence')
    def require(value,message):
        if not value:raise ValueError(message)
    def hash_value(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None
    for key in ('baseline','candidate','workflow','runtime','policy','checker','main_digest'):require(hash_value(bundle[key]),'invalid receipt hash '+key)
    for key in ('task_id','run_id','base_head','head'):require(isinstance(bundle[key],str) and bool(bundle[key]),'invalid receipt identity')
    for key in ('base_head','head'):require(re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',bundle[key]) is not None,'invalid git identity')
    manifest=bundle['manifest'];require(isinstance(manifest,dict),'invalid manifest')
    require(manifest.get('complete') is True and manifest.get('truncated') is False and isinstance(manifest.get('entries'),list),'incomplete manifest')
    require(manifest.get('digest')==canonical_hash({k:v for k,v in manifest.items() if k!='digest'}),'manifest hash mismatch')
    reviewer=bundle['reviewer'];require(isinstance(reviewer,dict),'missing reviewer')
    require(reviewer.get('approved') is True and reviewer.get('candidate')==bundle['candidate'] and isinstance(reviewer.get('version'),str) and bool(reviewer['version']) and hash_value(reviewer.get('log_sha256')),'invalid review binding')
    if 'verdict_sha256' in reviewer:require(hash_value(reviewer['verdict_sha256']),'invalid reviewer artifact hash')
    tests=bundle['tests'];require(isinstance(tests,list) and bool(tests),'missing machine tests')
    for test in tests:
        require(isinstance(test,dict) and type(test.get('exit_code')) is int and test['exit_code']==0 and hash_value(test.get('log_sha256')),'machine test did not pass')
        require(isinstance(test.get('argv'),list) and bool(test['argv']) and all(isinstance(x,str) and x for x in test['argv']),'missing test command')
    cas=bundle['cas'];require(isinstance(cas,dict) and cas.get('phase')=='done' and cas.get('expected')==bundle['base_head'] and cas.get('candidate')==bundle['head'],'CAS not complete')
    state=bundle['core_state'];require(isinstance(state,dict) and state.get('state')=='accepted','Core not accepted')
    evidence=state.get('evidence');require(isinstance(evidence,dict),'missing Core evidence')
    require(all(evidence.get(key)==bundle[key] for key in ('baseline','candidate','workflow')),'stale Core evidence')
    require(isinstance(evidence.get('policy'),dict) and canonical_hash(evidence['policy'])==bundle['policy']==state.get('policy_sha256'),'policy identity mismatch')
    require(bundle['main_digest']==bundle['candidate'],'main source mismatch')
    backend=bundle['backend'];require(isinstance(backend,dict) and backend.get('protocol')=='exec-v1' and backend.get('valid') is True and isinstance(backend.get('version'),str) and bool(backend['version']) and type(backend.get('model_completed')) is bool,'unsupported backend observations')
    receipt=dict(bundle,schema_version=1,complete=True)
    receipt['receipt_id']=canonical_hash(receipt)
    return receipt
