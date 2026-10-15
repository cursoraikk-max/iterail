#!/usr/bin/env python3
"""Independent additive regression; expired lease is controlled fixture data, not elapsed operation."""
import argparse, importlib.util, json, sys, tempfile, time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve()
sys.path.insert(0,str(repo))
helper=Path(__file__).resolve().parents[2]/'trusted/continuation_quality_guard.py'
spec=importlib.util.spec_from_file_location('native45fixture',helper);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
from agent_workflow.continuation import digest
before=digest(repo);results=[]
with tempfile.TemporaryDirectory(prefix='independent-expired-quality-') as temp:
 c=m.Cases(repo,Path(temp).resolve())
 for target in ('verified','ready_review'):
  f=c.fixture('expired-'+target);baseline=c.q.baseline(f);tid=c.q.task(f,'expired-proof');c.q.assessment(f,baseline,[c.q.candidate(tid)])
  run=f['e'].claim('independent-native-fixture',tid);owned=c.q.owned(run);f['e'].change(**owned);assert f['e'].verify(**owned)['passed']
  if target=='ready_review':f['e'].transition(**owned,target=target)
  # Explicitly controlled data expires a real machine-verified fixture lease.
  f['e'].db.execute('UPDATE runs SET lease_until=? WHERE id=?',(time.time()-1,run['id']))
  c.q.assessment(f,baseline)
  status=f['c'].tick()
  results.append({'stored_state':target,'phase':status['phase'],'requests_spent':status['requests_spent'],'request':status['request'],'passed':status['phase']=='blocked' and status['request'] is None and status['requests_spent']==0})
after=digest(repo);passed=before==after and all(r['passed'] for r in results)
print(json.dumps({'passed':passed,'source_before':before,'source_after':after,'cases':results,'native_simulation':True,'external_agent_calls':0,'network_calls':0},sort_keys=True))
sys.exit(0 if passed else 1)
