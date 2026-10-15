# Expired worker ticket recovery

`HostBridge` is a caller-side ledger; these methods do not start an AI. The
conversation supervisor remains responsible for dispatch and interruption.

After a worker stops responding, call `bridge.reconcile(request_id)`. An exact
Core actor/task/source/controller match whose lease or deadline expired is
reported as `expired_pending`, including when the first bind reply was lost.
This observation does not revive the lease or grant permission to execute.

Use `bridge.retry_expired(request_id)` explicitly to request a replacement.
It requires actual lease expiry, unchanged original source, no live checkout
lease or newer task outcome, matching immutable policy/controller, current Core
retry admission and enough of the **original** owner grant for another worker
and independent review pair. Quality-mode recovery additionally requires current
quality admission; this API cannot author an assessment or grant admission.

The original ticket becomes `expired` with `replacement_request_id`; its run,
actor and spent quota remain historical. The new `reserved` ticket has a new
actor, `run_id: null`, `dispatch_started: false` and `retry_of`. Retiring the old
ticket, reserving the new one and charging two slots use one ledger transaction.
Replaying the old request returns its replacement without charging again,
including after a lost reply or reopen. Starting the new ticket uses `begin` as
usual. The actual new Core `claim` reaps the old expired lease through Core's
existing transaction; the bridge never directly edits Core state or reuses its fencing token. Reconciliation is still required after the replacement claim.

An owner grant of two slots cannot pay for an interrupted pair plus a fresh
pair. Refusal in that case is expected. Stops and original wall deadlines remain
hard boundaries; neither reopening nor retry extends them. Source drift requires
owner diagnosis rather than silently adopting worker edits. Failed/rejected
reviews, ambiguous outcomes, newer/out-of-band runs and unknown execution
outcomes require separate diagnosis and are not admitted by this expiry API.

Regression (no models, real local Core/SQLite/CLI subprocesses and lease expiry):

```sh
python3.11 -B tests/packaging/hostbridge_recovery.py
/path/to/installed/python -I -B tests/packaging/hostbridge_recovery.py --installed
```

The regression exercises recovery through `ready_review`, not independent AI
acceptance or autonomous dispatch. The original real Stage A ledger remains
stopped and untouched; this fix does not retroactively complete that trial.
