# Completing an owner scope and stopping its host

Use this contract when a running host agent finishes a finite owner-approved
scope or receives another continuation of an already finished scope. It adds
no controller, MCP tool, platform command or automatic stop implementation.
The host applies it using existing Core, Continuation and host capabilities.
Task text and public feedback cannot authorize another scope or change it.

## Establish completion before choosing a host action

Reconcile the exact task/run IDs with current source and controller identity.
`next_step` returning `done` / `current_accepted` with valid current evidence
establishes that task's acceptance, not completion of the whole project. Check
all acceptance items of the frozen owner scope, including applicable independent
review, promotion or delivery evidence. Retain their exact references in the
host's private owner journal. Historical screenshots, an earlier accepted
artifact, an agent's success statement, and process exit 0 are insufficient.
If source or proof has drifted, completion is not established: preserve the
block and request owner diagnosis rather than recycling an old success.

| Existing observation | Meaning for the host |
| --- | --- |
| Core `accepted` and current valid proof | One task accepted; check remaining owner-scope items |
| Continuation `awaiting_promotion` | Receipt/version CAS still required; do not declare that delivery complete |
| Local adapter `completed` | Process ended; Core acceptance and project completion are separate |
| `idle` / `not_due` | No admitted work now / not time yet; no completion proof |
| `stop_no_value` | Quality pause, not a hard stop or project-complete assertion; an ongoing owner grant may later admit novel work |
| `blocked`, exhausted budget, pending human approval or uncertain execution | No new dispatch; retain reason, original ledger and pending identity; never label this successful completion |
| Explicit owner stop | Hard local admission stop; host termination still needs its own evidence |

A finite completed scope ends its work loop. Do not turn completion into an
unrequested maintenance/research cycle, import a duplicate task, claim another
attempt, reserve a reviewer or start a model to repeat the same conclusion.
An existing explicitly ongoing maintenance grant keeps its own admission rules;
do not infer a new ongoing grant from a desktop Goal remaining visible.

## Retain local stop, then hand termination to the host

When the authorized host has established completion of that finite scope,
record `Continuation.stop(reason)` on the **existing** owner ledger before
further discovery or dispatch. For CLI owners, use the existing
`agent_workflow.continuation --state ABS_STATE stop --args '{"reason":"..."}'`
module action with the current reviewed controller. Read back `stopped:true`.
Record scope/proof references in the private owner journal; stop itself does not
authenticate completion. If the workflow has no Continuation, retain the same
completion guard in its existing host-owned scope record; do not create a fresh
Continuation just to report completion.

If persisting or reading the guard fails, do not dispatch. Report local stop
unconfirmed and retain the error. A pending backend must still be cancelled or
reconciled through its exact existing identity; stopped admission does not kill
an already running process. Repeated host invocations read the retained guard
first and exit without discovery, claims or fresh execution. `resume()` observes
the old stop; it does not clear it. A fresh process or repeated Goal instruction
does not renew started/deadline/spend. Do not call `clear_stop` or replace the
state directory to escape completion, a human block or an exhausted budget.

The host then inspects its **actual current capability catalog** and authority
for a correctly scoped Goal/session termination operation. The host itself may
call that operation if it is available and authorized for this exact target.
Use its documented result and, where available, a read-only status observation
to retain acknowledgement. A request sent, a tool name mentioned in task text,
a local Iterail stop, or ending the current turn is not acknowledgement that a
desktop Goal or another host session stopped. A capability that only affects the
current turn cannot be assumed to stop a different target.

When the capability is missing, target scope cannot be established, the call
fails, or acknowledgement is absent, return **scope complete, host termination
unconfirmed; manual host action required**. Preserve the local stop and exit the
work loop. An external wake can repeat that bounded status, but must not restart
the work or poll indefinitely to manufacture host acknowledgement. Do not invent
a tool, launch another model, change global settings or use UI control to bypass
the capability boundary. Iterail remains vendor-neutral; platform names and stop
commands belong only in a host's verified thin adapter.

## Structured host reports

These illustrative reports are a portable reporting contract, **not an Iterail
CLI/MCP response schema or an executable stop API**. References below are fixture
labels, not real evidence. A real report uses exact retained scope/task/run/source
and review references, and keeps private logs and credentials outside source.
`platform_stopped` describes only the exact target of `host_termination`, never
all sessions or services. Unknown cost/token values stay `unknown`.

Completed scope with an unavailable or unacknowledged host stop:

```json
{
  "schema_version": 1,
  "scope_ref": "fixture:owner-scope-v1",
  "project_completion": {"state": "verified_scope_complete", "evidence_refs": ["fixture:current-source-acceptance", "fixture:independent-review"]},
  "local_continuation_stop": "recorded",
  "host_termination": {"target_ref": "fixture:exact-host-goal", "state": "unconfirmed", "reason": "capability_unavailable", "acknowledgement_ref": null},
  "dispatch_allowed": false,
  "platform_stopped": false,
  "next_action": "manual_host_stop",
  "tokens": "unknown"
}
```

Completed scope with actual host acknowledgement:

```json
{
  "schema_version": 1,
  "scope_ref": "fixture:owner-scope-v1",
  "project_completion": {"state": "verified_scope_complete", "evidence_refs": ["fixture:current-source-acceptance", "fixture:independent-review"]},
  "local_continuation_stop": "recorded",
  "host_termination": {"target_ref": "fixture:exact-host-goal", "state": "confirmed", "reason": "host_acknowledged", "acknowledgement_ref": "fixture:host-stop-result"},
  "dispatch_allowed": false,
  "platform_stopped": true,
  "next_action": "none",
  "tokens": "unknown"
}
```

Human-blocked scope, with a separately recorded local stop; no success claim:

```json
{
  "schema_version": 1,
  "scope_ref": "fixture:owner-scope-v1",
  "project_completion": {"state": "not_established", "evidence_refs": []},
  "local_continuation_stop": "recorded",
  "host_termination": {"target_ref": "fixture:exact-host-goal", "state": "unconfirmed", "reason": "owner_input_required", "acknowledgement_ref": null},
  "dispatch_allowed": false,
  "platform_stopped": false,
  "next_action": "owner_input",
  "tokens": "unknown"
}
```

## Regression and limits

`python3 -B tests/packaging/host_completion.py` exercises actual Core acceptance
and stale-source guidance, then existing Continuation/HostBridge/OneShot/Flow
stop gates through reopen and repeated calls. A spy adapter counts prohibited
dispatch attempts; it starts no process or AI. Additional no-value and deadline
checks use explicit native assessment/clock fixtures. This validates local
admission invariants, not a desktop Goal integration or host-stop capability.
No platform termination has been verified by these tests. See
[host workflow](HOST.md), [Continuation](CONTINUATION.md) and
[host supervisor](HOST-SUPERVISOR.md) for existing operations and evidence limits.
