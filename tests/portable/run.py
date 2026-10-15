#!/usr/bin/env python3
"""Run all portable native contracts from an external exact-byte fixture tree."""
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tempfile,time
from pathlib import Path
def sha(b):return hashlib.sha256(b).hexdigest()
def digest(root):
 h=hashlib.sha256()
 for p in sorted(root.rglob('*')):
  r=p.relative_to(root)
  if any(x in {'.git','.workflow','__pycache__'} for x in r.parts) or p.suffix=='.pyc':continue
  if p.is_symlink():raise ValueError('source symlink unsupported')
  if p.is_file():h.update(r.as_posix().encode()+b'\0'+p.read_bytes()+b'\0')
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--deadline',required=True,type=float);a=p.parse_args();repo=a.repo.resolve();out=a.output.resolve();assert out!=repo and repo not in out.parents,'logs must be outside source';out.mkdir(parents=True,exist_ok=True)
 suite=repo/'tests/portable';manifest=json.loads((suite/'manifest.json').read_text());assert manifest['core_commands']==53 and manifest['supplements']==2 and len(manifest['commands'])==55 and len({x['id'] for x in manifest['commands']})==55 and len(manifest['files'])==87
 for n,h in manifest['files'].items():
  q=Path(n);assert not q.is_absolute() and '..' not in q.parts;f=suite/'fixtures'/q;assert f.is_file() and not f.is_symlink() and sha(f.read_bytes())==h
 before=digest(repo);results=[];started=time.time()
 with tempfile.TemporaryDirectory(prefix='iterail-portable-fixtures-') as temp:
  owned=Path(temp);fixtures=owned/'fixtures';fixtures.mkdir();tested=owned/'source'
  shutil.copytree(repo,tested,ignore=shutil.ignore_patterns('.git','.workflow','__pycache__','*.pyc'));assert digest(tested)==before,'test copy source mismatch'
  environment={'PATH':str(Path(sys.executable).parent)+os.pathsep+os.defpath,'LANG':'C.UTF-8','PYTHONHASHSEED':'0','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(tested),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,'HOME':str(owned)}
  assert time.time()<a.deadline,'owner deadline exhausted before fixture Git'
  setup=[]
  for command in [['git','init','-q',str(tested)],['git','-C',str(tested),'add','.'],['git','-C',str(tested),'-c','user.name=Portable acceptance fixture','-c','user.email=fixture@example.invalid','commit','-qm','Owned byte-identical test snapshot']]:
   assert time.time()<a.deadline,'owner deadline exhausted before fixture Git command'
   initialized=subprocess.run(command,env=environment,capture_output=True,check=True,timeout=max(.01,min(30,a.deadline-time.time())));setup.append({'argv':command,'exit_code':initialized.returncode,'stdout_sha256':sha(initialized.stdout),'stderr_sha256':sha(initialized.stderr)})
  for n in manifest['files']:
   f=fixtures/n;f.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(suite/'fixtures'/n,f)
  for i,command in enumerate(manifest['commands'],1):
   argv=[]
   for v in command['argv']:
    v=v.replace('{python}',sys.executable).replace('{fixtures}',str(fixtures)).replace('{source}',str(tested));assert '{' not in v and '}' not in v;argv.append(v)
   assert time.time()<a.deadline,'owner deadline exhausted before command'
   original_argv=list(argv)
   if command['id'] in {'core-16','core-35'}:argv=[sys.executable,'-B',str(tested/'tests/portable/ast_compat.py'),'--script',argv[2],*argv[3:]]
   start=time.time();r=subprocess.run(argv,cwd=fixtures,env=environment,capture_output=True,timeout=max(0.01,min(manifest['per_command_seconds'],a.deadline-time.time())));stdout=out/('%02d.stdout'%i);stderr=out/('%02d.stderr'%i);stdout.write_bytes(r.stdout);stderr.write_bytes(r.stderr);text=(r.stdout+b'\n'+r.stderr).decode('utf-8',errors='replace');valid=r.returncode==0
   if i==1:valid=valid and re.search(r'Ran 55 tests in ',text) is not None and not re.search(r'\bskipped\b|\bexpected failures\b',text,re.I)
   if command['kind']=='supplement':
    parsed=json.loads(r.stdout);expected=manifest['supplement_expected'][command['id']];valid=valid and all(parsed.get(k)==v for k,v in expected.items())
    if command['id']=='version-supplement':valid=valid and parsed.get('source_sha256')==before and all(re.fullmatch(r'version-[0-9a-f]{24}',parsed.get(k,'')) for k in ['old_version','current_version']) and parsed['old_version']!=parsed['current_version']
   copy_after=digest(tested);source_same=digest(repo)==before and copy_after==before;row={'id':command['id'],'kind':command['kind'],'argv':argv,'original_argv':original_argv,'exit_code':r.returncode,'passed':valid and source_same,'source_unchanged':source_same,'original_source_root':str(repo),'tested_owned_source_root':str(tested),'original_source_sha256':digest(repo),'tested_source_sha256':copy_after,'seconds':time.time()-start,'stdout_sha256':sha(r.stdout),'stderr_sha256':sha(r.stderr),'stdout_log':str(stdout),'stderr_log':str(stderr)};results.append(row)
   if not row['passed']:break
  tested_after=digest(tested);assert tested_after==before,'owned test source changed'
 result={'fixture_git_setup':setup,'tested_owned_copy':True,'tested_source_digest':tested_after,'original_source_root':str(repo),'tested_source_root':str(tested),'passed':len(results)==55 and all(x['passed'] for x in results),'configured_core_commands':53,'independent_supplements':2,'required_commands':55,'executed':len(results),'skips_allowed':False,'source_before':before,'source_after':digest(repo),'source_unchanged':digest(repo)==before,'fixture_manifest_sha256':sha((suite/'manifest.json').read_bytes()),'seconds':time.time()-started,'results':results,'model_calls':0,'network_calls':0};(out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
