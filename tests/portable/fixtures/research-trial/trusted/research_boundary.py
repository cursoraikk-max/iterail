#!/usr/bin/env python3
"""Independent supplied-data receipt containment boundary; no model or network."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--repo',type=Path,required=True);args=parser.parse_args()
    guard=Path(__file__).with_name('research_guard.py')
    spec=importlib.util.spec_from_file_location('frozen_research_contract',guard)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    observed={};source=None
    try:
        with tempfile.TemporaryDirectory(prefix='research-receipt-boundary-') as temp:
            native=module.Native(args.repo,Path(temp).resolve());native.legacy()
            source=native.c.digest(args.repo)
            fixture=native.fixture('receipt_containment');request=fixture['r'].request('security');observation,_=native.observe(fixture,request)
            normal,receipt=native.receipt(fixture,request,observation)
            result=fixture['r'].review(str(normal));assert result['status']=='admit'
            observed['normal_receipt_admitted']=True
            outside=fixture['root']/'outside.json';module.write(outside,receipt)
            traversal=str(fixture['reviews']/'..'/'outside.json')
            denied=False
            try:fixture['r'].review(traversal)
            except native.Error:denied=True
            observed['outside_via_parent_traversal_denied']=denied
            assert denied,'receipt outside frozen review_directory was admitted via reviews/../outside.json'
        passed=True;error=None
    except Exception as failure:
        passed=False;error={'type':type(failure).__name__,'message':str(failure)}
    record={'passed':passed,'case':'receipt canonical directory containment','observed':observed,'error':error,'source_sha256':source,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'helper_script_sha256':hashlib.sha256(guard.read_bytes()).hexdigest(),'supplied_native_fixture':True,'actual_public_search':False,'network_calls':0,'model_calls':0,'external_api_calls':0}
    print(json.dumps(record));return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
