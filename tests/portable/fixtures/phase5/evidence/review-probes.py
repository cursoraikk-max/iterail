"""Supporting native review probes authored after UIUX implementation; not preimplementation gates."""
import ast,copy,os,sys,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'trusted'))
from common import run,require,rejects
source=Path(__file__).resolve().parents[1]/'trusted/check.py'
tree=ast.parse(source.read_text());tree.body=tree.body[:-1];helpers={};exec(compile(tree,str(source),'exec'),helpers)

def completeness_history(m):
 tmp,root,src,art,clock,p,s=helpers['fixture'](m,sink=lambda _:(_ for _ in ()).throw(AssertionError('incomplete evidence exported')))
 try:
  r=s.discover(src);fifo=art/'special';os.mkfifo(fifo);packet=helpers['packet'] if False else {'id':'fifo','kind':'fixture','source_sha256':r['source_sha256'],'actor':'native-evidence-author','artifact':{'path':str(fifo),'sha256':'0'*64,'viewport_id':'small','flow_id':'home'}};start=time.monotonic();rejects(lambda:s.record(r['run_id'],'screenshot',packet),'FIFO accepted');require(time.monotonic()-start<1,'FIFO artifact blocked')
  diff=art/'difference.json';diff.write_text('{"changed":true}');s.record(r['run_id'],'difference',helpers['packet'](diff,'diff',r['source_sha256']));rubric=helpers['rubric']()
  for x in rubric:x['evidence_ids']=['diff']
  result=s.review(r['run_id'],'independent-reviewer',rubric,'needs_improvement','Difference is signal only',[]);require(result['phase']=='evidence_required' and result['proposals']==[] and result['production_visual_verified'] is False,'difference alone completed review')
  helpers['evidence'](s,art,r);rejects(lambda:s.review(r['run_id'],'independent-reviewer',helpers['rubric'](),'acceptable','Scores below owner thresholds',[]),'owner minimum score ignored');s.review(r['run_id'],'independent-reviewer',helpers['rubric'](),'needs_improvement','Reasoned unverified review',[]);clock[0]+=1;require(s.discover(src)['phase']=='not_due','review cadence ignored');clock[0]+=60;require(s.discover(src)['run_id']!=r['run_id'],'next due review absent')
  marker=root/'state/identity.json';data=marker.read_bytes();marker.unlink();rejects(s.status,'same-process missing owner identity accepted');rejects(lambda:m.UIUXStore(root/'state',p),'restart missing owner identity accepted');marker.write_bytes(data);s.db.close();database=root/'state/uiux.sqlite';database.unlink();rejects(lambda:m.UIUXStore(root/'state',p),'missing initialized database silently rebuilt')
 finally:tmp.cleanup()

def concurrent_bounds(m):
 tmp,root,src,art,clock,p,original=helpers['fixture'](m)
 try:
  original.db.close();p=copy.deepcopy(p);p['limits']['evidence_items']=1;a=m.UIUXStore(root/'bounded',p);b=m.UIUXStore(root/'bounded',p);barrier=threading.Barrier(2);ids=[];errors=[]
  def discover(s):
   try:barrier.wait();ids.append(s.discover(src)['run_id'])
   except Exception as e:errors.append(str(e))
  threads=[threading.Thread(target=discover,args=(s,)) for s in (a,b)]
  for t in threads:t.start()
  for t in threads:t.join(10)
  require(not errors and len(ids)==2 and len(set(ids))==1 and not any(t.is_alive() for t in threads),'concurrent due discovery duplicated/lost run')
  image=art/'screen.png';image.write_bytes(helpers['png'](32,24));sha=a.status()['runs'][0]['source_sha256'];barrier=threading.Barrier(2);success=[];errors=[]
  def record(s,ident):
   try:barrier.wait();s.record(ids[0],'screenshot',helpers['packet'](image,ident,sha));success.append(ident)
   except ValueError as e:errors.append(str(e))
  threads=[threading.Thread(target=record,args=(s,str(i))) for i,s in enumerate((a,b))]
  for t in threads:t.start()
  for t in threads:t.join(10)
  require(len(success)==1 and len(errors)==1 and not any(t.is_alive() for t in threads) and len(a.status()['runs'][0]['evidence'])==1,'concurrent artifact item bound exceeded');a.db.close();b.db.close()
 finally:tmp.cleanup()

run('p5-uiux-supporting-review','uiux',[('difference_missing_threshold_cadence_specialfile_history',completeness_history),('multistore_concurrent_due_dedupe_and_item_bound',concurrent_bounds)])
