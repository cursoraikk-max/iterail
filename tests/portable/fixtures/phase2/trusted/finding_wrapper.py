"""Two fixed owner contract sources; feedback never selects executable code."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('check',choices=['producer-contract','memory-contract']);p.add_argument('--repo',required=True);a=p.parse_args();repo=Path(a.repo).resolve();sys.path.insert(0,str(repo))
from agent_workflow.core import digest
script=Path(__file__).parent/({'producer-contract':'producer.py','memory-contract':'memory.py'}[a.check]);before=digest(repo);argv=[sys.executable,str(script),'--repo',str(repo)]
try:
 process=subprocess.run(argv,capture_output=True,text=True,timeout=45);payload=json.loads(process.stdout);after=digest(repo)
 valid=(isinstance(payload,dict) and type(payload.get('passed')) is bool and payload.get('source_digest')==before==after and payload.get('source_stable') is True and payload.get('external_backend_calls')==0 and process.returncode in (0,1) and ((process.returncode==0)==payload['passed']))
 cases=payload.get('cases',[]);infrastructure=any(str(c.get('error','')).startswith(('OSError:','PermissionError:','FileNotFoundError:','TimeoutExpired:','OperationalError:')) for c in cases)
 classification='infrastructure_error' if not valid or infrastructure else ('passed' if payload['passed'] else 'product_defect')
 report={'id':a.check,'passed':classification=='passed','classification':classification,'source_stable':before==after,'exactsourceSHA':before,'evidence':[{'argv':argv,'exit_code':process.returncode,'stdout':payload,'stdout_sha256':hashlib.sha256(process.stdout.encode()).hexdigest(),'stderr_sha256':hashlib.sha256(process.stderr.encode()).hexdigest(),'checker_sha256':hashlib.sha256(script.read_bytes()).hexdigest()}]}
except (OSError,subprocess.TimeoutExpired,ValueError,KeyError,TypeError) as e:
 report={'id':a.check,'passed':False,'classification':'infrastructure_error','source_stable':digest(repo)==before,'exactsourceSHA':before,'evidence':[{'error':type(e).__name__,'detail':str(e),'argv':argv}]}
print(json.dumps(report));sys.exit(0 if report['passed'] else 1 if report['classification']=='product_defect' else 2)
