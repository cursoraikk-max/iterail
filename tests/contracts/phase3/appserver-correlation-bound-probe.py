import sys,json
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'trusted'))
from common import source_digest,load
repo=Path(sys.argv[1]).resolve();before=source_digest(repo);m=load(repo,'appserver');s=m.OfflineSession(max_bytes=128);accepted=False
try:s.register_request('q'*65537,'thread/read');accepted=True
except (ValueError,TypeError):pass
after=source_digest(repo);passed=not accepted
print(json.dumps({'id':'p3-appserver-correlation-bound','passed':passed,'classification':'passed' if passed else 'product_defect','source_stable':before==after,'exactsourceSHA':before,'evidence':{'external_backend_calls':0,'registered_id_bytes':65537,'session_max_bytes':128,'accepted':accepted,'assertion':'request correlation metadata cannot bypass the bounded parser input'}}))
raise SystemExit(0 if passed else 1)
