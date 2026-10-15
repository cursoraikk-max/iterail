"""Frozen readonly owned-root cleanup regression, no models/sockets/native dispatch."""
import hashlib,json,os,shutil,stat,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'phase4/trusted'))
from common import run,require
SECRET='PHASE15_RAW_CLEANUP_CANARY_NEVER_EXPORT_35419351'

def remove_owned(root):
 if not root.exists():return
 # Test-owned rescue only, after mocked failures end; never follow any symlink.
 for directory,dirs,files in os.walk(root,followlinks=False):
  os.chmod(directory,0o700)
  dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink()]
 shutil.rmtree(root)

def snapshot(root):
 root=Path(root);nested=root/'runtime'/'agent_workflow';nested.mkdir(parents=True);(nested/'private.py').write_text(SECRET);(nested/'private.py').chmod(0o444);nested.chmod(0o555);nested.parent.chmod(0o555);root.chmod(0o555)

def success(m):
 with tempfile.TemporaryDirectory(prefix='p15-success-') as name:
  seen=[];out=Path(name)/'out'
  def callback(root):seen.append(Path(root));snapshot(root)
  try:
   result=m.run_fixture(callback,out);require(result['passed'] is True and result['cleanup_complete'] is True and result.get('artifact') is None,'readonly success did not report completed cleanup');require(not seen[0].exists(),'readonly success left raw fixture');require(not out.exists() or not any(out.iterdir()),'success emitted failure artifact')
  finally:
   for root in seen:remove_owned(root)

def failure(m):
 with tempfile.TemporaryDirectory(prefix='p15-failure-') as name:
  seen=[];out=Path(name)/'out'
  def callback(root):seen.append(Path(root));snapshot(root);raise AssertionError(SECRET)
  try:
   result=m.run_fixture(callback,out);require(result['passed'] is False and result['cleanup_complete'] is True,'readonly failure cleanup masked callback/artifact');path=Path(result['artifact']);require(path.is_file() and path.resolve().is_relative_to(out.resolve()),'failure artifact missing');require(SECRET.encode() not in path.read_bytes() and path.suffix=='.json','raw failure canary exported');require(json.loads(path.read_bytes())['schema_version']==1,'sanitized failure record unavailable');require(not seen[0].exists(),'readonly failed raw fixture survived completed cleanup')
  finally:
   for root in seen:remove_owned(root)

def outside_symlink(m):
 with tempfile.TemporaryDirectory(prefix='p15-outside-') as name:
  base=Path(name);outside=base/'unrelated';outside.mkdir();sentinel=outside/'sentinel';sentinel.write_text(SECRET);sentinel.chmod(0o444);outside.chmod(0o555);out=base/'out';out.mkdir(mode=0o700);before=(stat.S_IMODE(sentinel.stat().st_mode),stat.S_IMODE(outside.stat().st_mode),stat.S_IMODE(out.stat().st_mode));seen=[]
  def callback(root):
   root=Path(root);seen.append(root);nested=root/'readonly';nested.mkdir();(nested/'external-link').symlink_to(outside,target_is_directory=True);nested.chmod(0o555);root.chmod(0o555)
  try:
   result=m.run_fixture(callback,out);require(result['passed'] is True and result['cleanup_complete'] is True and not seen[0].exists(),'own readonly symlink tree not removed');require(sentinel.read_text()==SECRET and before==(stat.S_IMODE(sentinel.stat().st_mode),stat.S_IMODE(outside.stat().st_mode),stat.S_IMODE(out.stat().st_mode)),'cleanup chmodded/followed unrelated target or output');require(not any(out.iterdir()),'symlink success emitted artifact')
  finally:
   for root in seen:remove_owned(root)
   outside.chmod(0o700)

def actual_cleanup_failure(m):
 with tempfile.TemporaryDirectory(prefix='p15-unlink-blocked-') as name:
  out=Path(name)/'out';seen=[];unlink=os.unlink
  def callback(root):
   root=Path(root);seen.append(root);(root/'blocked').write_text(SECRET);raise AssertionError(SECRET)
  def blocked(path,*args,**kwargs):
   if Path(os.fsdecode(path)).name=='blocked':raise PermissionError(SECRET)
   return unlink(path,*args,**kwargs)
  try:
   with patch('os.unlink',blocked):result=m.run_fixture(callback,out)
   require(result['passed'] is False and result['cleanup_complete'] is False and isinstance(result.get('cleanup_error'),dict) and result['cleanup_error'],'unremovable raw file falsely reported completed');path=Path(result['artifact']);require(path.is_file() and path.resolve().is_relative_to(out.resolve()) and SECRET.encode() not in path.read_bytes(),'committed failure record lost/raw exported during cleanup error');require(seen[0].exists(),'forced deletion failure fixture unexpectedly absent');require(SECRET not in json.dumps(result),'raw cleanup exception exported');require(all(p.suffix=='.json' for p in out.rglob('*') if p.is_file()),'private raw root exported after cleanup error')
  finally:
   for root in seen:remove_owned(root)

run('iterail-owned-readonly-cleanup','diagnostics',[('owned_readonly_success_cleanup',success),('owned_readonly_failure_retains_artifact_and_cleans',failure),('symlink_target_and_unrelated_permissions_untouched',outside_symlink),('permanent_cleanup_error_is_explicit_and_preserves_record',actual_cleanup_failure)])
