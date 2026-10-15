#!/usr/bin/env python3
"""Native51 durable bridge contract; caller-side execution is real Core fixture only."""
import argparse,copy,hashlib,importlib.util,json,shutil,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]

def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);a=p.parse_args();repo=a.repo.resolve();sys.path.insert(0,str(repo));checks=[]
 try:
  from agent_workflow.hostbridge import HostBridge
  from agent_workflow.mcp import FIELDS
  assert len(FIELDS)==27
  spec=importlib.util.spec_from_file_location('bridge_native_fixture',ROOT/'research-trial/trusted/continuation_quality_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with tempfile.TemporaryDirectory(prefix='host-bridge-') as d:
   c=m.Cases(repo,Path(d).resolve())
   def setup(label,two=False,slots=8):
    f=c.fixture(label,slots);proof=c.q.baseline(f);tid=c.q.task(f,'first-work');candidates=[c.q.candidate(tid)];f.update(tid=tid,proof=proof)
    if two:f['tid2']=c.q.task(f,'second-work');candidates.append(c.q.candidate(f['tid2'],finding='b'*64))
    c.q.assessment(f,proof,candidates);b=HostBridge(f['c']);f['bridge']=b;return f
   def reopen(f):c.reopen(f);f['bridge']=HostBridge(f['c']);return f['bridge']
   def deny(fn):
    try:fn()
    except (ValueError,TypeError):return
    raise AssertionError('unsafe duplicate or invalid operation admitted')
   def resources(f,base,spent=None):
    s=f['bridge'].status();assert s['started']==base['started'] and s['deadline']==base['deadline'];assert s['requests_spent']==(base['requests_spent'] if spent is None else spent);return s
   def entry(f,rid):return next(x for x in f['bridge'].status()['entries'] if x['request_id']==rid)
   def begin(f):
    b=f['bridge'];x=b.reserve();assert x['kind']=='worker' and x['task_id']==f['tid'];assert x['phase']=='reserved' and x['dispatch_started'] is False;assert x==b.reserve();base=b.status();x=b.begin(x['request_id']);assert x['phase']=='executing' and x['dispatch_started'] is True and x['packet']['id']==x['request_id'];key=hashlib.sha256(json.dumps({k:v for k,v in x['packet'].items() if k!='run_id'},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest();assert x['dispatch_key']==key and x['actor']=='host-'+key[:32];deny(lambda:b.begin(x['request_id']));return x,base
   def claim(f,ticket):
    v=f['c'].status()['active_version'];run=f['c']._core('claim',{'worker':ticket['actor'],'task_id':ticket['task_id']},v);f['run']=run;f['args']=c.q.owned(run);f['v']=v;return run
   def ready(f,change=False):
    if change:
     (f['repo']/'bridge-change.txt').write_text('actual source change\n');c.git(f['repo'],'add','.');c.git(f['repo'],'-c','user.name=Native contract','-c','user.email=native@example.invalid','commit','-qm','native bridge candidate')
    f['c']._core('change',f['args'],f['v']);assert f['c']._core('verify',f['args'],f['v'])['passed'];f['c']._core('transition',{**f['args'],'target':'ready_review'},f['v'])
   def review_ticket(f,worker):
    x=f['bridge'].reconcile(worker['request_id']);assert x['run_id']==f['run']['id'];x=f['bridge'].reserve();assert x['kind']=='review' and x['run_id']==f['run']['id'];ticket=f['bridge'].begin(x['request_id']);assert ticket['kind']=='review';return ticket
   def receipt(f,approved=True,reviewer=None):
    value={'schema_version':1,'approved':approved,'reviewer':reviewer or m.helper.REVIEWER,'run_id':f['run']['id'],'core_state':f['p']['core_state'],'source_root':f['p']['repo'],'source_sha256':c.q.digest(f['repo']),'controller_package_sha256':f['run']['workflow']};path=f['root']/('receipt-'+str(approved)+'.json');m.helper.write_json(path,value);return path,m.helper.sha(path.read_bytes())
   def accept(f):c.q.assessment(f,f['run']['id']);f['c']._core('transition',{**f['args'],'target':'accepted'},f['v'])
   def successor(f,name):p=f['root']/'runtime'/name;p.mkdir();shutil.copytree(f['repo']/'agent_workflow',p/'agent_workflow');return p
   # Real Core claim succeeded but host callback/result is deliberately absent.
   f=setup('lost-host-result',two=True);ticket,base=begin(f);claim(f,ticket);ready(f,change=True);reopen(f);assert entry(f,ticket['request_id'])['phase']=='executing';deny(lambda:f['bridge'].begin(ticket['request_id']));x=f['bridge'].reconcile(ticket['request_id']);assert x['run_id']==f['run']['id'];assert f['c'].status()['request']['run_id']==f['run']['id'];resources(f,base);assert f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]==2;checks.append('real changed verified Core outcome reconciles exact actor after discarded host reply and restart without redispatch')
   review=review_ticket(f,ticket);accept(f);rp,rh=receipt(f);succ=successor(f,'v2');old_version=f['v']['id']
   # Inject only lost outer receipt after actual existing promotion returns.
   actual=f['c'].promote;lost_calls=[]
   def lost(*args,**kw):
    lost_calls.append(1);actual(*args,**kw);raise ValueError('controlled lost promotion reply')
   f['c'].promote=lost
   try:f['bridge'].finish(review['request_id'],str(rp),rh,str(succ),old_version)
   except ValueError:pass
   assert lost_calls==[1],'lost-response fault did not invoke actual promotion';assert f['c'].status()['request'] is None;reopen(f);f['bridge'].reconcile(review['request_id']);assert entry(f,review['request_id'])['phase']=='completed';resources(f,base);assert sum(x['kind']=='version_promoted' for x in f['c'].db.execute('SELECT kind FROM events'))==1;deny(lambda:f['bridge'].begin(review['request_id']));checks.append('actual independent acceptance and promotion reconcile lost reply from durable event exactly once')
   # Same original cycle, actual second admitted task, simulated due clock only.
   c.q.assessment(f,f['run']['id'],[c.q.candidate(f['tid2'],finding='b'*64)]);f['clock'][0]+=6;f['tid']=f['tid2'];ticket2,base2=begin(f);assert ticket2['actor']!=ticket['actor'];assert base2['started']==base['started'] and base2['deadline']==base['deadline'] and base2['requests_spent']==4;claim(f,ticket2);ready(f);review2=review_ticket(f,ticket2);accept(f);rp,rh=receipt(f);succ2=successor(f,'v3');out=f['bridge'].finish(review2['request_id'],str(rp),rh,str(succ2),f['v']['id']);assert entry(f,review2['request_id'])['phase']=='completed';resources(f,base,4);assert sum(x['kind']=='version_promoted' for x in f['c'].db.execute('SELECT kind FROM events'))==2;checks.append('second real Core task review and promotion share original deadline with cumulative four slots')
   raw=json.dumps(f['bridge'].status());assert f['run']['token'] not in raw and m.helper.SECRET not in raw and m.helper.REVIEWER not in raw;checks.append('public bridge status contains no claim credentials task body or privileged review identity')
   f=setup('manual-adoption-no-begin');ticket=f['bridge'].reserve();base=f['bridge'].status();assert ticket['dispatch_started'] is False;claim(f,ticket);x=f['bridge'].reconcile(ticket['request_id']);assert x['run_id']==f['run']['id'] and x['dispatch_started'] is False;reopen(f);assert entry(f,ticket['request_id'])['dispatch_started'] is False;deny(lambda:f['bridge'].begin(ticket['request_id']));resources(f,base);checks.append('manual bootstrap work reconciles reserved intent without fabricating a begin or platform dispatch')
   f=setup('out-of-band-before-begin');ticket=f['bridge'].reserve();base=f['bridge'].status();claim(f,ticket);deny(lambda:f['bridge'].begin(ticket['request_id']));x=f['bridge'].reconcile(ticket['request_id']);assert x['run_id']==f['run']['id'];deny(lambda:f['bridge'].begin(ticket['request_id']));resources(f,base);assert f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]==2;checks.append('actual preexisting Core claim before begin blocks another dispatch and is explicitly reconciled')
   f=setup('unknown-outcome');ticket,base=begin(f);reopen(f);x=f['bridge'].reconcile(ticket['request_id']);assert x['phase']=='uncertain';deny(lambda:f['bridge'].begin(ticket['request_id']));resources(f,base);assert f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]==1;checks.append('no Core evidence remains durable uncertain and never authorizes redispatch')
   f=setup('ambiguous-outcome');ticket,base=begin(f);run1=claim(f,ticket);f['c']._core('fail',{**f['args'],'reason':'native first actual failed attempt'},f['v']);run2=claim(f,ticket);assert run1['id']!=run2['id'];x=f['bridge'].reconcile(ticket['request_id']);assert x['phase']=='uncertain';deny(lambda:f['bridge'].begin(ticket['request_id']));resources(f,base);checks.append('two actual same-actor Core attempts are ambiguous; no newest-row guessing')
   f=setup('independent-reject');ticket,base=begin(f);claim(f,ticket);ready(f);review=review_ticket(f,ticket);rp,rh=receipt(f,False);succ=successor(f,'rejected-candidate')
   try:f['bridge'].finish(review['request_id'],str(rp),rh,str(succ),f['v']['id'])
   except ValueError:pass
   rejected=entry(f,review['request_id']);assert rejected['phase']=='rejected' and rejected['receipt_sha256']==rh;assert f['c']._core('status',{'run_id':f['run']['id']},f['v'])['state']=='ready_review';assert f['c'].status()['active_version']['id']==f['v']['id'];reopen(f);assert entry(f,review['request_id'])['phase']=='rejected';deny(lambda:f['bridge'].begin(review['request_id']));resources(f,base);checks.append('independent false receipt durably rejects without accepting promoting or dispatching again')
   f=setup('wrong-self-receipt');ticket,base=begin(f);claim(f,ticket);ready(f);review=review_ticket(f,ticket);rp,rh=receipt(f,False,reviewer=f['run']['worker']);succ=successor(f,'wrong-self');deny(lambda:f['bridge'].finish(review['request_id'],str(rp),rh,str(succ),f['v']['id']));assert entry(f,review['request_id'])['phase']!='rejected';assert f['c']._core('status',{'run_id':f['run']['id']},f['v'])['state']=='ready_review';resources(f,base)
   rp,rh=receipt(f,False,reviewer='untrusted-reviewer');deny(lambda:f['bridge'].finish(review['request_id'],str(rp),rh,str(succ),f['v']['id']));assert entry(f,review['request_id'])['phase']!='rejected'
   rp,rh=receipt(f,False);deny(lambda:f['bridge'].finish(review['request_id'],str(rp),'0'*64,str(succ),f['v']['id']));assert entry(f,review['request_id'])['phase']!='rejected'
   for field,value in [('source_sha256','0'*64),('run_id','run-'+'0'*12),('controller_package_sha256','0'*64)]:
    rp,rh=receipt(f,False);bad=json.loads(rp.read_text());bad[field]=value;m.helper.write_json(rp,bad);badsha=m.helper.sha(rp.read_bytes());deny(lambda:f['bridge'].finish(review['request_id'],str(rp),badsha,str(succ),f['v']['id']));assert entry(f,review['request_id'])['phase']!='rejected'
   resources(f,base);checks.append('self unknown or stale wrong-run workflow hash receipts never become trusted rejection authority')
   f=setup('original-ceiling',two=True,slots=2);ticket,base=begin(f);claim(f,ticket);ready(f);review=review_ticket(f,ticket);accept(f);rp,rh=receipt(f);succ=successor(f,'ceiling-v2');f['bridge'].finish(review['request_id'],str(rp),rh,str(succ),f['v']['id']);c.q.assessment(f,f['run']['id'],[c.q.candidate(f['tid2'],finding='b'*64)]);f['clock'][0]+=6;reopen(f)
   try:f['bridge'].reserve()
   except ValueError:pass
   assert f['c'].status()['request'] is None and f['c'].status()['phase']=='blocked';resources(f,base);assert f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]==2;checks.append('known second task cannot exceed original two-slot ceiling after completed first task and restart')
   f=setup('original-deadline');ticket=f['bridge'].reserve();base=f['bridge'].status();assert ticket['phase']=='reserved' and ticket['dispatch_started'] is False;f['clock'][0]=base['deadline']+1;reopen(f);deny(lambda:f['bridge'].begin(ticket['request_id']));assert f['bridge'].status()['deadline']==base['deadline'] and f['bridge'].status()['requests_spent']==2;checks.append('restart after simulated original deadline never renews dispatch resources')
  print(json.dumps({'passed':True,'checks':checks,'native_local_fixture':True,'controlled_faults':True,'actual_agent_dispatch':False,'model_calls':0,'network_calls':0}));return 0
 except Exception as e:
  print(json.dumps({'passed':False,'checks':checks,'error_type':type(e).__name__,'error':str(e),'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 1
if __name__=='__main__':sys.exit(main())
