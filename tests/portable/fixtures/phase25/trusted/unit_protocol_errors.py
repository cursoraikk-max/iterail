"""Owner reviewed normative assertion adapter; original tests remain immutable."""
import ast,sys,types,unittest
from pathlib import Path
repo=Path(sys.argv[1]).resolve();sys.dont_write_bytecode=True;sys.path.insert(0,str(repo))
original=repo/'tests/test_context.py';tree=ast.parse(original.read_text(),filename=str(original));needle=ast.dump(ast.parse("self.assertTrue(replies[2]['result']['isError'])").body[0],include_attributes=False)
class Migration(ast.NodeTransformer):
 count=0
 def visit_Expr(self,node):
  if ast.dump(node,include_attributes=False)==needle:
   self.count+=1
   return [ast.copy_location(n,node) for n in ast.parse("self.assertEqual(replies[2]['error']['code'], -32602)\nself.assertNotIn('result', replies[2])").body]
  return self.generic_visit(node)
m=Migration();tree=m.visit(tree);assert m.count==1,'expected exactly one normative assertion migration';ast.fix_missing_locations(tree)
import tests
module=types.ModuleType('tests.test_context');module.__file__=str(original);module.__package__='tests';sys.modules[module.__name__]=module;exec(compile(tree,str(original),'exec'),module.__dict__)
suite=unittest.defaultTestLoader.discover(str(repo/'tests'),top_level_dir=str(repo));assert suite.countTestCases()==55,'unchanged suite count required'
result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)
