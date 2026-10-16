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

For quality-mode tasks, advice may treat an expired stored attempt as eligible
for claim only when the actual Core claim transaction would reap its lease.
Advice never changes the task to `queued`; the real claim still performs that
transaction, checks remaining attempts and enforces fresh independent admission.
Deadline-only expiry before lease expiry does not qualify for this projection.

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

Quality recovery has a separate deterministic fixture contract:

```sh
python3.11 -B tests/packaging/quality_recovery.py
/path/to/installed/python -I -B tests/packaging/quality_recovery.py --installed
```

It uses real Core/CLI processes and real lease expiry, with owner-authored
assessment fixtures representing independent roles. It rejects stale, self,
unknown-reviewer and regressed assessments at acceptance. Promotion also rechecks
the existing quality validator: a past `accepted` event cannot authorize promotion
after an assessment becomes stale, regresses or references the wrong proof run.
The machine tests, current source and independently attributed receipt must still
match. These fixtures are not actual independent AI reviews or evidence of
quality-mode model dispatch. See [the proposed host boundary](HOST-SUPERVISOR.md)
for the remaining unattended execution work.

## Final promotion fence

Receipt, Git and physical-runtime checks finish before a Core writer reservation
is acquired. The reservation uses `mode=rw`, `BEGIN IMMEDIATE`, then
`PRAGMA query_only=ON`: it excludes real Core writers in WAL mode without changing
Core application data. Lock order is Continuation owner lock, Core writer
reservation, then Continuation metadata transaction. No Core subprocess runs
while that reservation is held. Accepted run/task/policy and active leases are
reread in that connection, and it stays reserved until metadata commit or rollback.

The final transaction binds source/runtime/receipt/policy bytes, validates a fresh
quality snapshot and checks owner authority immediately before the update. A
pre-commit fence repeats file bindings and the exact assessment SHA/freshness,
then checks the original owner gate last. A failed final fence rolls back the
version update and success event; a retained finish intent is not completion.
Successful promotion records the assessment SHA used for its authorization.

Core/ledger operations are serialized through commit. The final file-authority
witness is the promotion's filesystem observation point, followed immediately by
the original owner gate. Ordinary file writers do not participate in SQLite's
transaction. Later file changes do not retroactively undo a committed historical
event; preventing arbitrary same-UID changes requires cooperative locking or
immutable versioned owner inputs. These checks do not claim global atomicity
across independently mutable filesystem inputs and SQLite.

`tests/packaging/promotion_commit.py` covers late assessment changes, natural age
expiry without a clock patch, original owner deadline, final-event rollback,
source/runtime binding drift and an actual competing `Core.claim`. It runs against
source and installed packages. All assessment roles are deterministic fixtures.
