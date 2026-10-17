"""Opt-in cooperative cross-state engineering budget; no backend scheduler."""
import argparse
import contextlib
import fcntl
import hashlib
import json
from pathlib import Path
import os
import re
import sqlite3
import time
from .continuation import absolute, regular
from .preflight import encoded, snapshot


def require(value, reason):
    if not value:
        raise ValueError(reason)


def key(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', value), 'stable scope request key required')
    return value


def read_policy(descriptor):
    require(isinstance(descriptor, dict) and set(descriptor) == {'path', 'sha256'}, 'owner scope descriptor required')
    path = absolute(descriptor['path']);raw = regular(path)
    require(hashlib.sha256(raw).hexdigest() == descriptor['sha256'], 'owner scope policy changed')
    p = json.loads(raw)
    require(isinstance(p, dict) and set(p) == {'schema_version','scope_id','repo','ledger','started','deadline',
            'max_dispatches','max_repairs','python','build_requirements','tests'} and type(p['schema_version']) is int and p['schema_version'] == 1,
            'exact engineering scope policy required')
    key(p['scope_id']);repo = absolute(p['repo']);ledger = absolute(p['ledger'])
    require(repo.is_dir() and not path.is_relative_to(repo) and not ledger.is_relative_to(repo), 'external owner scope policy and ledger required')
    require(all(type(p[x]) in (int,float) and 0 < p[x] < 1e12 for x in ('started','deadline')) and p['started'] < p['deadline'], 'absolute scope start/deadline required')
    require(type(p['max_dispatches']) is int and 1 <= p['max_dispatches'] <= 10000 and type(p['max_repairs']) is int and 0 <= p['max_repairs'] <= 10000, 'bounded cumulative scope counters required')
    require(isinstance(p['python'], str) and Path(p['python']).is_absolute() and isinstance(p['build_requirements'], dict)
            and all(isinstance(k,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',k) and isinstance(v,str) and re.fullmatch(r'\d+(?:\.\d+)*',v) for k,v in p['build_requirements'].items()), 'selected runtime metadata requirements required')
    require(isinstance(p['tests'], list) and p['tests'] and all(isinstance(a,list) and a and all(isinstance(x,str) and x for x in a) for a in p['tests']), 'frozen test argv required')
    return p


def marker_path(repo):
    """Read Git metadata only; no global registry or source-tree marker."""
    repo=Path(repo);git=repo/'.git'
    if git.is_file():
        text=regular(git).decode().strip();require(text.startswith('gitdir: '),'valid Git metadata required for scope')
        value=Path(text[8:]);git=(repo/value).resolve() if not value.is_absolute() else value.resolve()
        common=git/'commondir'
        if common.is_file():git=(git/regular(common).decode().strip()).resolve()
    require(git.is_dir(),'Git owner metadata required for scope binding')
    return git/'iterail-engineering-scope.json'


def enforce_binding(config):
    marker=marker_path(config['repo'])
    if marker.exists():
        require(json.loads(regular(marker))==config.get('engineering_scope'),'repository has an owner scope; descriptor cannot be omitted or replaced')


def initialize(descriptor):
    p = read_policy(descriptor);path = Path(p['ledger'])
    marker=marker_path(p['repo'])
    if marker.exists():
        require(json.loads(regular(marker))==descriptor,'repository scope cannot be repinned')
        require(path.is_file(),'scope ledger missing; never recreate an existing owner scope')
    if path.exists():
        if not marker.exists():
            # Only an explicit owner init may finish this interrupted setup.
            db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True)
            try:
                row=db.execute('SELECT policy_sha,policy,preflight FROM meta WHERE id=1').fetchone()
                require(row is not None and row[0]==descriptor['sha256'] and json.loads(row[1])==p,'orphan scope identity mismatch')
                current=snapshot(p);require(current['passed'] and current['fingerprint']==json.loads(row[2])['fingerprint'],'orphan scope environment drift')
            finally:db.close()
            fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(encoded(descriptor));stream.flush();os.fsync(stream.fileno())
        with Scope(descriptor) as store:
            return store.status()
    preflight = snapshot(p)
    require(preflight['passed'], 'scope preflight blocked: '+ '; '.join(preflight['errors']))
    require(time.time() < p['deadline'], 'scope deadline exhausted before initialization')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os,'O_NOFOLLOW',0), 0o600);os.close(fd)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('CREATE TABLE meta(id INTEGER PRIMARY KEY CHECK(id=1),policy_sha TEXT,policy TEXT,preflight TEXT,dispatches INTEGER,repairs INTEGER);'
                         'CREATE TABLE requests(id TEXT PRIMARY KEY,payload TEXT,state TEXT,work_key TEXT,phase TEXT,run_id TEXT);'
                         'CREATE TABLE repairs(id TEXT PRIMARY KEY,payload TEXT);'
                         'CREATE TABLE events(seq INTEGER PRIMARY KEY,at REAL,kind TEXT,body TEXT);')
        db.execute('INSERT INTO meta VALUES(1,?,?,?,0,0)', (descriptor['sha256'], encoded(p).decode(), encoded(preflight).decode()))
    finally:
        db.close()
    fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(encoded(descriptor));stream.flush();os.fsync(stream.fileno())
    with Scope(descriptor) as store:
        return store.status()


class Scope:
    def __init__(self, descriptor):
        self.descriptor = descriptor;self.policy = read_policy(descriptor);path = absolute(self.policy['ledger'])
        marker=marker_path(self.policy['repo']);require(marker.is_file() and json.loads(regular(marker))==descriptor,'original repository scope binding required')
        require(path.is_file() and not path.is_symlink(), 'scope ledger missing; owner initialization required, never recreate')
        self.db = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, isolation_level=None, timeout=10);self.db.row_factory = sqlite3.Row
        row = self.db.execute('SELECT * FROM meta WHERE id=1').fetchone()
        require(row is not None and row['policy_sha'] == descriptor['sha256'] and json.loads(row['policy']) == self.policy, 'scope ledger identity mismatch')
        self.lock_path = path.with_name(path.name+'.lock')

    def __enter__(self):return self
    def __exit__(self,*args):self.db.close()

    @contextlib.contextmanager
    def locked(self):
        require(not self.lock_path.is_symlink(), 'regular scope lock required')
        with self.lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(lock, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:yield;self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise

    def event(self,kind,body):self.db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',(time.time(),kind,encoded(body).decode()))

    def status(self):
        row = self.db.execute('SELECT * FROM meta WHERE id=1').fetchone()
        return {'scope_id':self.policy['scope_id'],'started':self.policy['started'],'deadline':self.policy['deadline'],
                'dispatches':row['dispatches'],'repairs':row['repairs'],'max_dispatches':self.policy['max_dispatches'],
                'max_repairs':self.policy['max_repairs'],'expired':time.time()>=self.policy['deadline'],
                'requests':[dict(x) for x in self.db.execute('SELECT id,state,work_key,phase,run_id FROM requests ORDER BY rowid')],
                'preflight':json.loads(row['preflight']), 'autonomous_dispatch':False}

    def preflight(self):
        from .preflight import executable
        old = json.loads(self.db.execute('SELECT preflight FROM meta WHERE id=1').fetchone()[0]);errors=[]
        expected=[old['observations']['python'],*old['observations']['commands']]
        selected=[self.policy['python'],*[argv[0] for argv in self.policy['tests']]]
        for name,prior in zip(selected,expected):
            try:
                identity=executable(name)
                if identity!={k:prior[k] for k in ('path','resolved_path','sha256')}:errors.append('frozen executable drift before capability probe')
            except (ValueError,OSError) as error:errors.append(str(error))
        if errors:
            return {'passed':False,'errors':errors,'matches_frozen_environment':False,'probes_executed':False,
                    'executes_project_tests':False,'provider_ready':False}
        current = snapshot(self.policy);current['probes_executed']=True
        current['matches_frozen_environment'] = current['fingerprint'] == old['fingerprint']
        current['passed'] = current['passed'] and current['matches_frozen_environment']
        if not current['matches_frozen_environment']:current['errors'].append('frozen runtime environment drift')
        return current

    def recover(self,row):
        """Only actual origin Core link+policy can recover an interrupted bind."""
        origin = absolute(row['state'])/'tasks.sqlite';require(origin.is_file(), 'scope outcome unknown: origin state unavailable')
        db = sqlite3.connect(origin.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        try:
            config = json.loads(db.execute('SELECT body FROM policy WHERE id=1').fetchone()[0])
            require(config.get('engineering_scope') == self.descriptor and config['repo'] == self.policy['repo'], 'scope origin policy mismatch')
            found = db.execute('SELECT runs.* FROM scope_links JOIN runs ON runs.id=scope_links.run_id WHERE scope_links.request_id=?',(row['id'],)).fetchone()
            if found is None:
                if row['phase'] != 'closed':
                    with self.tx():self.db.execute("UPDATE requests SET phase='uncertain' WHERE id=?",(row['id'],))
                return None
            require(row['run_id'] in (None,found['id']), 'scope origin run changed')
            if row['phase'] != 'closed':
                phase = 'closed' if found['state'] in {'accepted','failed','invalidated'} else 'uncertain' if found['state']=='expired' or time.time()>=min(found['lease_until'],found['deadline']) else 'bound'
                with self.tx():
                    self.db.execute('UPDATE requests SET run_id=?,phase=? WHERE id=?',(found['id'],phase,row['id']))
            return dict(found)
        finally:db.close()

    def claim(self, engine, worker, task_id, request_id):
        key(request_id);require(isinstance(worker,str) and worker.strip() and task_id, 'scoped claim needs worker, task_id and request_id')
        task = engine.db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone();require(task is not None,'known task required')
        work = hashlib.sha256(encoded([task['source'],task['external_key']])).hexdigest()
        payload = encoded({'worker':worker,'work_key':work,'revision':task['revision'],'title':task['title'],'body':task['body']}).decode()
        with self.locked():
            existing = self.db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
            if existing:
                require(existing['payload']==payload,'scope request payload conflict')
                run = self.recover(existing)
                require(run is not None,'scope outcome unknown; reconcile original request, never redispatch')
                require(existing['state']==str(engine.state),'scope request belongs to original state; reconcile there')
                return run
            require(time.time()<self.policy['deadline'],'scope absolute deadline exhausted')
            for prior in self.db.execute("SELECT * FROM requests WHERE phase!='closed'").fetchall():
                run=self.recover(prior)
                after=self.db.execute('SELECT phase FROM requests WHERE id=?',(prior['id'],)).fetchone()[0]
                require(run is not None and after=='closed','scope active or unknown execution requires reconciliation')
            check=self.preflight();require(check['passed'],'scope preflight blocked: '+'; '.join(check['errors']))
            require(time.time()<self.policy['deadline'],'scope absolute deadline exhausted after preflight')
            row=self.db.execute('SELECT * FROM meta WHERE id=1').fetchone();retry=self.db.execute('SELECT 1 FROM requests WHERE work_key=?',(work,)).fetchone() is not None
            require(row['dispatches']<self.policy['max_dispatches'],'scope dispatch budget exhausted')
            require(not retry or row['repairs']<self.policy['max_repairs'],'scope repair budget exhausted')
            with self.tx():
                self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,NULL)',(request_id,payload,str(engine.state),work,'reserved'))
                self.db.execute('UPDATE meta SET dispatches=dispatches+1,repairs=repairs+? WHERE id=1',(int(retry),))
                self.event('claim_reserved',{'request_id':request_id,'retry_repair':retry})
            # Deliberately separate DB commits: missing local link is uncertain.
            run=engine._claim(worker,task_id,_scope_key=request_id,_deadline=self.policy['deadline'])
            with self.tx():
                self.db.execute("UPDATE requests SET phase='bound',run_id=? WHERE id=?",(run['id'],request_id));self.event('claim_bound',{'request_id':request_id,'run_id':run['id']})
            return run

    def reconcile(self):
        errors=[]
        with self.locked():
            for row in self.db.execute("SELECT * FROM requests WHERE phase!='closed'").fetchall():
                try:self.recover(row)
                except (ValueError,OSError,sqlite3.Error) as error:errors.append(str(error))
            result=self.status();result['reconciliation_errors']=errors
            return result

    def repair(self,engine,run_id,token,repair_id,reason):
        key(repair_id);require(isinstance(reason,str) and 0<len(reason.strip())<=4096,'repair reason required')
        run=engine.owned(run_id,token);link=engine.db.execute('SELECT request_id FROM scope_links WHERE run_id=?',(run_id,)).fetchone();require(link is not None,'scoped run required')
        payload=encoded({'request_id':link[0],'reason':reason}).decode()
        with self.locked():
            old=self.db.execute('SELECT payload FROM repairs WHERE id=?',(repair_id,)).fetchone()
            if old:require(old[0]==payload,'repair key payload conflict');return self.status()
            require(time.time()<self.policy['deadline'],'scope absolute deadline exhausted')
            row=self.db.execute('SELECT repairs FROM meta WHERE id=1').fetchone();require(row[0]<self.policy['max_repairs'],'scope repair budget exhausted')
            with self.tx():
                self.db.execute('INSERT INTO repairs VALUES(?,?)',(repair_id,payload));self.db.execute('UPDATE meta SET repairs=repairs+1 WHERE id=1');self.event('repair_reserved',{'repair_id':repair_id,'request_id':link[0]})
            return self.status()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['init','status','resolve-not-started']);parser.add_argument('--policy',required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--receipt');parser.add_argument('--receipt-sha256');args=parser.parse_args()
    try:
        descriptor={'path':args.policy,'sha256':args.sha256}
        if args.action=='init':result=initialize(descriptor)
        else:
            with Scope(descriptor) as store:
                if args.action=='resolve-not-started':
                    path=absolute(args.receipt);require(not path.is_relative_to(Path(store.policy['repo'])),'external owner receipt required');raw=regular(path)
                    require(hashlib.sha256(raw).hexdigest()==args.receipt_sha256,'pinned owner receipt required');receipt=json.loads(raw)
                    require(set(receipt)=={'scope_id','request_id','origin_state','outcome','reason'} and receipt['scope_id']==store.policy['scope_id'] and receipt['outcome']=='not_started' and isinstance(receipt['reason'],str) and receipt['reason'].strip(),'explicit owner not-started diagnosis required')
                    with store.locked():
                        row=store.db.execute('SELECT * FROM requests WHERE id=?',(receipt['request_id'],)).fetchone();require(row is not None and row['state']==receipt['origin_state'],'exact original scope request required')
                        require(store.recover(row) is None,'actual Core run exists; cannot declare not started')
                        with store.tx():store.db.execute("UPDATE requests SET phase='closed' WHERE id=?",(row['id'],));store.event('owner_not_started',receipt)
                result=store.status()
        print(json.dumps({'ok':True,'result':result}))
    except (ValueError,OSError,TypeError,sqlite3.Error) as error:
        print(json.dumps({'ok':False,'error':str(error)}));raise SystemExit(1)


if __name__=='__main__':main()
