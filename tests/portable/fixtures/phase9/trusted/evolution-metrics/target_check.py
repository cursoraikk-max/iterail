import hashlib,json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
def digest(root):
 h=hashlib.sha256()
 for p in sorted(root.rglob('*')):
  rel=p.relative_to(root)
  if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or p.suffix=='.pyc':continue
  if p.is_symlink():raise ValueError('symlink')
  if p.is_file():h.update(str(rel).encode()+b'\0'+p.read_bytes()+b'\0')
 return h.hexdigest()
before=digest(root);p=subprocess.run(['sh',str(root/'app.sh')],cwd=root,capture_output=True,text=True);after=digest(root)
r={'id':'fixture-check','passed':p.returncode==0,'classification':'passed' if p.returncode==0 else 'product_defect','source_stable':before==after,'exactsourceSHA':before,'evidence':[{'argv':['sh','app.sh'],'exit_code':p.returncode,'evidence_kind':'native_fixture'}]}
print(json.dumps(r));raise SystemExit(0 if r['passed'] else 1)
