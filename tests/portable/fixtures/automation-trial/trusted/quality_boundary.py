"""Supplemental independent boundary regressions; disposable native fixtures only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,required=True)
    args=parser.parse_args(); repo=args.repo.resolve()
    spec=importlib.util.spec_from_file_location('frozen_contract',Path(__file__).with_name('quality_guard.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    results=[]
    with tempfile.TemporaryDirectory(prefix='quality-boundary-') as temp:
        c=module.Contract(repo,Path(temp))
        from agent_workflow.quality import external
        from agent_workflow.producer import bind_task
        def case(name,fn):
            try:
                observed=fn(); results.append({'case':name,'passed':True,'observed':observed})
            except AssertionError as error:
                results.append({'case':name,'passed':False,'error':str(error)})
        def producer():
            f=c.fixture('producer');f['e'].db.close();shutil.rmtree(f['root']/'state')
            checker=f['root']/'checker.py'
            checker.write_text('import sys,json\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(repo))+')\nfrom agent_workflow.core import digest\nprint(json.dumps(dict(id="native",passed=True,classification="passed",source_stable=True,exactsourceSHA=digest(sys.argv[1]))))\n')
            policy={'schema_version':1,'allowed_sources':['failed_log','unresolved_review','owner_check'],'registry':{'native':{'argv':[sys.executable,str(checker),'{repo}'],'acceptance':{'exit_code':1,'classification':'product_defect','source_stable':True}}},'queue':{'max_open':4,'max_new_per_cycle':1,'wip':1,'aging_seconds':60,'repro_seconds':3,'log_bytes':4096}}
            path=f['root']/'producer.json';module.write_json(path,policy)
            f['config']['producer_policy']={'path':str(path),'sha256':module.sha(path.read_bytes())}
            f['e']=c.Engine(f['root']/'state',f['config'])
            tid=c.task(f,'baseline-proof');bind_task(f['e'],tid,'native',c.digest(f['repo']),policy)
            run=f['e'].claim('worker',tid);own=c.owned(run);f['e'].change(**own)
            assert f['e'].verify(**own)['passed'],'producer baseline verifier failed'
            f['e'].transition(**own,target='ready_review');f['e'].transition(**own,target='accepted')
            feature=c.task(f,'producer-feature');c.assessment(f,run['id'],[c.candidate(feature)])
            checker.write_text(checker.read_text()+'# changed trusted owner checker\n')
            advice=f['e'].next_step(task_id=tid)
            assert advice['next_action']=='owner_reassess',advice
            q=advice['quality']
            admitted=False
            try:f['e'].claim('worker',feature);admitted=True
            except c.Error:pass
            assert not q['claim_allowed'] and not admitted,{'legacy':advice['next_action'],'quality':q,'actual_feature_claim_admitted':admitted}
            return 'trusted checker drift blocks projection and real feature claim'
        case('producer_owner_identity',producer)
        def verified():
            f=c.fixture('verified');tid=c.task(f,'baseline-proof');run=f['e'].claim('worker',tid);own=c.owned(run)
            f['e'].change(**own);assert f['e'].verify(**own)['passed']
            feature=c.task(f,'feature-before-review');c.assessment(f,run['id'],[c.candidate(feature)])
            q=f['e'].next_step(task_id=tid)['quality']
            assert q['disposition']=='wait_review' and not q['claim_allowed'],q
            assert not q['host_request'] or q['host_request']['kind']!='worker',q
            return 'verified pending review emits no feature advancement'
        case('verified_pending_review',verified)
        def intent():
            f=c.fixture('intent');old=c.baseline(f);feature=c.task(f,'pending-feature');c.assessment(f,old,[c.candidate(feature)])
            run=f['e'].claim('worker',feature);own=c.owned(run);f['e'].change(**own);assert f['e'].verify(**own)['passed'];f['e'].transition(**own,target='ready_review')
            q=f['e'].next_step(task_id=f['query_task'])['quality'];h=q['host_request']
            assert h and h.get('kind')=='reviewer' and h.get('task_id')==feature and h.get('run_id')==run['id'],q
            return 'old anchor query targets actual pending task and run'
        case('pending_review_target',intent)
        def recovery():
            f=c.fixture('recovery');c.baseline(f);q=f['e'].next_step(task_id=f['query_task'])['quality']
            assert q['resume_conditions'] and all(not s.endswith('_resolved') for s in q['resume_conditions']),q
            return q['resume_conditions']
        case('concrete_recovery_conditions',recovery)
        def regular():
            f=c.fixture('regular');directory=f['root']/'directory';directory.mkdir()
            try:external(str(directory),f['repo'])
            except ValueError:return 'directory rejected'
            raise AssertionError('external accepted directory as a regular file')
        case('regular_file_required',regular)
        def fifo():
            f=c.fixture('fifo');pipe=f['root']/'input.fifo';os.mkfifo(pipe)
            config=dict(f['config'],quality_policy={'path':str(pipe),'sha256':'0'*64})
            code='import sys,json;sys.path.insert(0,'+repr(str(repo))+');from agent_workflow.quality import load\ntry:load(json.loads(sys.argv[1]))\nexcept (ValueError,OSError):sys.exit(0)\nsys.exit(1)'
            try:result=subprocess.run([sys.executable,'-B','-c',code,json.dumps(config)],capture_output=True,timeout=1)
            except subprocess.TimeoutExpired:raise AssertionError('FIFO read blocked until independent 1s timeout; child reaped')
            assert result.returncode==0,'FIFO was not rejected'
            return 'FIFO rejected promptly without blocking reads'
        case('fifo_bounded_rejection',fifo)
        def ui():
            f=c.fixture('ui');proof=c.baseline(f);tid=c.task(f,'ui-feature');c.assessment(f,proof,[c.candidate(tid)])
            run=f['e'].claim('worker',tid);own=c.owned(run);f['e'].change(**own);assert f['e'].verify(**own)['passed'];f['e'].transition(**own,target='ready_review')
            c.assessment(f,run['id'],changes={'ui':'pass'})
            value=json.loads(f['assessment_path'].read_text());value['dimensions']['ui']['evidence']=['native:non-ui'];module.write_json(f['assessment_path'],value)
            try:f['e'].transition(**own,target='accepted')
            except c.Error:return 'non-UI pass assessment rejected at real acceptance'
            raise AssertionError('real accepted transition allowed ui:pass under ui_required:false')
        case('non_ui_acceptance',ui)
    record={'passed':all(r['passed'] for r in results),'results':results,'source_sha256':c.digest(repo),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'model_calls':0,'network_calls':0,'external_api_calls':0}
    print(json.dumps(record,ensure_ascii=False));return 0 if record['passed'] else 1
if __name__=='__main__':sys.exit(main())
