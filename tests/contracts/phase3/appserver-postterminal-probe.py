import sys,json
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'trusted'))
from common import source_digest,load
repo=Path(sys.argv[1]).resolve();before=source_digest(repo);m=load(repo,'appserver');s=m.OfflineSession();s.register_request('late','thread/read')
s.consume({'method':'turn/started','params':{'threadId':'t','turn':{'id':'u','status':'inProgress','items':[]}}})
s.consume({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed','items':[]}}})
s.consume({'id':'late','error':{'code':-32600,'message':'post-terminal request failure'}})
snap=s.snapshot();passed=not(snap['completed'] and snap['failed']);after=source_digest(repo)
print(json.dumps({'id':'p3-appserver-postterminal-summary','passed':passed,'classification':'passed' if passed else 'product_defect','source_stable':before==after,'exactsourceSHA':before,'evidence':{'external_backend_calls':0,'snapshot':snap,'assertion':'failed global session summary cannot simultaneously claim completed success'}}))
raise SystemExit(0 if passed else 1)
