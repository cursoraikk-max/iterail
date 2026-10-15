"""Synthetic native release-transition fixture, never an actual product defect."""
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();ident=sys.argv[2]
def digest(root):
 h=hashlib.sha256()
 for path in sorted(root.rglob('*')):
  rel=path.relative_to(root)
  if any(x in {'.git','.workflow','__pycache__'} for x in rel.parts) or path.suffix=='.pyc':continue
  if path.is_symlink():raise ValueError('symlink')
  if path.is_file():h.update(str(rel).encode()+b'\0'+path.read_bytes()+b'\0')
 return h.hexdigest()
before=digest(root);text=(root/'agent_workflow/runner.py').read_text()
passed=True
if ident=='native-b':passed='#NATIVE_RELEASE_B' in text
elif ident=='native-c':passed='#NATIVE_RELEASE_B' not in text or '#NATIVE_RELEASE_C' in text
elif ident=='native-health-reject':passed='#NATIVE_RELEASE_B' not in text
else:compile(text,str(root/'agent_workflow/runner.py'),'exec')
print(json.dumps({'id':ident,'passed':passed,'classification':'passed' if passed else 'product_defect','source_stable':before==digest(root),'exactsourceSHA':before,'evidence':[{'evidence_kind':'native_fixture','actual_product_defect':False,'condition':ident}]}));raise SystemExit(0 if passed else 1)
