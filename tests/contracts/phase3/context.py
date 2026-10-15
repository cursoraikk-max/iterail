from common import *
import tempfile

def make(root):
 (root/'rules.txt').write_text('Required acceptance: full mandatory text.\n')
 (root/'main.py').write_text('from related import repair\nrepair()\n')
 (root/'related.py').write_text('def repair():\n    return "λ修复"\n')
 (root/'unrelated.py').write_text('unrelated = 123\n')
 (root/'.env').write_text('API_KEY=synthetic-secret\n')
 (root/'secret.py').write_text('TOKEN="sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"\n')
 (root/'binary.dat').write_bytes(b'\xff\x00\xfe')
 (root/'huge.py').write_text('repair='+'x'*7000)

def bundle(m,root,budget=5000,required=None):
 return m.retrieve_context(root,'repair',mandatory_paths=required or ['rules.txt','main.py'],byte_budget=budget,max_file_bytes=1000,source_digest=source_digest(root))
def good(m):
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);make(root);b=bundle(m,root)
  require(b==bundle(m,root),'nondeterministic retrieval')
  require(b['schema_version']==1 and b['source_digest']==source_digest(root))
  require(b['used_bytes']==len(json.dumps({k:v for k,v in b.items() if k!='used_bytes'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()))
  require(b['used_bytes']<=5000)
  files={x['path']:x for x in b['files']};require({'rules.txt','main.py','related.py'}<=files.keys(),'related code/mandatory omitted')
  for p,x in files.items():
   require(x['content']==(root/p).read_text() and x['sha256']==hashlib.sha256(x['content'].encode()).hexdigest())
   require(x['bytes']==len(x['content'].encode()))
  require(files['rules.txt']['mandatory'] and files['main.py']['mandatory'])
  omitted={x['path']:x['reason'] for x in b['omitted']}
  require({'.env','secret.py','binary.dat','huge.py'}<=omitted.keys(),'unsafe omissions hidden')
  require('.env' not in files and 'secret.py' not in files)
  require(m.validate_context(b,source_digest(root),['rules.txt','main.py'],5000) is True)
  for edit in [lambda x:x['files'][0].update(content='tampered'),lambda x:x.update(source_digest='0'*64),lambda x:x.update(used_bytes=0),lambda x:x['files'].append(copy.deepcopy(x['files'][0])),lambda x:x['omitted'].append({'path':'../escape','reason':'budget'})]:
   altered=copy.deepcopy(b);edit(altered);rejects(lambda:m.validate_context(altered,source_digest(root),['rules.txt','main.py'],5000))
  altered=copy.deepcopy(b);altered['files']=[x for x in altered['files'] if x['path']!='rules.txt'];rejects(lambda:m.validate_context(altered,source_digest(root),['rules.txt','main.py'],5000))
  rejects(lambda:m.validate_context(b,'1'*64,['rules.txt','main.py'],5000))

def bounds(m):
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);make(root)
  for paths in [['missing.txt'],['binary.dat'],['secret.py'],['.env'],['../escape'],['/tmp/outside'],['rules.txt','rules.txt']]:rejects(lambda:bundle(m,root,required=paths))
  rejects(lambda:bundle(m,root,budget=20))
  rejects(lambda:m.retrieve_context(root,'repair',mandatory_paths=['rules.txt'],byte_budget=True,max_file_bytes=20,source_digest=source_digest(root)))
  rejects(lambda:m.retrieve_context(root,'repair',mandatory_paths=['rules.txt'],byte_budget=5000,max_file_bytes=1000,source_digest='0'*64))
  (root/'rules.txt').write_text('é'*2500)
  rejects(lambda:bundle(m,root,budget=3000))
  b=bundle(m,root,budget=12000);require(next(x for x in b['files'] if x['path']=='rules.txt')['content']=='é'*2500,'mandatory truncated')
 with tempfile.TemporaryDirectory() as t:
  root=Path(t);(root/'rules.txt').symlink_to('/tmp/nonexistent-phase3-test')
  rejects(lambda:m.retrieve_context(root,'q',mandatory_paths=['rules.txt'],byte_budget=5000,max_file_bytes=1000,source_digest='0'*64))
run('p3-context','context',['appserver'],[('related_code_integrity_and_omissions',good),('mandatory_path_utf8_bounds',bounds)])
