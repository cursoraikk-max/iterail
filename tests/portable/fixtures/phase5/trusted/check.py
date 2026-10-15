"""Fixed native UI/UX policy, evidence and inert-task contracts; no provider/browser calls."""
import copy,hashlib,json,struct,tempfile,zlib
from pathlib import Path
from common import run,require,rejects

def fixture(m,has_ui=True,sink=None):
 tmp=tempfile.TemporaryDirectory(prefix='uiux-v1-');root=Path(tmp.name).resolve();src=root/'source';src.mkdir();(src/'index.html').write_text('<button>Open home</button>');art=root/'artifacts';art.mkdir();clock=[1000.0]
 p={'schema_version':1,'project':{'id':'native-ui','has_ui':has_ui},'artifact_root':str(art),'standards':{'users':['reader'],'flows':[{'id':'home','steps':['Open home']}],'references':[{'id':'owner-reference','uri':'https://owner.example/style'}],'tokens':{'brand-color':'#123456'},'viewports':[{'id':'small','width':32,'height':24}],'a11y':['keyboard navigation']},'cadence':{'interval_seconds':60},'limits':{'artifact_bytes':4096,'evidence_items':8},'rubric':[{'id':'flow','minimum_score':3},{'id':'a11y','minimum_score':3}],'approved_brand_changes':[]}
 s=m.UIUXStore(root/'state',p,clock=lambda:clock[0],task_sink=sink);return tmp,root,src,art,clock,p,s

def png(w,h):
 def chunk(k,data):return struct.pack('!I',len(data))+k+data+struct.pack('!I',zlib.crc32(k+data)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(b'\x00'+b'\x12\x34\x56'*w for _ in range(h))))+chunk(b'IEND',b'')
def packet(path,ident,source,kind='fixture'):return {'id':ident,'kind':kind,'source_sha256':source,'actor':'native-evidence-author','artifact':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'viewport_id':'small','flow_id':'home'}}
def rubric():return [{'id':'flow','score':1,'reason':'Home action has incomplete feedback in interaction evidence','evidence_ids':['screen','interaction']},{'id':'a11y','score':2,'reason':'Keyboard focus needs investigation against the supplied screenshot','evidence_ids':['screen']}]
def finding(ident='minor',major=False):return {'id':ident,'title':'Investigate home feedback','description':'Inspect home action and focus feedback using owner flow and viewport','evidence_ids':['screen','interaction'],'major_brand_change':major,'brand_change_id':'rebrand' if major else None}
def evidence(s,art,r):
 image=art/'screen.png';image.write_bytes(png(32,24));inter=art/'interaction.json';inter.write_text(json.dumps({'flow_id':'home','steps':[{'step':'Open home','selector':'button','outcome':'passed'}]}))
 a=s.record(r['run_id'],'screenshot',packet(image,'screen',r['source_sha256']));b=s.record(r['run_id'],'interaction',packet(inter,'interaction',r['source_sha256'],'submitted'));require(a['production_visual_verified'] is False and b['production_visual_verified'] is False,'fixture/submitted evidence falsely production verified');return image,inter

def na_policy(m):
 emitted=[];tmp,root,src,art,clock,p,s=fixture(m,False,emitted.append)
 try:
  r=s.discover(src);require(r['phase']=='not_applicable' and not emitted,'no UI must be N/A without task/capture');before=s.status()['policy_sha256'];changed=copy.deepcopy(p);changed['standards']['tokens']['brand-color']='#ffffff';rejects(lambda:m.UIUXStore(root/'state',changed),'owner standards changed after initialization');require(s.status()['policy_sha256']==before,'policy mutation changed frozen identity')
 finally:tmp.cleanup()
def scheduling_stale(m):
 emitted=[];tmp,root,src,art,clock,p,s=fixture(m,sink=emitted.append)
 try:
  first=s.discover(src);require(first['phase']=='review_required' and first['production_visual_verified'] is False,'first due review missing');again=s.discover(src);require(again['run_id']==first['run_id'] and again['phase'] in ('unchanged','not_due'),'unchanged pending review duplicated')
  (src/'index.html').write_text('<button>Changed source</button>');fresh=s.discover(src);require(fresh['run_id']!=first['run_id'] and fresh['source_sha256']!=first['source_sha256'],'source change failed to schedule new review')
  evidence(s,art,fresh);s.review(fresh['run_id'],'independent-reviewer',rubric(),'needs_improvement','Investigate submitted evidence, no production capture',[finding()]);count=len(emitted);(src/'index.html').write_text('<button>Stale after review</button>');rejects(lambda:s.review(fresh['run_id'],'independent-reviewer',rubric(),'needs_improvement','Same source-bound investigation',[finding()]),'stale source reviewed without ticker');q=s.query();require(fresh['run_id'] in q['stale_run_ids'] and len(emitted)==count,'stale query exported tasks or failed to mark stale')
 finally:tmp.cleanup()
def bounded_evidence_brand(m):
 emitted=[];tmp,root,src,art,clock,p,s=fixture(m,sink=emitted.append)
 try:
  r=s.discover(src);image=art/'screen.png';image.write_bytes(png(32,24));base=packet(image,'screen',r['source_sha256'])
  for mut in ('hash','source','captured','viewport','outside','symlink','large','dimensions'):
   data=copy.deepcopy(base)
   if mut=='hash':data['artifact']['sha256']='0'*64
   elif mut=='source':data['source_sha256']='0'*64
   elif mut=='captured':data['kind']='captured'
   elif mut=='viewport':data['artifact']['viewport_id']='unapproved'
   else:
    target=(root if mut=='outside' else art)/(mut+'.png')
    if mut=='symlink':target.symlink_to(image)
    else:target.write_bytes(b'x'*4097 if mut=='large' else png(33,24) if mut=='dimensions' else png(32,24))
    data=packet(target,mut,r['source_sha256'])
   rejects(lambda:s.record(r['run_id'],'screenshot',data),'invalid evidence accepted: '+mut)
  evidence(s,art,r);rejects(lambda:s.review(r['run_id'],'native-evidence-author',rubric(),'needs_improvement','Self review',[finding()]),'self review accepted');bad=rubric();bad[0]['reason']='';rejects(lambda:s.review(r['run_id'],'independent-reviewer',bad,'needs_improvement','Missing reasons',[finding()]),'rubric without concrete reasons accepted')
  count=len(emitted);out=s.review(r['run_id'],'independent-reviewer',rubric(),'needs_improvement','Owner brand changes require approval',[finding('major',True)]);require(out['production_visual_verified'] is False and len(emitted)==count,'unapproved major brand became runnable task or falsely verified')
 finally:tmp.cleanup()
def inert_outbox(m):
 imported={};attempts=[];fail=[True]
 def sink(p):
  require(p['commands']==[] and p['permissions']=={} and isinstance(p['body'],str),'UI evidence expanded commands/permissions');attempts.append(p['external_key']);imported.setdefault(p['external_key'],p)
  if fail[0]:fail[0]=False;raise RuntimeError('native callback interruption after idempotent import')
 tmp,root,src,art,clock,p,s=fixture(m)
 try:
  r=s.discover(src);evidence(s,art,r);s.task_sink=sink
  try:s.review(r['run_id'],'independent-reviewer',rubric(),'needs_improvement','Bounded investigation from submitted evidence',[finding()])
  except RuntimeError:pass
  out=s.review(r['run_id'],'independent-reviewer',rubric(),'needs_improvement','Bounded investigation from submitted evidence',[finding()]);s.review(r['run_id'],'independent-reviewer',rubric(),'needs_improvement','Bounded investigation from submitted evidence',[finding()]);require(len(imported)==1 and len(set(attempts))==1,'callback replay duplicated task identity');require(out['production_visual_verified'] is False,'reasoned review falsely production verified')
 finally:tmp.cleanup()
run('p5-uiux','uiux',[('no_UI_NA_and_immutable_owner_standards',na_policy),('due_dedupe_source_change_and_stale_without_tick',scheduling_stale),('bounded_source_evidence_independent_rubric_major_brand',bounded_evidence_brand),('inert_task_outbox_idempotent_recovery',inert_outbox)])
