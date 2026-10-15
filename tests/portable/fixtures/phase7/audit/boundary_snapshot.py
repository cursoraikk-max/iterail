"""Read-only phase7 protected boundary snapshots. Never exports ledger payloads/tokens."""
import hashlib,json,sqlite3,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'phase5/trusted'))
from common import source_digest,encoded

def snapshot():
    files={}
    for phase in range(1,7):
        for path in (ROOT/f'phase{phase}').rglob('*'):
            if not path.is_file() or any(x in {'.git','__pycache__'} for x in path.parts):continue
            parts=path.relative_to(ROOT/f'phase{phase}').parts
            protected=(any('trusted' in part for part in parts[:-1]) or ('manifest' in path.name and path.suffix=='.json'))
            if protected:files[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    for path in list((ROOT/'phase4/controllers/c3').rglob('*.py'))+[ROOT/'phase4/live-config.json',ROOT/'phase4/live-owner/start.py']:
        if '__pycache__' not in path.parts:files[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    owner=ROOT/'phase4/live-owner/evolution.sqlite'
    db=sqlite3.connect('file:'+str(owner)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        names=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables={name:hashlib.sha256(encoded([dict(row) for row in db.execute('SELECT * FROM "'+name+'" ORDER BY rowid')])).hexdigest() for name in names}
        m=dict(db.execute('SELECT * FROM meta').fetchone());public={key:m[key] for key in ['started','deadline','calls','stopped','blocked','epoch']}
    finally:db.close()
    main=ROOT/'agent-workflow-multi'
    head=subprocess.run(['git','-C',str(main),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    return {'schema_version':1,'read_only':True,'protected_file_sha256':files,'original_owner_table_sha256':tables,'original_owner_public':public,'original_MainB_HEAD':head,'original_MainB_source_sha256':source_digest(main),'phase4_candidate_source_sha256':source_digest(ROOT/'phase4/candidate')}

if __name__=='__main__':
    value=snapshot()
    if len(sys.argv)>1:
        prior=json.loads(Path(sys.argv[1]).read_text());result={'snapshot':value,'passed':value==prior,'protected_files_unchanged':value['protected_file_sha256']==prior['protected_file_sha256'],'original_owner_tables_unchanged':value['original_owner_table_sha256']==prior['original_owner_table_sha256'],'original_MainB_unchanged':value['original_MainB_HEAD']==prior['original_MainB_HEAD'] and value['original_MainB_source_sha256']==prior['original_MainB_source_sha256'],'phase4_candidate_unchanged':value['phase4_candidate_source_sha256']==prior['phase4_candidate_source_sha256']};print(json.dumps(result,sort_keys=True,indent=2));raise SystemExit(0 if result['passed'] else 1)
    print(json.dumps(value,sort_keys=True,indent=2))
