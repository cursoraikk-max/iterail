"""Frozen phase7 acceptance check; native owned fixture only."""
import json, sys, traceback
import audit_common as a
before=a.common.source_digest(a.SOURCE)
try:
    observation,passed=a.store_integrity_restart()
    evidence=[{'case':'store_integrity_restart','passed':passed,'observations':observation}]
except Exception as error:
    passed=False;evidence=[{'case':'store_integrity_restart','passed':False,'error':type(error).__name__+': '+str(error),'traceback':traceback.format_exc()}]
after=a.common.source_digest(a.SOURCE)
passed=passed and before==after
print(json.dumps({'id':'p7-uiux-restart-integrity','passed':passed,'classification':'passed' if passed else 'product_defect','exactsourceSHA':before,'source_unchanged':before==after,'evidence_kind':'native_fixture','external_provider_calls':0,'network_calls':0,'evidence':evidence},sort_keys=True))
raise SystemExit(0 if passed else 1)
