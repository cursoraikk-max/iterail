"""Foreign standalone Store cannot cause Core to read outside its source."""
import copy,json,hashlib,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'trusted'))
from common import run,command,require,rejects

def scope(m):
 from agent_workflow import uiux
 with tempfile.TemporaryDirectory(prefix='uiux-scope-') as td:
  root=Path(td).resolve();repo=root/'repo';repo.mkdir();(repo/'index.html').write_text('<button>Owner</button>');command(['git','init','-q',str(repo)]);foreign=root/'foreign';foreign.mkdir();(foreign/'private.txt').write_text('native fixture only');art=root/'art';art.mkdir()
  p={'schema_version':1,'project':{'id':'scope-fixture','has_ui':True},'artifact_root':str(art),'standards':{'users':['reader'],'flows':[{'id':'home','steps':['Open home']}],'references':[],'tokens':{},'viewports':[{'id':'small','width':32,'height':24}],'a11y':['keyboard']},'cadence':{'interval_seconds':60},'limits':{'artifact_bytes':4096,'evidence_items':8},'rubric':[{'id':'flow','minimum_score':3}],'approved_brand_changes':[]};path=root/'owner.json';path.write_text(json.dumps(p));c={'repo':str(repo),'tests':[[sys.executable,'-c','pass']],'sources':['local'],'budget':{'lease_seconds':20,'run_seconds':60,'test_seconds':20,'max_attempts':2},'acceptance':{'require_tests':True,'require_unchanged_candidate':True},'permissions':{'execute_tests':True,'publish':False,'network':False},'uiux_policy':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}
  e=m.Engine(root/'state',c);s=uiux.UIUXStore(e.state/'uiux',p);s.discover(foreign);s.db.close();observed=[];original=uiux.source_digest
  def spy(source):observed.append(str(Path(source).resolve()));return original(source)
  with patch.object(uiux,'source_digest',side_effect=spy):rejects(e.uiux_query,'Core accepted foreign source run')
  require(str(foreign) not in observed,'Core read foreign source before rejecting Store scope');require(not e.db.execute('SELECT * FROM tasks').fetchall(),'foreign run imported task');e.db.close()

run('p5-uiux-source-scope','core',[('foreign_Store_metadata_rejected_before_source_read',scope)])
