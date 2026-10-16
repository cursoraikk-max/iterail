"""Bounded local fixture mailbox guardian; never signals a recovered PID."""
import os
from pathlib import Path
import subprocess
import sys
import time
from .continuation import Continuation,digest,encoded,load,regular,require,sha,absolute


def guarded_supervise(spec_path, supervise):
    job=Path(spec_path).parent;intent=load(regular(job/'intent.json'));spec=load(regular(spec_path))
    c=Continuation(intent['continuation_state'])
    try:
        try:
            with c._locked():
                version=load(c._row()['active_version']);c._runtime(version)
                require(digest(Path(__file__).resolve().parent)==version['package_sha256'],'reviewed host controller required')
                row=c.db.execute('SELECT body FROM host_process_intents WHERE request_id=?',(intent['request_id'],)).fetchone()
                require(row is not None and load(row[0])==intent,'exact committed launch intent required')
                command=spec['command'];p=command['policy'];raw=regular(absolute(command['policy_path']))
                require(sha(raw)==command['policy_sha256'] and load(raw)==p
                        and intent['adapter_identity']=='local-process-v1:'+command['policy_sha256'], 'pinned launch policy changed')
                require(spec['argv']==[p['executable_path'],'-I','-B',str(job/'script.py')]
                        and spec['cwd']==p['cwd']==intent['workdir'], 'exact frozen launch argv and cwd required')
                cwd=absolute(spec['cwd']);require(cwd.is_dir() and not cwd.is_relative_to(Path(intent['source_root'])), 'canonical external cwd required before spawn')
                identity=cwd.stat();require(spec['cwd_identity']=={'device':identity.st_dev,'inode':identity.st_ino},'cwd directory identity changed before spawn')
                # Validate original cooperative inputs again at the final spawn
                # boundary, then execute the approved job snapshot, never the
                # mutable original pathname. Same-UID malicious rewrites of this
                # private snapshot/interpreter remain outside the trust contract.
                for field in ('executable','script'):
                    require(sha(regular(absolute(p[field+'_path']),67108864))==p[field+'_sha256'], 'fixture command changed before spawn')
                require(sha(regular(job/'script.py',67108864))==p['script_sha256'],'frozen script snapshot changed')
                require(c._gate(c._row()) is None and not (job/'cancel.json').exists()
                        and time.time()<intent['deadline'], 'original host gate changed before process launch')
        except (ValueError,OSError,TypeError,KeyError) as error:
            value={'execution_id':intent['execution_id'],'dispatch_key':intent['dispatch_key'],
                   'stage':'before_fixture_spawn','error':type(error).__name__+': '+str(error),'observed_at':time.time()}
            temp=job/'launch-rejection.tmp';temp.write_bytes(encoded(value));temp.replace(job/'launch-rejection.json')
            raise
        return supervise(spec)
    finally:c.db.close()


def main(directory):
    job=Path(directory);intent=load(regular(job/'intent.json'));spec=load(regular(job/'spec.json'))
    sequence=0
    def observation(state):
        nonlocal sequence
        sequence+=1
        value={k:intent[k] for k in ('execution_id','dispatch_key','nonce')}
        value.update(state=state,sequence=sequence,observed_at=time.time())
        temp=job/'observation.tmp';temp.write_bytes(encoded(value));temp.replace(job/'observation.json')
    # Exactly one wrapper may claim this job. A stale marker never grants a retry.
    fd=os.open(job/'guardian-started',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    require(time.time()<intent['deadline'] and spec['cwd']==intent['workdir'],'original deadline and exact worker directory required')
    c=Continuation(intent['continuation_state'])
    try:
        with c._locked():
            require(c._gate(c._row()) is None and not (job/'cancel.json').exists(),'owner stopped, expired or cancellation pending')
    finally:c.db.close()
    code='import sys;sys.path.insert(0,sys.argv[1]);from agent_workflow.guardian import supervise;from agent_workflow.hostprocess import guarded_supervise;guarded_supervise(sys.argv[2],supervise)'
    guardian=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(Path(__file__).resolve().parent.parent),str(job/'spec.json')],
                              stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    observation('running');reason=None
    try:
        while guardian.poll() is None:
            if time.time()>=intent['deadline']:reason='expired'
            if (job/'cancel.json').exists():
                cancel=load(regular(job/'cancel.json'))
                require(cancel=={'execution_id':intent['execution_id'],'nonce':intent['nonce']},'exact local cancel request required')
                reason=reason or 'cancelled'
            if reason:
                guardian.stdin.close();guardian.wait(timeout=10);break
            observation('running')
            time.sleep(.1)
        guardian.wait(timeout=10)
        result=load(regular(job/'result.json'))
        require(result['call_id']==intent['execution_id'] and result['group_termination_confirmed'] is True,'actual guardian result required')
        state=reason or ('expired' if result['reason'] in {'timeout','total_timeout'} else 'completed' if result['exit_code']==0 else 'failed')
        observation(state)
    finally:
        if not guardian.stdin.closed:guardian.stdin.close()
        # EOF invokes the existing guardian's bounded group cleanup. The wrapper
        # never adopts or signals a process identity from a PID file.
        if guardian.poll() is None:guardian.wait(timeout=10)


if __name__=='__main__':main(sys.argv[1])
