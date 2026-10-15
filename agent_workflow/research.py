"""Optional cooperative-host research ledger. Supplied excerpts never grant authority."""
import argparse
import contextlib
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from .guidance import _source, _hash

LIMIT = 65536


class ResearchError(ValueError):
    pass


def fail(code='research_invalid'):
    raise ResearchError(code)


def canonical(value):
    try:
        raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
        if len(raw)>LIMIT: fail('research_input_bound')
        return raw
    except (ValueError,TypeError,UnicodeError,RecursionError): fail()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def strict(raw):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value: fail()
            value[key]=item
        return value
    try:
        if len(raw)>LIMIT: fail('research_input_bound')
        return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x: fail())
    except (ValueError,UnicodeError,RecursionError): fail()


def exact(value,fields):
    if not isinstance(value,dict) or set(value)!=set(fields): fail()
    canonical(value)


def text(value,maximum):
    if not isinstance(value,str) or not value.strip(): fail()
    try:
        if len(value.encode('utf-8'))>maximum: fail('research_input_bound')
    except UnicodeError: fail()


def ident(value):
    text(value,128)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value): fail()


def strings(value,maximum=16,size=64):
    if not isinstance(value,list) or not 1<=len(value)<=maximum: fail()
    for item in value: text(item,size)
    if len(set(value))!=len(value): fail()


def scope(value):
    text(value,256)
    parts=PurePosixPath(value).parts
    if (not parts or PurePosixPath(value).is_absolute() or '\\' in value
            or str(PurePosixPath(value))!=value or any(p in {'.','..'} or p.startswith('.') for p in parts)):
        fail()


def absolute(value,repo=None,directory=False):
    text(value,4096); path=Path(value)
    if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)): fail('research_file_invalid')
    resolved=path.resolve()
    if repo is not None and (resolved==repo or repo in resolved.parents): fail('research_file_invalid')
    if directory and not path.is_dir(): fail('research_file_invalid')
    return resolved


def read(path):
    path=absolute(str(path))
    try:
        directory=os.open('/',os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child=os.open(part,os.O_RDONLY | os.O_DIRECTORY | os.O_NONBLOCK | os.O_NOFOLLOW,dir_fd=directory)
                os.close(directory);directory=child
            fd=os.open(path.name,os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,dir_fd=directory)
        finally: os.close(directory)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>LIMIT: fail('research_file_invalid')
            chunks=[]; remaining=LIMIT+1
            while remaining:
                chunk=os.read(fd,min(8192,remaining))
                if not chunk: break
                chunks.append(chunk); remaining-=len(chunk)
            raw=b''.join(chunks)
            if len(raw)>LIMIT: fail('research_input_bound')
            return raw
        finally: os.close(fd)
    except OSError: fail('research_file_invalid')


def stamp(value,age):
    if type(value) not in (int,float) or not math.isfinite(value) or not time.time()-age<=value<=time.time()+60: fail('research_stale')


def hostname(value):
    text(value,253)
    if (value!=value.lower() or value.endswith('.') or '.' not in value
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',value)
            or any(not p or len(p)>63 or p.startswith('-') or p.endswith('-') for p in value.split('.'))
            or value.endswith(('.localhost','.local','.internal'))): fail()
    try: ipaddress.ip_address(value)
    except ValueError: return
    fail()


def url(value,hosts):
    text(value,2048)
    if any(ord(c)<33 or ord(c)==127 for c in value) or '\\' in value: fail()
    try:
        parsed=urlsplit(value)
        hostname(parsed.hostname)
        if (parsed.scheme!='https' or parsed.hostname not in hosts or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None,443) or parsed.query or parsed.fragment): fail()
    except (ValueError,TypeError): fail()


def policy(descriptor,repo):
    exact(descriptor,{'path','sha256'})
    if not _hash(descriptor['sha256']): fail()
    file=absolute(descriptor['path'],repo);raw=read(file)
    if hashlib.sha256(raw).hexdigest()!=descriptor['sha256']: fail('research_policy_changed')
    p=strict(raw);exact(p,{'schema_version','goals','topics','trusted_reviewers','review_directory','caps','max_age_seconds'})
    if type(p['schema_version']) is not int or p['schema_version']!=1: fail()
    strings(p['goals']);strings(p['trusted_reviewers'])
    absolute(p['review_directory'],repo,directory=True)
    if not isinstance(p['topics'],dict) or not 1<=len(p['topics'])<=16: fail()
    for name,topic in p['topics'].items():
        text(name,64);exact(topic,{'goal_id','query','hosts','scope_paths'})
        if topic['goal_id'] not in p['goals']: fail()
        text(topic['query'],512);strings(topic['hosts']);strings(topic['scope_paths'],size=256)
        for host in topic['hosts']: hostname(host)
        for path in topic['scope_paths']: scope(path)
    caps=p['caps'];exact(caps,{'max_requests','max_observations_per_request','max_excerpt_bytes','max_total_bytes','max_reviews','max_mutations'})
    ceilings={'max_requests':128,'max_observations_per_request':128,'max_excerpt_bytes':8192,'max_total_bytes':1048576,'max_reviews':128,'max_mutations':4096}
    if any(type(value) is not int or not 1<=value<=ceilings[key] for key,value in caps.items()): fail()
    if type(p['max_age_seconds']) is not int or not 1<=p['max_age_seconds']<=86400: fail()
    return p


class Research:
    def __init__(self,engine,policy=None):
        self.engine=engine;self.db=engine.db;self.repo=Path(engine.config['repo']).resolve()
        try:
            database=self.db.execute('PRAGMA database_list').fetchone()[2]
            absolute(database,self.repo)
            exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_owner'").fetchone()
            if not exists:
                if policy is None: fail('research_not_initialized')
                loaded=globals()['policy'](policy,self.repo)
                # Only opt-in initialization creates optional tables; query/reopen never migrate.
                self.db.executescript('''
                CREATE TABLE IF NOT EXISTS research_owner(id INTEGER PRIMARY KEY CHECK(id=1),descriptor TEXT NOT NULL,mutations INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS research_requests(id TEXT PRIMARY KEY,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_observations(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,identity TEXT UNIQUE NOT NULL,body TEXT NOT NULL,excerpt TEXT,bytes INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS research_reviews(id TEXT PRIMARY KEY,receipt_path TEXT NOT NULL,receipt_sha TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_conversions(id TEXT PRIMARY KEY,review_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,result TEXT);
                ''')
                with self.tx():
                    self.db.execute('INSERT OR IGNORE INTO research_owner VALUES(1,?,0)',(canonical(policy).decode(),))
            row=self.db.execute('SELECT descriptor FROM research_owner WHERE id=1').fetchone()
            if not row: fail('research_not_initialized')
            self.descriptor=strict(row[0])
            if policy is not None and policy!=self.descriptor: fail('research_policy_changed')
            self.p=globals()['policy'](self.descriptor,self.repo)
        except (OSError,sqlite3.Error,KeyError,TypeError): fail('research_integrity')

    @contextlib.contextmanager
    def tx(self):
        if self.db.in_transaction: fail('research_transaction_conflict')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def mutation(self):
        self.p=policy(self.descriptor,self.repo)
        with self.tx():
            row=self.db.execute('SELECT mutations FROM research_owner WHERE id=1').fetchone()
            if row[0]>=self.p['caps']['max_mutations']: fail('research_mutations_exhausted')
            self.db.execute('UPDATE research_owner SET mutations=mutations+1 WHERE id=1')

    def current(self,request):
        if (request['source_sha256']!=_source(self.repo) or request['controller_sha256']!=self.engine.workflow
                or request['policy_sha256']!=self.descriptor['sha256']): fail('research_stale')

    def get_request(self,rid):
        ident(rid);row=self.db.execute('SELECT body FROM research_requests WHERE id=?',(rid,)).fetchone()
        if not row: fail()
        request=strict(row[0]);self.current(request);return request

    def event(self,kind,data):
        self.engine.event(None,None,kind,data)

    def request(self,topic_id):
        self.mutation();text(topic_id,64)
        if topic_id not in self.p['topics']: fail()
        topic=self.p['topics'][topic_id]
        binding={'source_sha256':_source(self.repo),'controller_sha256':self.engine.workflow,'policy_sha256':self.descriptor['sha256'],'topic_id':topic_id,**topic}
        rid='research-request-'+sha(binding)
        result={k:binding[k] for k in ('topic_id','goal_id','query','source_sha256','controller_sha256','policy_sha256')}
        result.update(request_id=rid,status='requested')
        with self.tx():
            old=self.db.execute('SELECT body FROM research_requests WHERE id=?',(rid,)).fetchone()
            if old: return strict(old[0])
            if self.db.execute('SELECT count(*) FROM research_requests').fetchone()[0]>=self.p['caps']['max_requests']: fail('research_request_cap')
            self.db.execute('INSERT INTO research_requests VALUES(?,?)',(rid,canonical(result).decode()));self.event('research_requested',{'request_id':rid})
        return result

    def observe(self,request_id,observation):
        self.mutation();request=self.get_request(request_id);topic=self.p['topics'][request['topic_id']]
        if not isinstance(observation,dict): fail()
        status=observation.get('status');raw=None
        if status=='available':
            exact(observation,{'status','url','captured_at','capture_tool','collector','version','locator','license','complete','excerpt'})
            url(observation['url'],topic['hosts']);text(observation['locator'],256)
            for key in ('version','license'):
                if observation[key] is not None: text(observation[key],128)
            if type(observation['complete']) is not bool: fail()
            text(observation['excerpt'],self.p['caps']['max_excerpt_bytes']);raw=observation['excerpt']
            meta={k:v for k,v in observation.items() if k!='excerpt'};meta['excerpt_sha256']=hashlib.sha256(raw.encode('utf-8')).hexdigest()
        elif status=='unavailable':
            exact(observation,{'status','captured_at','capture_tool','collector','reason'})
            if observation['reason']!='host_search_unavailable': fail()
            meta=dict(observation)
        else: fail()
        stamp(observation['captured_at'],self.p['max_age_seconds'])
        text(observation['capture_tool'],64);text(observation['collector'],64)
        meta['request_id']=request_id;identity=sha({k:v for k,v in meta.items() if k!='captured_at'});oid='research-observation-'+identity
        size=len(raw.encode('utf-8')) if raw else 0
        with self.tx():
            old=self.db.execute('SELECT body FROM research_observations WHERE identity=?',(identity,)).fetchone()
            if old: meta=strict(old[0])
            else:
                self.current(request)
                if self.db.execute('SELECT count(*) FROM research_observations WHERE request_id=?',(request_id,)).fetchone()[0]>=self.p['caps']['max_observations_per_request']: fail('research_observation_cap')
                if self.db.execute('SELECT coalesce(sum(bytes),0) FROM research_observations').fetchone()[0]+size>self.p['caps']['max_total_bytes']: fail('research_storage_cap')
                self.db.execute('INSERT INTO research_observations VALUES(?,?,?,?,?,?)',(oid,request_id,identity,canonical(meta).decode(),raw,size));self.event('research_observed',{'observation_id':oid,'status':status})
        return {'observation_id':oid,'observation_sha256':sha(meta),'excerpt_sha256':meta.get('excerpt_sha256'),'status':meta['status']}

    def receipt(self,path,expected_sha=None):
        file=absolute(path,self.repo);directory=absolute(self.p['review_directory'],self.repo,directory=True)
        if directory not in file.parents: fail('research_receipt_invalid')
        raw=read(file);fingerprint=hashlib.sha256(raw).hexdigest()
        if expected_sha is not None and fingerprint!=expected_sha: fail('research_receipt_changed')
        receipt=strict(raw);exact(receipt,{'schema_version','request_id','observations','reviewer','reviewed_at','source_sha256','policy_sha256','goal_id','finding_id','verdict','candidate','candidate_sha256'})
        if type(receipt['schema_version']) is not int or receipt['schema_version']!=1 or not _hash(receipt['finding_id']): fail()
        request=self.get_request(receipt['request_id']);stamp(receipt['reviewed_at'],self.p['max_age_seconds'])
        if receipt['source_sha256']!=request['source_sha256'] or receipt['policy_sha256']!=self.descriptor['sha256'] or receipt['goal_id']!=request['goal_id']: fail('research_receipt_binding')
        if receipt['reviewer'] not in self.p['trusted_reviewers']: fail('research_reviewer_invalid')
        if self.db.execute('SELECT 1 FROM runs WHERE worker=? LIMIT 1',(receipt['reviewer'],)).fetchone(): fail('research_reviewer_invalid')
        observations=receipt['observations']
        if not isinstance(observations,list) or not 1<=len(observations)<=16: fail()
        seen=set();evidence=[]
        for pair in observations:
            exact(pair,{'observation_id','observation_sha256'});ident(pair['observation_id'])
            if not _hash(pair['observation_sha256']) or pair['observation_id'] in seen: fail()
            seen.add(pair['observation_id']);row=self.db.execute('SELECT body FROM research_observations WHERE id=? AND request_id=?',(pair['observation_id'],receipt['request_id'])).fetchone()
            if not row: fail('research_receipt_binding')
            item=strict(row[0]);stamp(item['captured_at'],self.p['max_age_seconds'])
            if sha(item)!=pair['observation_sha256'] or item['collector']==receipt['reviewer']: fail('research_receipt_binding')
            if receipt['verdict']=='admit':
                if item['status']!='available' or item['complete'] is not True: fail('research_evidence_incomplete')
                evidence.append({'observation_id':pair['observation_id'],**{k:item[k] for k in ('excerpt_sha256','url','version','locator')}})
        if receipt['verdict'] not in {'reject','discuss','admit'}: fail()
        if receipt['verdict']!='admit':
            if receipt['candidate'] is not None or receipt['candidate_sha256'] is not None: fail()
        else:
            candidate=receipt['candidate'];exact(candidate,{'title','claim','inference','scope_paths'})
            text(candidate['title'],128);text(candidate['claim'],512);text(candidate['inference'],512);strings(candidate['scope_paths'],size=256)
            for item in candidate['scope_paths']:
                scope(item)
                if item not in self.p['topics'][request['topic_id']]['scope_paths']: fail('research_scope_invalid')
            if not _hash(receipt['candidate_sha256']) or receipt['candidate_sha256']!=sha(candidate): fail('research_receipt_binding')
        return receipt,fingerprint,evidence

    def review(self,receipt_path):
        self.mutation();receipt,fingerprint,_=self.receipt(receipt_path);rid='research-review-'+fingerprint
        with self.tx():
            if not self.db.execute('SELECT 1 FROM research_reviews WHERE id=?',(rid,)).fetchone():
                if self.db.execute('SELECT count(*) FROM research_reviews').fetchone()[0]>=self.p['caps']['max_reviews']: fail('research_review_cap')
                self.db.execute('INSERT INTO research_reviews VALUES(?,?,?,?)',(rid,receipt_path,fingerprint,canonical(receipt).decode()));self.event('research_reviewed',{'review_id':rid,'status':receipt['verdict']})
        return {'review_id':rid,'status':receipt['verdict'],'finding_id':receipt['finding_id']}

    def convert(self,review_id):
        self.mutation();ident(review_id)
        row=self.db.execute('SELECT * FROM research_reviews WHERE id=?',(review_id,)).fetchone()
        if not row: fail()
        receipt,fingerprint,evidence=self.receipt(row['receipt_path'],row['receipt_sha'])
        if receipt['verdict']!='admit': fail('research_not_admitted')
        key='research:'+sha({'source_sha256':receipt['source_sha256'],'finding_id':receipt['finding_id']})
        candidate=receipt['candidate'];body={'schema_version':1,'type':'reviewed_research_candidate',**{k:receipt[k] for k in ('source_sha256','goal_id','finding_id')},**{k:candidate[k] for k in ('claim','inference','scope_paths')},'evidence':evidence}
        proposal={'source':'local','external_key':key,'title':candidate['title'],'body':canonical(body).decode()}
        with self.tx():
            old=self.db.execute('SELECT * FROM research_conversions WHERE id=?',(key,)).fetchone()
            if old:
                if old['status']=='conflict': fail('research_conversion_conflict')
                proposal=strict(old['payload'])
                original=self.db.execute('SELECT * FROM research_reviews WHERE id=?',(old['review_id'],)).fetchone()
                if not original: fail('research_conversion_conflict')
                self.receipt(original['receipt_path'],original['receipt_sha'])
                if old['status']=='converted':
                    existing=self.db.execute('SELECT * FROM tasks WHERE source=? AND external_key=?',(proposal['source'],key)).fetchone()
                    if not existing or any(existing[k]!=proposal[k] for k in proposal): fail('research_conversion_conflict')
                    result=strict(old['result'])
                    if existing['id']!=result['task_id'] or sha({k:existing[k] for k in ('source','external_key','title','body','revision')})!=result['task_payload_sha256']: fail('research_conversion_conflict')
                    return result
            else:
                self.db.execute('INSERT INTO research_conversions VALUES(?,?,?, ?,NULL)',(key,review_id,canonical(proposal).decode(),'reserved'));self.event('research_conversion_reserved',{'review_id':review_id,'external_key':key})
        # No nested Core transaction: immutable import owns its original transaction.
        self.p=policy(self.descriptor,self.repo);self.receipt(row['receipt_path'],fingerprint)
        try:
            task=self.engine.import_task(proposal['external_key'],proposal['title'],proposal['body'],source=proposal['source'])
        except ValueError:
            with self.tx():
                self.db.execute("UPDATE research_conversions SET status='conflict' WHERE id=?",(key,));self.event('research_conversion_conflict',{'external_key':key})
            fail('research_conversion_conflict')
        identity={k:task[k] for k in ('source','external_key','title','body','revision')}
        if any(task[k]!=proposal[k] for k in proposal): fail('research_conversion_conflict')
        result={'task_id':task['id'],'task_revision':task['revision'],'task_payload_sha256':sha(identity),'external_key':key,'status':'converted'}
        with self.tx():
            self.db.execute("UPDATE research_conversions SET status='converted',result=? WHERE id=?",(canonical(result).decode(),key));self.event('research_converted',{'external_key':key,'task_id':task['id'],'task_payload_sha256':result['task_payload_sha256']})
        # Imported task remains inert history if source drifted during import; never hide it.
        self.current(self.get_request(receipt['request_id']))
        return result

    def query(self):
        self.p=policy(self.descriptor,self.repo)
        existing=self.db.in_transaction
        if not existing: self.db.execute('BEGIN')
        try: return self._query_snapshot()
        finally:
            if not existing: self.db.execute('ROLLBACK')

    def _query_snapshot(self):
        requests=[strict(r[0]) for r in self.db.execute('SELECT body FROM research_requests ORDER BY rowid LIMIT 128')]
        observations=[]
        for row in self.db.execute('SELECT id,body FROM research_observations ORDER BY rowid LIMIT 16384'):
            item=strict(row['body']);observations.append({'observation_id':row['id'],'observation_sha256':sha(item),'request_id':item['request_id'],'status':item['status'],'excerpt_sha256':item.get('excerpt_sha256')})
        reviews=[{'review_id':r['id'],'status':strict(r['body'])['verdict'],'finding_id':strict(r['body'])['finding_id']} for r in self.db.execute('SELECT id,body FROM research_reviews ORDER BY rowid LIMIT 128')]
        conversions=[{'external_key':r['id'],'status':r['status'],'task_id':strict(r['result'])['task_id'] if r['result'] else None} for r in self.db.execute('SELECT id,status,result FROM research_conversions ORDER BY rowid LIMIT 128')]
        usage={'requests':self.db.execute('SELECT count(*) FROM research_requests').fetchone()[0],'observations':self.db.execute('SELECT count(*) FROM research_observations').fetchone()[0],'excerpt_bytes':self.db.execute('SELECT coalesce(sum(bytes),0) FROM research_observations').fetchone()[0],'reviews':self.db.execute('SELECT count(*) FROM research_reviews').fetchone()[0],'conversions':self.db.execute('SELECT count(*) FROM research_conversions').fetchone()[0],'mutations':self.db.execute('SELECT mutations FROM research_owner WHERE id=1').fetchone()[0]}
        return {'schema_version':1,'policy_sha256':self.descriptor['sha256'],'source_sha256':_source(self.repo),'requests':requests,'observations':observations,'reviews':reviews,'conversions':conversions,'usage':usage}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state',required=True);parser.add_argument('--policy');parser.add_argument('action',choices=['init','request','observe','review','convert','query']);parser.add_argument('--args',default='{}');a=parser.parse_args();db=None
    try:
        args=strict(a.args.encode('utf-8'))
        fields={'init':set(),'request':{'topic_id'},'observe':{'request_id','observation'},'review':{'receipt_path'},'convert':{'review_id'},'query':set()}
        exact(args,fields[a.action])
        if bool(a.policy)!=(a.action=='init'): fail()
        if a.action=='query':
            # Existing state only, no Engine constructor, migrations, reconciliation or journal mutation.
            path=absolute(str(Path(a.state)/'tasks.sqlite'));db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
            config=strict(db.execute('SELECT body FROM policy WHERE id=1').fetchone()[0]);from .core import digest
            engine=SimpleNamespace(db=db,config=config,workflow=digest(Path(__file__).parent))
        else:
            from .core import Engine
            engine=Engine(a.state)
        research=Research(engine,strict(read(a.policy)) if a.policy else None)
        result={'initialized':True} if a.action=='init' else getattr(research,a.action)(**args)
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False,allow_nan=False));return 0
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error,UnicodeError,RecursionError):
        print(json.dumps({'ok':False,'error':'research_request_rejected'}));return 1
    finally:
        if db is not None: db.close()


if __name__=='__main__':
    raise SystemExit(main())
