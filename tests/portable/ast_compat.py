"""Narrow known AST-serialization compatibility; never changes contract predicates."""
import argparse,ast,hashlib,json,sys
from pathlib import Path
PINS={'semantic.py':'29a32c04b0120a2d7c2c64c417a03a23ebb919308c8370f2c666a4868aa22306','deadline.py':'c75225387a4d3abcf3725e4f2e261d0de613a28ed29fe9e5f30a18c14c34f3a2'}
TARGET='a88410bede2efdb6e93ae2622ec2848c290b7446ba1c1ead26c91dba2345d229'
REFERENCE='dd693dfc07f0b7d3e9e727e1372645ee026e3df7646c30195e4d0b8ecaf73f6e'
AST_SHA={(3,11):'46b3f212c7d9e7c2f013a299fbd0970d07cf47f93a66da383dc8eaeab7e839d7',(3,14):REFERENCE}
STRUCTURE='b71b07790c2c9af90bcbd975dc24c51df3cca66e87b29ff6dedc9f23833f3e29'
def sha(b):return hashlib.sha256(b).hexdigest()
def neutral(value):
 if isinstance(value,ast.AST):return {'node':type(value).__name__,**{k:neutral(v) for k,v in ast.iter_fields(value) if v!=[]}}
 if isinstance(value,list):return [neutral(v) for v in value]
 return value
def main():
 p=argparse.ArgumentParser();p.add_argument('--script',required=True,type=Path);p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args();script=a.script.resolve();assert script.name in PINS and script.parent.name=='trusted' and script.parent.parent.name=='phase20';assert sha(script.read_bytes())==PINS[script.name]
 expected=AST_SHA.get(sys.version_info[:2]);assert expected is not None,'unreviewed Python AST schema'
 target=script.parents[2]/'phase4/trusted/target.py';assert sha(target.read_bytes())==TARGET;tree=ast.parse(target.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='native_target')
 assert sha(ast.dump(fn,include_attributes=False).encode())==expected
 assert sum(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='require' for n in ast.walk(fn))==12
 normalized=json.dumps(neutral(fn),sort_keys=True,separators=(',',':'))+'\n';normalized=normalized.encode();assert sha(normalized)==STRUCTURE,'normative AST semantic structure drift'
 original=ast.parse(script.read_text());assignments=[n for n in original.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ASSERTIONS_AST_SHA' for t in n.targets)];assert len(assignments)==1 and isinstance(assignments[0].value,ast.Constant) and assignments[0].value.value==REFERENCE
 assignments[0].value.value=expected;sys.argv=[str(script),*a.args];namespace={'__name__':'__main__','__file__':str(script),'__package__':None};exec(compile(original,str(script),'exec'),namespace)
if __name__=='__main__':main()
