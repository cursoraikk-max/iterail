#!/usr/bin/env python3
"""Independent native53. Bytes only, no provider/model/token measurement."""
import argparse,copy,hashlib,importlib.util,json,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
def enc(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def main():
 p=argparse.ArgumentParser();p.add_argument('repo',type=Path,nargs='?');p.add_argument('--repo',dest='repo_alias',type=Path);a=p.parse_args();p.error('choose exactly one repository argument') if (a.repo is None)==(a.repo_alias is None) else None;a.repo=a.repo if a.repo is not None else a.repo_alias;checks=[]
 try:
  spec=importlib.util.spec_from_file_location('existing_quality_fixture',ROOT/'automation-trial/trusted/quality_guard.py');q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
  with tempfile.TemporaryDirectory(prefix='native53-') as tmp:
   c=q.Contract(a.repo,Path(tmp));f=c.fixture('context',False);e=f['e'];repo=f['repo'];skill=repo/'skills/agent-workflow/SKILL.md';skill.parent.mkdir(parents=True);skill.write_text('Complete mandatory instructions: independently verify all changes.\n中文规则。\n');(repo/'guide.md').write_text('Context guide\n'+'中文 relevant detail\n'*100)
   for i in range(20):(repo/('relevant-%02d.txt'%i)).write_text('relevant 中文 '+('detail '*130)+'\n')
   args={'query':'context relevant','mandatory_paths':'[]','byte_budget':'16384','max_file_bytes':'4096'}
   snapshot=lambda:tuple(e.db.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in ['tasks','runs','events']);before=snapshot()
   full=e.context_query(**args)
   assert full.get('not_modified') is False and re.fullmatch('[0-9a-f]{64}',full.get('revision','')), 'context_query lacks opaque conditional revision'
   assert full['authority']=='inert_context_only' and full['schema_version']==1 and {'mandatory','context','used_bytes'}<=set(full)
   def account(packet,budget=16384):assert packet['used_bytes']==len(enc({k:v for k,v in packet.items() if k!='used_bytes'}))<=budget
   account(full);assert any(x['path']=='skills/agent-workflow/SKILL.md' and x['content']==skill.read_text() and x['mandatory'] for x in full['context']['files'])
   revision=full['revision'];conditional=dict(args,if_none_match=revision);compact=e.context_query(**conditional)
   assert set(compact)=={'schema_version','authority','not_modified','revision','source_digest','used_bytes'} and compact['not_modified'] is True and compact['revision']==revision and compact['source_digest']==full['context']['source_digest'] and compact['authority']=='inert_context_only' and compact['schema_version']==1
   account(compact);assert len(enc(compact))<len(enc(full))/4 and snapshot()==before
   assert e.context_query(**args)==full and e.context_query(**dict(args,if_none_match=''))==full
   from agent_workflow.core import Engine
   assert Engine(f['root']/'state').context_query(**conditional)==compact
   checks.append('default complete mandatory context; matching inert compact packet reduces exact UTF8 bytes and survives restart without Core writes')
   for invalid in ['garbage','0'*64,revision.upper(),' '+revision,revision+'x','中文']:
    assert e.context_query(**dict(args,if_none_match=invalid))==full
   for bad in [None,True,{},[],7,'x'*2049]:
    try:e.context_query(**dict(args,if_none_match=bad))
    except (c.Error,TypeError,ValueError):pass
    else:raise AssertionError('invalid conditional input accepted')
   checks.append('malformed/stale strings fall back full without echo; nonstring/oversize cannot suppress validation')
   # Changes whose selected context may remain the same still change identity.
   changed_revisions=[]
   for changes in [{'query':'other query'},{'byte_budget':'16000'},{'max_file_bytes':'4095'},{'mandatory_paths':'["guide.md"]'},{'mandatory_paths':'["guide.md","value.txt"]'},{'mandatory_paths':'["value.txt","guide.md"]'}]:
    v=e.context_query(**dict(conditional,**changes));assert v['not_modified'] is False and v['revision']!=revision;account(v,int(changes.get('byte_budget',16384)));changed_revisions.append(v['revision'])
   assert changed_revisions[-1]!=changed_revisions[-2], 'ordered mandatory paths lost from revision identity'
   assert e.context_query(**dict(args,byte_budget='016384',max_file_bytes='04096'))['revision']==revision
   old=(repo/'value.txt').read_bytes();(repo/'value.txt').write_text('changed unrelated source\n');assert e.context_query(**conditional)['not_modified'] is False;(repo/'value.txt').write_bytes(old)
   cfg=copy.deepcopy(f['config']);cfg['budget']['max_attempts']+=1;other=Engine(f['root']/'other-state',cfg);assert other.context_query(**conditional)['revision']!=revision
   same_repo=f['root']/'same-source-other-root';shutil.copytree(repo,same_repo);cfg=copy.deepcopy(f['config']);cfg['repo']=str(same_repo);other=Engine(f['root']/'other-source-policy-state',cfg);assert other.context_query(**conditional)['revision']!=revision
   checks.append('query paths/order normalized budgets cap source and full frozen policy changes invalidate revision')
   for mutation in ['missing','secret','budget','symlink']:
    original=skill.read_bytes()
    try:
     if mutation=='missing':skill.unlink()
     elif mutation=='secret':skill.write_text('api_key = '+('A'*32))
     elif mutation=='symlink':skill.unlink();skill.symlink_to(repo/'value.txt')
     else:skill.write_text('mandatory\n'*3000)
     try:e.context_query(**conditional)
     except (c.Error,ValueError,OSError):pass
     else:raise AssertionError('matching revision bypassed '+mutation+' mandatory/source check')
    finally:
     if skill.is_symlink() or skill.exists():skill.unlink()
     skill.write_bytes(original)
   try:e.context_query(**dict(conditional,byte_budget='1'))
   except (c.Error,ValueError):pass
   else:raise AssertionError('conditional match bypassed metadata budget')
   assert e.context_query(**args)==full and snapshot()==before
   checks.append('full mandatory secret missing containment and effective-budget validation runs before conditional equality')
   env=dict(os.environ,PYTHONPATH=str(a.repo.resolve()),PYTHONDONTWRITEBYTECODE='1');cmd=[sys.executable,'-B','-m','agent_workflow','--state',str(f['root']/'state')]
   cli=subprocess.run(cmd+['context_query','--args',json.dumps(conditional)],env=env,cwd=f['root'],text=True,capture_output=True,timeout=10);assert cli.returncode==0 and json.loads(cli.stdout)=={'ok':True,'result':compact}
   messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'context_query','arguments':conditional}}]
   mcp=subprocess.run(cmd+['mcp'],env=env,cwd=f['root'],input=''.join(json.dumps(x)+'\n' for x in messages),text=True,capture_output=True,timeout=10);assert mcp.returncode==0
   rows=[json.loads(x) for x in mcp.stdout.splitlines()];catalog=next(x['result']['tools'] for x in rows if x.get('id')==2);tool=next(x for x in catalog if x['name']=='context_query');assert len(catalog)==27 and tool['inputSchema']['properties']['if_none_match']=={'type':'string'} and tool['inputSchema']['required']==['query']
   result=next(x['result'] for x in rows if x.get('id')==3);assert not result.get('isError') and json.loads(result['content'][0]['text'])==compact
   checks.append('actual stdio CLI and MCP agree compact packet with optional string schema and same27 tools')
   clone=Path(tmp)/'other-controller';shutil.copytree(a.repo,clone,ignore=shutil.ignore_patterns('.git','.workflow','__pycache__'));(clone/'agent_workflow/native53-marker.txt').write_text('different immutable controller input\n')
   env['PYTHONPATH']=str(clone);changed=subprocess.run(cmd+['context_query','--args',json.dumps(conditional)],env=env,cwd=f['root'],text=True,capture_output=True,timeout=10);assert changed.returncode==0;v=json.loads(changed.stdout)['result'];assert v['not_modified'] is False and v['revision']!=revision
   assert snapshot()==before;checks.append('actual separate controller copy invalidates revision; no tasks runs events or proof reused')
   print(json.dumps({'passed':True,'checks':checks,'native_local_fixture':True,'full_canonical_utf8_bytes':len(enc(full)),'conditional_canonical_utf8_bytes':len(enc(compact)),'bytes_only':True,'actual_tokens':None,'actual_cost':None,'model_calls':0,'network_calls':0}));return 0
 except Exception as error:
  print(json.dumps({'passed':False,'checks':checks,'error_type':type(error).__name__,'error':str(error),'native_local_fixture':True,'model_calls':0,'network_calls':0}));return 1
if __name__=='__main__':raise SystemExit(main())
