"""Independent fixed-policy reproduction; records exact argv and raw output hashes."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'phase5/trusted'));from common import source_digest
stage=sys.argv[1];assert stage in {'task1','task2'}
policy_path=ROOT/'phase7'/('project-'+stage+'.json');policy=json.loads(policy_path.read_bytes());source=Path(policy['repo']);before=source_digest(source)
folder=ROOT/'phase7/evidence'/('independent-'+stage);folder.mkdir(exist_ok=False)
results=[];start=time.time();env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
for i,argv in enumerate(policy['tests']):
    called=time.time();out=folder/('%02d.stdout.log'%i);err=folder/('%02d.stderr.log'%i)
    try:
        with out.open('wb') as so,err.open('wb') as se:p=subprocess.run(argv,cwd=source,env=env,stdin=subprocess.DEVNULL,stdout=so,stderr=se,timeout=60)
        code=p.returncode;error=None
    except subprocess.TimeoutExpired:code=-1;error='TimeoutExpired'
    result={'index':i,'argv':argv,'exit_code':code,'execution_error':error,'seconds':time.time()-called,'stdout_path':str(out.relative_to(ROOT)),'stderr_path':str(err.relative_to(ROOT)),'stdout_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256(err.read_bytes()).hexdigest()};results.append(result)
    (folder/'progress.json').write_text(json.dumps({'completed':len(results),'total':len(policy['tests']),'latest_exit_code':code},sort_keys=True))
after=source_digest(source);report={'schema_version':1,'stage':stage,'policy_sha256':hashlib.sha256(policy_path.read_bytes()).hexdigest(),'exact_source_sha256':before,'after_source_sha256':after,'source_unchanged':before==after,'passed':all(r['exit_code']==0 and r['execution_error'] is None for r in results) and before==after,'evidence_kind':'native_fixture','external_provider_calls':0,'network_calls':0,'seconds':time.time()-start,'results':results}
(folder/'summary.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'commands':len(results),'source':before,'seconds':report['seconds'],'summary':str((folder/'summary.json').relative_to(ROOT))}));raise SystemExit(0 if report['passed'] else 1)
