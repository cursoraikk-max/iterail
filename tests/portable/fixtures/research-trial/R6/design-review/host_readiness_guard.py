#!/usr/bin/env python3
"""Independent native50; static metadata and bounded local IPC, zero model calls."""
import argparse,copy,hashlib,importlib.util,json,os,socket,subprocess,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3]
SECRET='HOST_PRIVATE_SENTINEL_901f74'

def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve();sys.path.insert(0,str(repo));checks=[]
 try:
  from agent_workflow import diagnostics as h
  from agent_workflow.core import digest
  from agent_workflow.mcp import FIELDS
  assert len(FIELDS)==27,'MCP surface changed'
  for name in ['host_contract','validate_host_request','host_readiness']:assert callable(getattr(h,name,None)),'missing reusable host capability: '+name
  spec=importlib.util.spec_from_file_location('host_native_fixture',ROOT/'research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with tempfile.TemporaryDirectory(prefix='host-contract-') as d:
   root=Path(d).resolve();c=m.Cases(repo,root);contract=h.host_contract();assert contract['schema_version']==1 and set(contract['request_kinds'])=={'worker','review','quality_review'};assert contract['authority']=='structural_only' and contract['autonomous_dispatch'] is False and contract['model_dependency'] is False
   f=c.fixture('actual-packets');proof=c.q.baseline(f);tid=c.q.task(f,'actual-host-work');c.q.assessment(f,proof,[c.q.candidate(tid)]);before=f['c'].tick();worker=before['request'];v=before['active_version'];original=f['c'].status();events=f['e'].db.execute('SELECT count(*) FROM events').fetchone()[0]
   def valid(packet,expected=v):
    result=h.validate_host_request(packet,expected_version=expected);assert set(result)=={'kind','request_id','task_id','run_id','version','package_sha256','source_sha256'};assert result['request_id']==packet['id'] and result['kind']==packet['kind'];assert result['task_id']==packet['task_id'] and result['run_id']==packet['run_id'];assert 'controller_path' not in result;return result
   valid(worker);assert f['c'].status()==original and events==f['e'].db.execute('SELECT count(*) FROM events').fetchone()[0]
   run=f['c']._core('claim',{'worker':'native-host-packet-worker'},v);bound=f['c'].bind_run(worker['id'],run['id']);valid(bound['request']);args=c.q.owned(run);f['c']._core('change',args,v);assert f['c']._core('verify',args,v)['passed'];f['c']._core('transition',{**args,'target':'ready_review'},v);review=f['c'].tick()['request'];valid(review)
   g=c.fixture('semantic-packet');c.q.baseline(g);semantic=g['c'].tick()['request'];assert semantic['kind']=='quality_review';valid(semantic,g['c'].status()['active_version']);checks.append('actual durable worker review semantic packets validate structurally without claim or resource authority')
   def denied(fn):
    try:fn()
    except (ValueError,TypeError) as error:assert SECRET not in str(error);return
    raise AssertionError('unsafe input admitted')
   packetlink=root/'packet-controller-link';packetlink.symlink_to(v['controller_path'],target_is_directory=True)
   bads=[{**worker,'controller_path':str(packetlink)},{**worker,'controller_path':v['controller_path']+'/../'+Path(v['controller_path']).name}]
   for key,value in [('id','request-'+SECRET),('version','version-xxx'),('task_id',None),('run_id','run-wrong'),('source_sha256','A'*64),('package_sha256','0'*63),('controller_path','relative/'+SECRET),('kind','dispatch')]:bads.append({**worker,key:value})
   bads.extend([{**worker,'raw_prompt':SECRET},{k:v for k,v in worker.items() if k!='package_sha256'},{**review,'run_id':None},{**semantic,'task_id':tid},{**semantic,'core_policy_sha256':'bad'}])
   for item in bads:denied(lambda item=item:h.validate_host_request(item))
   for key,value in [('id','version-'+'0'*24),('package_sha256','0'*64),('controller_path',str(root))]:denied(lambda key=key,value=value:h.validate_host_request(worker,expected_version={**v,key:value}))
   # A packet's immutable baseline need not equal active version's historical source.
   valid(worker,{**v,'source_sha256':'b'*64});checks.append('strict exact packet keys IDs hashes nullable roles and expected physical identities reject malformed data without baseline reinterpretation')
   work=root/'owned-probes';work.mkdir();marker=root/'executed';exe=root/(SECRET+'-executable');exe.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nPath('+repr(str(marker))+').write_text('+repr(SECRET)+')\n');exe.chmod(0o700);exehash=hashlib.sha256(exe.read_bytes()).hexdigest();before_source=digest(repo);before_work=list(work.iterdir())
   def observe(**kw):
    z=h.host_readiness(str(repo),**kw);assert z['schema_version']==1 and z['source_sha256']==digest(repo) and z['package_sha256']==digest(repo/'agent_workflow');assert z['model_calls']==0 and z['provider_ready'] is False and z['provider_readiness']=='not_tested' and z['autonomous_dispatch'] is False and z['executes_commands'] is False
    raw=json.dumps(z);assert SECRET not in raw and str(root) not in raw and str(repo) not in raw and 'token' not in raw and not marker.exists();return z
   with patch.object(subprocess,'run',side_effect=AssertionError('subprocess forbidden')),patch.object(subprocess,'Popen',side_effect=AssertionError('Popen forbidden')),patch.object(os,'system',side_effect=AssertionError('shell forbidden')),patch.object(os,'execv',side_effect=AssertionError('exec forbidden')),patch.object(socket.socket,'connect',side_effect=AssertionError('connect forbidden')),patch.object(socket.socket,'send',side_effect=AssertionError('send forbidden')),patch.object(socket.socket,'sendall',side_effect=AssertionError('sendall forbidden')),patch.object(socket.socket,'sendto',side_effect=AssertionError('sendto forbidden')),patch.object(socket.socket,'accept',side_effect=AssertionError('accept forbidden')),patch.object(socket,'getaddrinfo',side_effect=AssertionError('DNS forbidden')):
    with patch('socket.socket',side_effect=AssertionError('IPC not requested')):
     z=observe(executable_path=str(exe),expected_executable_sha256=exehash);assert z['executable']['status']=='metadata_only' and z['executable']['sha256']==exehash and z['executable']['size']==exe.stat().st_size;assert z['ipc']['requested'] is False and z['ipc']['loopback_tcp']['status']=='not_requested' and z['ipc']['unix']['status']=='not_requested'
    assert observe()['executable']['status']=='not_requested';assert observe(executable_path=str(root/'missing'))['executable']['status']=='missing';assert observe(executable_path=str(exe),expected_executable_sha256='0'*64)['executable']['status']=='hash_mismatch'
    exe.write_text(exe.read_text()+'# changed\n');assert observe(executable_path=str(exe),expected_executable_sha256=exehash)['executable']['status']=='hash_mismatch';exe.chmod(0o600);assert observe(executable_path=str(exe))['executable']['status']=='not_executable';exe.chmod(0o700)
    link=root/'executable-link';link.symlink_to(exe);assert observe(executable_path=str(link))['executable']['status']=='symlink_rejected';parent=root/'parent-link';parent.symlink_to(root,target_is_directory=True);assert observe(executable_path=str(parent/exe.name))['executable']['status']=='symlink_rejected'
    huge=root/'huge';huge.touch();huge.chmod(0o700)
    with huge.open('wb') as stream:stream.truncate(16777217)
    assert observe(executable_path=str(huge))['executable']['status']=='too_large';assert observe(executable_path=str(work))['executable']['status']=='not_regular'
    if hasattr(os,'mkfifo'):
     fifo=root/'fifo';os.mkfifo(fifo);assert observe(executable_path=str(fifo))['executable']['status']=='not_regular'
    denied(lambda:h.host_readiness(str(repo),probe_ipc=True));denied(lambda:h.host_readiness(str(repo),probe_ipc=True,probe_root=str(repo)));denied(lambda:h.host_readiness(str(repo),probe_ipc=True,probe_root=str(parent)));denied(lambda:h.host_readiness(str(repo),expected_executable_sha256='bad'))
    z=observe(probe_ipc=True,probe_root=str(work));assert z['ipc']['requested'] is True
    for key in ['loopback_tcp','unix']:
     probe=z['ipc'][key];assert probe['status'] in {'available','blocked','unsupported'};assert probe['errno'] is None or type(probe['errno']) is int
    assert list(work.iterdir())==before_work
    # Deterministic mock only; this is explicitly not live EPERM evidence.
    with patch('socket.socket',side_effect=PermissionError(1,SECRET)):
     blocked=observe(probe_ipc=True,probe_root=str(work));assert blocked['ipc']['loopback_tcp']['status']=='blocked' and blocked['ipc']['loopback_tcp']['errno']==1;assert blocked['ipc']['unix']['status'] in {'blocked','unsupported'}
    assert list(work.iterdir())==before_work
    bounded=root/'bounded-source';bounded.mkdir();(bounded/'agent_workflow').mkdir();(bounded/'agent_workflow/__init__.py').write_text('')
    if hasattr(os,'mkfifo'):
     os.mkfifo(bounded/'ignored-fifo');ignored=h.host_readiness(str(bounded));assert ignored['provider_readiness']=='not_tested' and ignored['source_sha256']==digest(bounded)
    large=bounded/'aggregate-large'
    with large.open('wb') as stream:stream.truncate(67108865)
    denied(lambda:h.host_readiness(str(bounded)))
   assert digest(repo)==before_source;checks.append('bounded executable metadata hashes missing symlink nonregular oversize and drift; no subprocess/executable/provider invocation')
   checks.append('actual optional local IPC observation and cleanup; injected EPERM is deterministic mock only, never live integration claim')
   source_link=root/'source-link';source_link.symlink_to(repo,target_is_directory=True);denied(lambda:h.host_readiness(str(source_link)))
   env=dict(os.environ,PYTHONPATH=str(repo),PYTHONDONTWRITEBYTECODE='1');argv=[sys.executable,'-B','-m','agent_workflow.diagnostics','host-readiness','--source-root',str(repo),'--executable',str(exe)];cli=subprocess.run(argv,cwd=root,env=env,text=True,capture_output=True,timeout=10);assert cli.returncode==0,cli.stdout+cli.stderr;out=json.loads(cli.stdout);assert out['ok'] is True and out['result']['source_sha256']==before_source and out['result']['provider_readiness']=='not_tested';assert SECRET not in cli.stdout and str(root) not in cli.stdout
   invalid=subprocess.run(argv+['--probe-ipc'],cwd=root,env=env,text=True,capture_output=True,timeout=10);assert invalid.returncode!=0 and json.loads(invalid.stdout)['ok'] is False and SECRET not in invalid.stdout;assert digest(repo)==before_source;checks.append('real host-readiness CLI structured success and invalid input; source unchanged; no private path/error echo')
  print(json.dumps({'passed':True,'checks':checks,'native_local_fixture':True,'model_calls':0,'network_connections':0,'provider_readiness':'not_tested','ipc_permission_observation':z['ipc'],'mock_permission_failure_separately_labeled':True}));return 0
 except Exception as ex:
  print(json.dumps({'passed':False,'checks':checks,'error_type':type(ex).__name__,'error':str(ex),'native_local_fixture':True,'model_calls':0,'network_connections':0}));return 1
if __name__=='__main__':sys.exit(main())
