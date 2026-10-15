"""Phase7 preregistered audit checks. Native owned fixtures; no network/provider calls."""
import copy, hashlib, json, struct, sys, tempfile, traceback, zlib
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'phase5/trusted'))
import common
SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT/'phase4/candidate'
common.REPO = SOURCE
sys.path.insert(0, str(SOURCE))
from agent_workflow import uiux, uiux_maintenance, evolution

def policy(root, ident='audit-ui'):
    art=root/'art';art.mkdir()
    return {'schema_version':1,'project':{'id':ident,'has_ui':True},'artifact_root':str(art),'standards':{'users':['reader'],'flows':[{'id':'home','steps':['Open home']}],'references':[],'tokens':{},'viewports':[{'id':'small','width':32,'height':24}],'a11y':['keyboard']},'cadence':{'interval_seconds':60},'limits':{'artifact_bytes':4096,'evidence_items':8},'rubric':[{'id':'flow','minimum_score':3}],'approved_brand_changes':[]}
def chunk(kind, data):
    return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
def png(idat):
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',32,24,8,2,0,0,0))+(chunk(b'IDAT',idat) if idat is not None else b'')+chunk(b'IEND',b'')
def packet(path, ident, run):
    return {'id':ident,'kind':'submitted','source_sha256':run['source_sha256'],'actor':'author','artifact':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'viewport_id':'small','flow_id':'home'}}

def unusable_png():
    observations=[]
    for label, data in [('missing_IDAT',png(None)),('invalid_deflate',png(b'not a zlib stream'))]:
        with tempfile.TemporaryDirectory(prefix='p7-screenshot-',dir=ROOT/'phase7/audit') as raw:
            root=Path(raw).resolve();src=root/'src';src.mkdir();(src/'index.html').write_text('<button>Home</button>');p=policy(root);emitted=[]
            store=uiux.UIUXStore(root/'state',p,clock=lambda:1000,task_sink=emitted.append)
            try:
                run=store.discover(src);image=root/'art/small.png';image.write_bytes(data)
                rejected=False
                try:store.record(run['run_id'],'screenshot',packet(image,'bad-screen',run))
                except ValueError:rejected=True
                obs={'variant':label,'byte_count':len(data),'sha256':hashlib.sha256(data).hexdigest(),'rejected':rejected}
                if not rejected:
                    interaction=root/'art/interaction.json';interaction.write_bytes(uiux.encoded({'flow_id':'home','steps':[{'step':'Open home','selector':'button','outcome':'passed'}]}))
                    store.record(run['run_id'],'interaction',packet(interaction,'interaction',run))
                    review=store.review(run['run_id'],'independent-reviewer',[{'id':'flow','score':3,'reason':'Submitted file was counted as screenshot coverage.','evidence_ids':['bad-screen','interaction']}],'needs_improvement','Investigate submitted evidence.',[{'id':'gap','title':'Investigate UI gap','description':'Native invalid screenshot fixture.','evidence_ids':['bad-screen'],'major_brand_change':False,'brand_change_id':None}])
                    obs.update(review_phase=review['phase'],exported_tasks=len(emitted),production_visual_verified=review['production_visual_verified'])
                else:
                    common.require(store.db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]==0,'invalid screenshot wrote evidence')
                    valid=root/'art/valid.png';valid.write_bytes(png(zlib.compress((b'\x00'+b'\x20\x40\x60'*32)*24)))
                    store.record(run['run_id'],'screenshot',packet(valid,'valid-screen',run));obs['valid_control_admitted']=True
                observations.append(obs)
            finally:store.db.close()
    return observations, all(x['rejected'] and x.get('valid_control_admitted') for x in observations)

def store_integrity_restart():
    tmp,raw,c=common.fixture(evolution,wall=600)
    try:
        root=raw.resolve();c['repo']=str(Path(c['repo']).resolve());p=policy(root,c['project']['id']);path=root/'owner-uiux.json';path.write_bytes(uiux.encoded(p));c['project']['uiux']={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        clock=[1000.0];Base=uiux_maintenance.UIUXMaintenance
        class Clocked(Base):
            def __init__(self,*args,**kwargs):super().__init__(*args,clock=lambda:clock[0],**kwargs)
        with patch.object(uiux_maintenance,'UIUXMaintenance',Clocked):
            owner=evolution.Supervisor(root/'owner',c)
            try:
                first=owner.maintenance_tick();common.require(first['phase']=='due','setup did not import due audit')
                scheduler=Base(root/'owner/uiux-core',p,c['repo'],clock=lambda:clock[0]);before=scheduler.status()
                identity=root/'owner/uiux-core/uiux/identity.json';original=identity.read_bytes();identity.write_bytes(uiux.encoded({'schema_version':1,'policy_sha256':'0'*64}))
                initial_meta=copy.deepcopy(owner.status()['meta']);owner.db.close();owner=None
                clock[0]=1061.0;owner=evolution.Supervisor(root/'owner');result=owner.maintenance_tick();after=scheduler.status()
                tasks=owner.root/'uiux-core/tasks.sqlite';import sqlite3
                db=sqlite3.connect('file:'+str(tasks)+'?mode=ro',uri=True);count=db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0];db.close()
                after_meta=owner.status()['meta'];obs={'first_phase':first['phase'],'after_restart':result,'scheduler_before':before,'scheduler_after':after,'queued_tasks':count,'calls_unchanged':initial_meta['calls']==after_meta['calls'],'deadline_unchanged':initial_meta['deadline']==after_meta['deadline'],'corruption':'owned UI Store identity SHA mismatch','identity_before_sha256':hashlib.sha256(original).hexdigest(),'identity_after_sha256':hashlib.sha256(identity.read_bytes()).hexdigest()}
                ok=result['phase']=='blocked' and result.get('failure_class')=='integrity' and before['next_due']==after['next_due'] and before['failure_count']==after['failure_count'] and count==1 and obs['calls_unchanged'] and obs['deadline_unchanged']
                return obs,ok
            finally:
                if owner is not None:owner.db.close()
    finally:tmp.cleanup()
