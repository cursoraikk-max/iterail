"""Exact owner-derived UIUX metadata bridge; no backend dispatch."""
import copy,hashlib,importlib.util,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'trusted'))
import common
from common import run,fixture,require,rejects,close

def bridge(m):
 tmp,root,c=fixture(m,wall=600)
 try:
  root=root.resolve();art=root/'art';art.mkdir();p={'schema_version':1,'project':{'id':c['project']['id'],'has_ui':False},'artifact_root':str(art),'standards':{'users':[],'flows':[],'references':[],'tokens':{},'viewports':[],'a11y':[]},'cadence':{'interval_seconds':60},'limits':{'artifact_bytes':4096,'evidence_items':8},'rubric':[],'approved_brand_changes':[]};path=root/'owner-uiux.json';path.write_text(json.dumps(p));desc={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};c['project']['uiux']=desc
  for bad in ('unbound','hash','symlink','inside'):
   invalid=copy.deepcopy(c)
   if bad=='unbound':invalid['project']['uiux']={'path':str(path)}
   elif bad=='hash':invalid['project']['uiux']['sha256']='0'*64
   else:
    target=(Path(c['repo']) if bad=='inside' else root)/(bad+'.json')
    if bad=='symlink':target.symlink_to(path)
    else:target.write_bytes(path.read_bytes())
    invalid['project']['uiux']['path']=str(target)
   rejects(lambda:m.validate_config(invalid),'invalid owner UIUX descriptor accepted: '+bad)
   if bad=='inside':target.unlink()
  s=m.Supervisor(root/'owner',c);release=dict(s.db.execute('SELECT * FROM releases WHERE id=?',(common.meta(s)['active'],)).fetchone());child=m.derive_child_config(s.config);state=s.root/'children'/'native-bridge'/'state';state.parent.mkdir(parents=True);grant,_=s.reserve_step(c['checks'][0]['id'],state,m.canonical(child))
  runner_path=Path(release['path'])/'agent_workflow/runner.py';spec=importlib.util.spec_from_file_location('agent_workflow.native_uiux_bridge',runner_path);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
  rejects(lambda:r.validate_config(child),'standalone Runner gained extended owner permission')
  descriptor={'governor_state':str(s.root),'grant_id':grant,'release':release['id'],'active_fingerprint':release['fingerprint']};b=r._OwnerBridge(descriptor,state,child)
  require(b.derived(b.owner)==child and child['project']['uiux']==desc,'owner metadata derivation changed');changed=copy.deepcopy(child);changed['project']['uiux']['sha256']='0'*64;rejects(lambda:b.validate_child(changed),'child UIUX override accepted');row=s.db.execute('SELECT * FROM reservations WHERE id=?',(grant,)).fetchone();require(json.loads(row['result'])['actual_used']==0 and common.meta(s)['calls']==2 and not s.db.execute("SELECT * FROM reservations WHERE kind LIKE 'supervised:%'").fetchall(),'UI metadata launched backend or changed reservation accounting');b.db.close();close(s)
 finally:tmp.cleanup()

run('p5-uiux-owner-bridge','evolution',[('SHA_bound_exact_owner_derivation_metadata_no_dispatch',bridge)])
