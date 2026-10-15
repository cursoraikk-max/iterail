"""Foundation subprocess tests; these do not establish actual AI integration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agent_workflow.runner import Runner, RunnerError, JOBCTL, validate_config


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        (self.repo/'core.py').write_text('baseline\n')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Test','-c','user.email=test@invalid','commit','-qm','baseline'],check=True)
        self.check=self.root/'check.py';self.check.write_text('import json; print(json.dumps({"id":"real-local-check","evidence":[],"repair":"Fixture check"}));raise SystemExit(1)\n')
        self.config={'repo':str(self.repo),'backend':sys.executable,'checks':[{'id':'real-local-check','argv':[sys.executable,str(self.check),'--repo','{repo}'],'repair':'Fixture check'}],
                     'tests':[[sys.executable,'-c','print("machine")']], 'budget':{'wall_seconds':30,'max_calls':2,'call_seconds':2,'log_bytes':1024,'max_attempts':2},'allowlist':['agent_workflow/core.py','agent_workflow/core_*.py','docs/*.md'],'poll_seconds':1}
        self.r=Runner(self.root/'state',self.config);self.addCleanup(self.r.db.close)

    def test_policy_budget_and_stop_persist(self):
        self.r.reserve(None,'backend:worker');self.r.reserve(None,'backend:reviewer')
        with self.assertRaises(RunnerError):self.r.reserve(None,'backend:retry')
        self.r.set_stop(True)
        other=Runner(self.root/'state');self.addCleanup(other.db.close)
        self.assertTrue(other.stopped())
        with self.assertRaises(RunnerError):other.reserve(None,'discovery')
        altered=json.loads(json.dumps(self.config));altered['budget']['max_calls']=3
        with self.assertRaises(RunnerError):Runner(self.root/'state',altered)
        self.assertEqual(other.status()['meta']['calls'],2)

    def test_worker_requires_reviewer_allowance_and_denial_is_uncharged(self):
        ident,_=self.r.reserve(None,'backend:worker')
        # A failed implementation still consumes its real call reservation.
        self.r.db.execute("UPDATE calls SET phase='finished',result=? WHERE id=?",
                          (json.dumps({'exit_code':1}),ident))
        before=self.r.status()
        with self.assertRaisesRegex(RunnerError,'reviewer'):
            self.r.reserve(None,'backend:worker')
        after=self.r.status()
        for key in ('calls','reserved_seconds','reserved_bytes'):
            self.assertEqual(after['meta'][key],before['meta'][key])
        self.assertEqual(after['calls'],before['calls'])
        self.assertEqual(after['events'],before['events'])
        # The last session remains available for the independent reviewer.
        self.r.reserve(None,'backend:reviewer')
        self.assertEqual(self.r.status()['meta']['calls'],2)
        with self.assertRaises(RunnerError):self.r.reserve(None,'backend:reviewer')

    def test_worker_needs_wall_for_review_and_machine_gates(self):
        self.r.db.execute('UPDATE meta SET started=?',(time.time()-27,))
        before=self.r.status()
        with self.assertRaisesRegex(RunnerError,'wall'):
            self.r.reserve(None,'backend:worker')
        self.assertEqual(self.r.status()['meta']['calls'],before['meta']['calls'])
        self.assertEqual(self.r.status()['calls'],before['calls'])

    def test_budget_preflight_does_not_prepare_or_charge_attempt(self):
        self.r.discover();job=self.r.status()['jobs'][0]
        self.r.db.execute('UPDATE meta SET calls=1')
        before=self.r.status()
        with self.assertRaises(RunnerError):self.r.prepare(job)
        after=self.r.status()
        self.assertEqual(after['jobs'][0]['attempts'],0)
        self.assertIsNone(after['jobs'][0]['slot'])
        self.assertEqual(after['calls'],before['calls'])
        self.assertEqual(after['events'],before['events'])

    def test_strict_finding_rejects_setup_failure_and_wrong_source(self):
        check=dict(self.config['checks'][0],finding_contract='strict-v1')
        baseline='a'*64
        good={'id':check['id'],'exactsourceSHA':baseline,'source_stable':True,
              'passed':False,'classification':'product_defect','evidence':[]}
        self.assertTrue(self.r.finding_valid(check,{'exit_code':1},json.dumps(good),baseline))
        for exit_code,payload in [(2,good),(1,'traceback setup failed'),
                                  (1,dict(good,exactsourceSHA='b'*64)),
                                  (1,dict(good,source_stable=False)),
                                  (1,dict(good,classification='infrastructure_error'))]:
            self.assertFalse(self.r.finding_valid(check,{'exit_code':exit_code},json.dumps(payload),baseline))
        self.assertFalse(self.r.finding_valid(self.config['checks'][0],{'exit_code':1},'Traceback setup failed',baseline))

    def test_discovery_dedupe_and_trusted_identity(self):
        self.r.discover();self.r.discover()
        jobs=self.r.status()['jobs'];self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['check_id'],'real-local-check')
        self.check.write_text('raise SystemExit(0)\n')
        with self.assertRaisesRegex(RunnerError,'trusted checks changed'):self.r.discover()

    def test_bounded_output_and_timeout(self):
        result,output=self.r.execute(None,'fixture:binary',[sys.executable,'-c','import os; os.write(1,b"x"*50000)'],self.repo)
        self.assertEqual(result['exit_code'],0)
        self.assertEqual(result['output_bytes'],50000)
        self.assertEqual(result['saved_bytes'],1024)
        self.assertTrue(result['truncated']);self.assertEqual(len(output),1024)
        result,_=self.r.execute(None,'fixture:timeout',[sys.executable,'-c','import time; print("start",flush=True);time.sleep(5)'],self.repo)
        self.assertEqual(result['exit_code'],124);self.assertLess(result['duration'],3)

    def test_guardian_parent_eof_stops_descendant(self):
        marker=self.root/'late'
        child='import time;from pathlib import Path;time.sleep(.8);Path('+repr(str(marker))+').write_text("alive")'
        code='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr(child)+']);print("started",flush=True);time.sleep(10)'
        spec={'argv':[sys.executable,'-c',code],'cwd':str(self.repo),'seconds':3,'bytes':1024,'output':str(self.root/'guardian.log'),'result':str(self.root/'guardian.json')}
        path=self.root/'spec.json';path.write_text(json.dumps(spec))
        guardian=self.root/'state/runtime/agent_workflow/guardian.py'
        p=subprocess.Popen([sys.executable,str(guardian),str(path)],stdin=subprocess.PIPE)
        try:
            deadline=time.time()+2
            while (not (self.root/'guardian.log').exists() or (self.root/'guardian.log').stat().st_size==0) and time.time()<deadline:time.sleep(.01)
            p.stdin.close();p.wait(timeout=3);time.sleep(.9)
            result=json.loads((self.root/'guardian.json').read_text());self.assertEqual(result['reason'],'parent_eof');self.assertFalse(marker.exists())
        finally:
            if p.poll() is None:p.kill();p.wait()

    def test_integration_intent_recovers_git_db_gap(self):
        from agent_workflow.runner import git
        old=git(self.repo,'rev-parse','HEAD')
        (self.repo/'core.py').write_text('candidate\n');git(self.repo,'add','.');git(self.repo,'-c','user.name=Test','-c','user.email=test@invalid','commit','-m','candidate');new=git(self.repo,'rev-parse','HEAD')
        self.r.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,1,NULL,NULL,NULL,NULL,?,NULL)',('job-gap','check','baseline',old,'integrating',new))
        self.r.db.execute('INSERT INTO integration VALUES(?,?,?,?)',('job-gap',old,new,'intent'))
        # A controlled Git/DB integration gap fixture, not a real backend call.
        git(self.repo,'checkout',old,'--','core.py')
        self.r.recover()
        self.assertEqual((self.repo/'core.py').read_text(),'candidate\n')
        self.assertEqual(self.r.status()['jobs'][0]['phase'],'complete')
        self.assertIn('integration_recovered',[e['kind'] for e in self.r.status()['events']])

    def test_worker_protocol_is_compilable_and_protected(self):
        compile(JOBCTL,'jobctl.py','exec')
        bad=dict(self.config,allowlist=['tests/*'])
        with self.assertRaises(RunnerError):validate_config(bad)

    def test_backend_log_replay_ignores_non_object_json_records(self):
        # Actual Codex output included a standalone JSON-valid quoted source line.
        # Replay locally: this test makes no backend/model call and spends no quota.
        from unittest.mock import patch
        lines = [json.dumps({'type':'thread.started','thread_id':'replayed-thread'}),
                 '"import os; os.write(1,b\'binary\'); "',
                 'null', '42', 'true', '[]', '1.5', 'ordinary non-JSON output',
                 json.dumps({'type':'turn.completed','usage':{'input_tokens':11,'output_tokens':5}})]
        output='\n'.join(lines)
        result={'exit_code':0,'truncated':False}
        with patch.object(self.r,'execute',return_value=(result,output)) as execute:
            self.assertEqual(self.r.backend({'id':'fixture','slot':str(self.root)},'worker',self.repo,'Offline replay'),(result,output))
        self.assertEqual(execute.call_count,1)
        identity=json.loads(self.r.status()['events'][-1]['data'])
        self.assertEqual(identity['thread_ids'],['replayed-thread'])
        self.assertEqual(identity['usage'],[{'input_tokens':11,'output_tokens':5}])
        self.assertIsNone(identity['cost'])
        self.assertEqual(self.r.status()['meta']['calls'],0)

    def test_retry_feedback_is_bounded_typed_and_token_redacted(self):
        self.r.event('fixture','review_verdict',{'reason':'real reviewer rejection'})
        logs=self.root/'state/logs';logs.mkdir()
        path=logs/'fixture.log';path.write_text('x'*20000+'\nreal failure tail\n{"token":"PRIVATE"}\n--args private-command\nhandle.private.json')
        self.r.db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?)',('fixture-call','fixture','machine-test','finished',0,json.dumps({'output':str(path),'exit_code':7})))
        feedback=self.r.retry_feedback({'id':'fixture','error':'real attempt error '+'z'*20000})
        self.assertLessEqual(len(feedback.encode()),16384)
        self.assertLessEqual(len(json.dumps(feedback,ensure_ascii=False).encode()),16384)
        self.assertIn('real reviewer rejection',feedback)
        self.assertIn('real failure tail',feedback)
        self.assertNotIn('PRIVATE',feedback)
        self.assertNotIn('--args',feedback)
        self.assertNotIn('handle.private',feedback)
        self.r.event('fixture','review_verdict',[])
        self.assertIsInstance(self.r.retry_feedback({'id':'fixture','error':42}),str)
        self.r.event('fixture','attempt_failed',{'stage':'reviewer','attempt':2})
        self.r.event('fixture','review_verdict',{'approved':False,'candidate':'d'*64,'reason':'preserve exit127'})
        structured=json.loads(self.r.retry_feedback({'id':'fixture','error':'review rejected'}))
        self.assertEqual(structured['review']['candidate'],'d'*64)
        self.assertEqual(structured['failure']['stage'],'reviewer')
        log=structured['machine_logs'][0]
        self.assertEqual(log['exit_code'],7)
        self.assertEqual(log['log'],str(path.resolve()))
        import hashlib
        self.assertEqual(log['saved_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())

    def test_retry_feedback_redacts_nested_and_fragment_token_values(self):
        marker='FIXTURE_LEASE_SENTINEL'
        logs=self.root/'state/logs';logs.mkdir()
        path=logs/'nested.log'
        event={'type':'item.completed','item':{'aggregated_output':json.dumps({'token':marker,'nested':{'token':marker}})}}
        fragment=json.dumps(event)[:-4]  # Truncated outer JSON, still contains escaped token fields.
        # A long quoted value would lose its token key if raw-tail clipping came first.
        long_value={'token':'x'*6000+marker}
        path.write_text(json.dumps(event)+'\n'+fragment+'\n'+json.dumps(long_value))
        self.r.db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?)',('nested-call','fixture','backend:worker','finished',0,json.dumps({'output':str(path),'exit_code':1,'truncated':True})))
        feedback=self.r.retry_feedback({'id':'fixture','error':'offline native probe'})
        self.assertNotIn(marker,feedback)
        self.assertIn('REDACTED',feedback)
        import hashlib
        log=json.loads(feedback)['machine_logs'][0]
        self.assertEqual(log['saved_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(log['tail_scope'],'tail of retained prefix')

    def test_qualified_source_restore_requires_fresh_baseline_and_evidence(self):
        # Real local CLI subprocess fixture; no Codex/model call.
        import shutil
        from agent_workflow import core
        from agent_workflow.runner import git
        shutil.copytree(Path(core.__file__).parent,self.repo/'agent_workflow',ignore=shutil.ignore_patterns('__pycache__'))
        git(self.repo,'add','.');git(self.repo,'-c','user.name=Test','-c','user.email=test@invalid','commit','-m','reuse fixture')
        self.r.discover();first=self.r.prepare(self.r.status()['jobs'][0]);slot=Path(first['slot'])
        def cli(attempt,action,args=None,ok=True):
            directory=Path(attempt['slot'])
            p=subprocess.run([sys.executable,str(directory/'jobctl.py'),action]+([json.dumps(args)] if args else []),cwd=directory/'candidate',capture_output=True,text=True)
            self.assertEqual(p.returncode==0,ok,p.stdout+p.stderr)
            return json.loads(p.stdout) if p.returncode==0 else None
        cli(first,'claim');cli(first,'progress',{'note':'fixture implementation'})
        cli(first,'baseline')
        helper=slot/'candidate/agent_workflow/core_reuse_fixture.py';helper.write_text('answer = 41\n')
        cli(first,'change');body=self.r.retain_reusable(first)
        self.r.event(first['id'],'review_verdict',{'approved':False,'candidate':body['candidate'],'reason':'fixture revision requested'})
        git(slot/'candidate','reset','--hard',first['base_head']);git(slot/'candidate','clean','-fd')
        self.r.db.execute("UPDATE jobs SET phase='queued' WHERE id=?",(first['id'],))
        second=self.r.prepare(dict(self.r.db.execute('SELECT * FROM jobs WHERE id=?',(first['id'],)).fetchone()))
        second_slot=Path(second['slot'])
        cli(second,'claim');cli(second,'restore',ok=False)
        fresh=json.loads((second_slot/'handle.private.json').read_text())
        old=json.loads((slot/'handle.private.json').read_text())
        self.assertNotEqual(fresh['id'],old['id']);self.assertNotEqual(fresh['token'],old['token'])
        cli(second,'progress',{'note':'fresh baseline before source restore'});cli(second,'baseline')
        restored=cli(second,'restore')
        self.assertEqual(restored['restored_candidate'],body['candidate'])
        self.assertEqual((second_slot/'candidate/agent_workflow/core_reuse_fixture.py').read_text(),'answer = 41\n')
        cli(second,'change')
        import sqlite3
        db=sqlite3.connect(second_slot/'core-state/tasks.sqlite')
        state,evidence=db.execute('SELECT state,evidence FROM runs WHERE id=?',(fresh['id'],)).fetchone();db.close()
        self.assertEqual(state,'changed');self.assertIsNone(evidence)
        # Source reuse carries no prior acceptance, verification or review state.
        self.assertNotIn('evidence',json.loads((second_slot/'job.json').read_text())['reuse'])
        # Expired ownership is rejected before applying even an otherwise qualified patch.
        git(second_slot/'candidate','reset','--hard',second['base_head']);git(second_slot/'candidate','clean','-fd')
        from agent_workflow.core import digest
        unchanged=digest(second_slot/'candidate')
        db=sqlite3.connect(second_slot/'core-state/tasks.sqlite')
        db.execute('UPDATE runs SET lease_until=? WHERE id=?',(time.time()-1,fresh['id']));db.commit();db.close()
        cli(second,'restore',ok=False)
        self.assertEqual(digest(second_slot/'candidate'),unchanged)
        self.assertFalse((second_slot/'candidate/agent_workflow/core_reuse_fixture.py').exists())
        # A changed patch or protected candidate/control change is never eligible.
        patch=Path(body['path']);patch.chmod(0o600);patch.write_bytes(patch.read_bytes()+b'corrupt')
        metadata=json.loads((second_slot/'policy.json').read_text())
        self.assertIsNone(self.r.select_reuse(second,second_slot,metadata))
        protected=second_slot/'candidate/tests';protected.mkdir();(protected/'weaken.py').write_text('pass\n')
        with self.assertRaisesRegex(RunnerError,'outside allowlist'):self.r.retain_reusable(second)
        shutil.rmtree(protected)
        control=second_slot/'policy.json';original=control.read_text();control.write_text('{}')
        with self.assertRaisesRegex(RunnerError,'frozen'):self.r.retain_reusable(second)
        control.write_text(original)
        (self.repo/'core.py').write_text('changed base\n')
        with self.assertRaisesRegex(RunnerError,'stale base'):self.r.retain_reusable(second)

    def test_stale_queued_finding_rechecks_before_any_ai_call(self):
        from agent_workflow.runner import git
        self.r.discover();old=self.r.status()['jobs'][0]
        (self.repo/'core.py').write_text('new reviewed baseline\n')
        git(self.repo,'add','.');git(self.repo,'-c','user.name=Test','-c','user.email=test@invalid','commit','-m','new base')
        self.assertIsNone(self.r.prepare(old))
        self.r.discover()
        jobs=self.r.status()['jobs']
        self.assertEqual([j['phase'] for j in jobs],['superseded','queued'])
        self.assertNotEqual(jobs[0]['baseline'],jobs[1]['baseline'])
        self.assertEqual(self.r.status()['meta']['calls'],0)

    def test_prepared_clone_real_cli_baseline_protocol(self):
        # Local protocol fixture only: no backend/model invocation.
        import shutil
        from agent_workflow import core
        from agent_workflow.runner import git
        shutil.copytree(Path(core.__file__).parent,self.repo/'agent_workflow',ignore=shutil.ignore_patterns('__pycache__'))
        git(self.repo,'add','.');git(self.repo,'-c','user.name=Test','-c','user.email=test@invalid','commit','-m','workflow fixture')
        self.r.discover()
        job=self.r.prepare(self.r.status()['jobs'][0]);slot=Path(job['slot'])
        self.assertFalse((slot/'candidate/.git/commondir').exists())
        def worker_cli(action,args=None):
            p=subprocess.run([sys.executable,str(slot/'jobctl.py'),action]+([json.dumps(args)] if args else []),cwd=slot/'candidate',capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            return json.loads(p.stdout)
        worker_cli('claim');worker_cli('progress',{'note':'local fixture baseline'})
        failure=worker_cli('baseline')
        self.assertFalse(failure['result']['passed'])
        self.assertEqual(failure['result']['evidence']['candidate'],job['baseline'])
        helper=slot/'candidate/agent_workflow/core_fixture.py';helper.write_text('fixture = True\n')
        changed=worker_cli('change')
        self.assertEqual(changed['result']['candidate'],self.r.enforce(job))
        controller=slot/'controller/agent_workflow/core.py'
        controller.write_text(controller.read_text()+'\n# altered controller\n')
        frozen=json.loads((slot/'frozen.json').read_text())
        from agent_workflow.core import digest
        frozen['controller']=digest(slot/'controller')
        (slot/'frozen.json').write_text(json.dumps(frozen))
        with self.assertRaisesRegex(RunnerError,'worker changed frozen'):
            self.r.enforce(job)
