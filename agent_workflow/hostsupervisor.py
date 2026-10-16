"""One admitted dispatch per invocation; Core/Continuation retain all authority.

The protocol is model/platform independent. LocalProcess is an opt-in POSIX
fixture implementation, not a provider or an installed wake source.
"""
import argparse
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import time
from typing import Protocol
import uuid
from .continuation import Continuation, absolute, digest, encoded, hash_value, load, regular, require, sha
from .hostbridge import HostBridge


class HostAdapter(Protocol):
    identity: str
    workdir: str
    def start(self, intent: dict) -> dict: ...
    def observe(self, intent: dict) -> dict: ...
    def cancel(self, intent: dict) -> dict: ...
    def reconcile(self, intent: dict) -> dict: ...


def write_once(path, value, *, raw=False):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(value if raw else encoded(value));stream.flush();os.fsync(stream.fileno())


class LocalProcess:
    """Owner-pinned fixture command with a nonce-bound durable guardian mailbox.

    Restart callers never signal a PID. Only the original guardian owns process
    handles. Missing/mismatched evidence is uncertain and cannot permit restart.
    """
    def __init__(self, continuation, policy_path, policy_sha256):
        self.c = continuation
        path = absolute(policy_path)
        require(not path.is_relative_to(Path(self.c.policy['repo'])), 'host policy outside candidate required')
        raw = regular(path);require(sha(raw) == policy_sha256, 'pinned host policy required')
        p = load(raw)
        require(set(p) == {'schema_version','fixture_only','continuation_state','executable_path','executable_sha256',
                          'script_path','script_sha256','cwd','max_seconds','output_bytes'}
                and p['schema_version'] == 1 and type(p['schema_version']) is int and p['fixture_only'] is True
                and p['continuation_state'] == str(self.c.root), 'exact local fixture owner policy required')
        require(type(p['max_seconds']) is int and 1 <= p['max_seconds'] <= 300
                and type(p['output_bytes']) is int and 1 <= p['output_bytes'] <= 1048576, 'bounded fixture resources required')
        # Recovery binds policy/dispatch identity, not mutable launch inputs.
        # Missing/edited scripts, executables or cwd must not disable cancellation.
        for field in ('executable','script','cwd'):
            value=p[field+'_path'] if field!='cwd' else p[field]
            require(isinstance(value,str) and Path(value).is_absolute() and os.path.normpath(value)==value
                    and not Path(value).is_relative_to(Path(self.c.policy['repo'])), 'external absolute fixture path required')
            if field!='cwd':require(hash_value(p[field+'_sha256']),'fixture command hash required')
        self.policy = p;self.workdir=p['cwd'];self.policy_path = path;self.identity = 'local-process-v1:'+policy_sha256
        self.root = self.c.root/'host-process-jobs'
        require(not self.root.is_symlink(), 'regular host jobs required');self.root.mkdir(mode=0o700,exist_ok=True)

    def _job(self, intent):
        require(intent['adapter_identity'] == self.identity and intent['workdir']==self.workdir
                and intent['source_root']==self.c.policy['repo'] and intent['continuation_state']==str(self.c.root)
                and len(intent['dispatch_key']) == 64
                and all(c in '0123456789abcdef' for c in intent['dispatch_key']), 'exact adapter dispatch required')
        job = self.root/intent['dispatch_key']
        require(not job.is_symlink(), 'regular dispatch directory required')
        return job

    def observe(self, intent):
        job = self._job(intent)
        if not (job/'intent.json').exists():return self._observation(intent, 'uncertain', 0)
        recorded = load(regular(job/'intent.json'))
        require(recorded == intent, 'durable local intent changed')
        if not (job/'observation.json').exists():return self._observation(intent, 'uncertain', 0)
        value = load(regular(job/'observation.json'))
        require(isinstance(value,dict) and value.get('execution_id') == intent['execution_id'] and value.get('dispatch_key') == intent['dispatch_key']
                and value.get('nonce') == intent['nonce'] and type(value.get('sequence')) is int
                and value['sequence'] > 0 and value.get('state') in {'running','completed','cancelled','expired','failed'}
                and type(value.get('observed_at')) in (int,float) and math.isfinite(value['observed_at']),
                'authenticated bounded host observation required')
        # Missing terminal result cannot be upgraded to completed by a status file.
        if value['state'] in {'completed','cancelled','expired','failed'}:
            result = load(regular(job/'result.json'))
            require(isinstance(result,dict) and result.get('call_id') == intent['execution_id'] and result.get('group_termination_confirmed') is True,
                    'actual guardian cleanup result required')
        if value['state']=='running' and (time.time()-value['observed_at']>2 or value['observed_at']>time.time()+1):
            return self._observation(intent,'uncertain',value['sequence'])
        value.pop('nonce');return value

    @staticmethod
    def _observation(intent, state, sequence):
        return {k:intent[k] for k in ('execution_id','dispatch_key')} | {'state':state,'sequence':sequence,'observed_at':time.time()}

    def reconcile(self, intent):return self.observe(intent)

    def cancel(self, intent):
        job = self._job(intent)
        if not (job/'intent.json').exists():return self._observation(intent, 'uncertain', 0)
        require(load(regular(job/'intent.json')) == intent, 'exact cancel identity required')
        path = job/'cancel.json';value = {'execution_id':intent['execution_id'],'nonce':intent['nonce']}
        if path.exists():require(load(regular(path)) == value, 'cancel identity changed')
        else:write_once(path,value)
        return self.observe(intent)  # A request is not proof of cancellation.

    def start(self, intent):
        job = self._job(intent)
        if job.exists():return self.reconcile(intent)
        require(self.c._gate(self.c._row()) is None,'original continuation stopped or expired')
        version=load(self.c._row()['active_version']);self.c._runtime(version)
        require(digest(Path(__file__).resolve().parent)==version['package_sha256'],'reviewed host controller required')
        # mkdir is the durable local dedupe barrier. A crash after it is uncertain.
        job.mkdir(mode=0o700)
        write_once(job/'intent.json',intent)
        p = self.policy
        require(sha(regular(self.policy_path)) == self.identity.split(':')[1], 'host policy changed')
        cwd=absolute(p['cwd']);require(cwd.is_dir() and not cwd.is_relative_to(Path(intent['source_root'])),'owned external fixture work directory required for start')
        identity=cwd.stat();cwd_identity={'device':identity.st_dev,'inode':identity.st_ino}
        for field in ('executable','script'):
            file=absolute(p[field+'_path']);raw=regular(file,67108864)
            require(sha(raw) == p[field+'_sha256'], 'fixture command changed')
            if field=='script':write_once(job/'script.py',raw,raw=True)
        require(time.time() < intent['deadline'], 'host original deadline expired')
        spec = {'argv':[p['executable_path'],'-I','-B',str(job/'script.py')], 'cwd':intent['workdir'],
                'env':{'PATH':os.defpath,'HOME':str(job),'LANG':'C.UTF-8','ITERAIL_EXECUTION_ID':intent['execution_id']},
                'seconds':min(p['max_seconds'],intent['deadline']-time.time()),'total_cutoff':intent['deadline'],
                'output':str(job/'output.log'),'result':str(job/'result.json'),'bytes':p['output_bytes'],
                'call_id':intent['execution_id'],
                'cwd_identity':cwd_identity,'command':{'policy':p,'policy_path':str(self.policy_path),'policy_sha256':self.identity.split(':')[1]}}
        write_once(job/'spec.json',spec)
        env = {'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
        # Bootstrap imports only the physical installed package, including in -I tests.
        code = 'import sys;sys.path.insert(0,sys.argv[1]);from agent_workflow.hostprocess import main;main(sys.argv[2])'
        subprocess.Popen([sys.executable,'-I','-B','-c',code,str(Path(__file__).resolve().parent.parent),str(job)],
                         cwd=job,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return self.observe(intent)


class OneShot:
    def __init__(self, continuation, adapter: HostAdapter):
        self.c=continuation;self.adapter=adapter
        with self.c._locked():
            self._controller()
            self.c.db.execute('CREATE TABLE IF NOT EXISTS host_process_intents(request_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            self.c.db.execute('CREATE TABLE IF NOT EXISTS host_adapter_identity(id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL)')
            with self.c._tx():
                row=self.c.db.execute('SELECT identity FROM host_adapter_identity WHERE id=1').fetchone()
                if row:require(row[0] == adapter.identity,'immutable host adapter identity changed')
                else:self.c.db.execute('INSERT INTO host_adapter_identity VALUES(1,?)',(adapter.identity,))

        self.b=HostBridge(continuation)

    def _controller(self):
        row=self.c._row();version=load(row['active_version']);self.c._runtime(version)
        require(digest(Path(__file__).resolve().parent)==version['package_sha256'],'reviewed host controller required')

    @contextlib.contextmanager
    def _serial(self):
        path=self.c.root/'host-oneshot.lock';require(not path.is_symlink(),'regular one-shot lock required')
        with path.open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)

    def _intents(self):
        with self.c._locked():
            self.c._identity()
            rows=self.c.db.execute('SELECT body FROM host_process_intents ORDER BY rowid LIMIT 2').fetchall()
            require(len(rows)<=1,'one-shot intent ceiling inconsistent')
            return [load(row[0]) for row in rows]

    def _record(self, intent, observation):
        require(isinstance(observation,dict) and set(observation)=={'execution_id','dispatch_key','state','sequence','observed_at'}
                and observation['state'] in {'uncertain','running','completed','cancelled','expired','failed'}
                and type(observation['sequence']) is int and observation['sequence']>=0
                and type(observation['observed_at']) in (int,float) and math.isfinite(observation['observed_at'])
                and observation['observed_at']<=time.time()+1, 'structured host observation required')
        require(observation.get('execution_id') == intent['execution_id'] and observation.get('dispatch_key') == intent['dispatch_key'],
                'host observation identity mismatch')
        # Host evidence does not bind a run, resolve quality, accept or promote.
        with self.c._locked():
            if observation['state']!='uncertain':
                require(observation['sequence']>0,'positive host cursor required')
                if observation['state']=='running':
                    require(time.time()-observation['observed_at']<=2,'stale running observation')
                # An intervening uncertain observation must never reset a cursor
                # or erase a previously observed terminal result.
                for previous in self.c.db.execute("SELECT body FROM events WHERE kind='host_process_observed' ORDER BY seq DESC"):
                    prior=load(previous[0])
                    require(observation['sequence']>=prior['sequence'],'host observation cursor regressed')
                    if prior['state'] in {'completed','cancelled','expired','failed'}:
                        require(observation==prior,'terminal host observation changed')
            with self.c._tx():self.c._event('host_process_observed',observation)
        return {'intent':{k:v for k,v in intent.items() if k!='nonce'},'host':observation,'core_acceptance':False}

    def run(self, action='tick'):
        require(action in {'tick','observe','cancel','reconcile'},'one-shot action required')
        with self._serial():
            with self.c._locked():self._controller()
            previous=self._intents()
            if previous:
                # This first thin slice deliberately stops at one intent per owner grant.
                # No successor is auto-created after terminal or uncertain outcomes.
                intent=previous[-1]
                with self.c._locked():gate=self.c._gate(self.c._row())
                fn=self.adapter.cancel if action=='cancel' or gate is not None else self.adapter.reconcile if action in {'tick','reconcile'} else self.adapter.observe
                return self._record(intent,fn(intent))
            if action != 'tick':return {'host':{'state':'not_started'},'core_acceptance':False}
            with self.c._locked():
                require(self.c._gate(self.c._row()) is None,'original continuation stopped or expired')
            ticket=self.b.reserve()
            # Consumed bridge without an adapter intent means the prior outcome is unknown.
            require(ticket['phase']=='reserved' and ticket['dispatch_started'] is False,'existing dispatch requires owner reconciliation')
            begun=self.b.begin(ticket['request_id'])
            with self.c._locked():
                row=self.c._row();require(self.c._gate(row) is None,'original continuation stopped or expired')
                entry=self.b._get(ticket['request_id']);self.b._pending(entry,row)
                intent={'request_id':ticket['request_id'],'dispatch_key':ticket['dispatch_key'],
                        'execution_id':'execution-'+uuid.uuid4().hex,'nonce':uuid.uuid4().hex,
                        'adapter_identity':self.adapter.identity,'role':ticket['kind'],
                        'deadline':row['deadline'],'workdir':self.adapter.workdir,'source_root':self.c.policy['repo'],
                        'continuation_state':str(self.c.root),'packet':begun['packet']}
                with self.c._tx():
                    self.c.db.execute('INSERT INTO host_process_intents VALUES(?,?)',(intent['request_id'],encoded(intent).decode()))
                    self.c._event('host_process_intent', {k:v for k,v in intent.items() if k not in {'nonce','packet'}})
            # Recheck owner stop while serializing against owner mutations. No slow
            # Core operation occurs here. Intent remains consumed even if start fails.
            with self.c._locked():
                require(self.c._gate(self.c._row()) is None,'original continuation stopped or expired')
                observation=self.adapter.start(intent)
            return self._record(intent,observation)


def main():
    p=argparse.ArgumentParser(description='Bounded one-shot local fixture host; no models or OS wake registration')
    p.add_argument('--state',required=True);p.add_argument('--host-policy',required=True);p.add_argument('--host-policy-sha256',required=True)
    p.add_argument('action',choices=['tick','observe','cancel','reconcile']);args=p.parse_args();c=None
    try:
        c=Continuation(args.state);adapter=LocalProcess(c,args.host_policy,args.host_policy_sha256)
        print(json.dumps({'ok':True,'result':OneShot(c,adapter).run(args.action)}))
    except (ValueError,TypeError,OSError,sqlite3.DatabaseError) as e:
        print(json.dumps({'ok':False,'error':type(e).__name__+': '+str(e)}));raise SystemExit(1)
    finally:
        if c is not None:c.db.close()


if __name__=='__main__':main()
