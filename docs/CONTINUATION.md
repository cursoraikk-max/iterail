# Durable conversation pull

`agent_workflow.continuation.Continuation` keeps ongoing development requests in
an owner ledger outside the editable checkout. A completed task is a checkpoint:
after independently accepted promotion, a later due tick discovers the next
valuable owner finding or records recoverable idle. There is no built-in limit
of two tasks or a package-and-stop completion rule. The immutable wall deadline
and request ceiling are resource limits; they never renew on restart or resume.

This module does not call a model, start internal agents or schedule a future
platform wake. An authorized controlling conversation must pull its requests,
perform internal worker/reviewer orchestration and use the normal Core lifecycle.
Each result says `autonomous_dispatch:false`,
`requires_conversation_bridge:true`, and `external_provider_calls:0`. A durable
request is a bridge intent, not proof that an agent executed. An external provider
owner's spent or expired grant is neither read nor reused.

## Owner policy and bootstrap

Supply a separately reviewed policy to the constructor or module `init` command.
The exact schema is:

```json
{
  "schema_version": 1,
  "repo": "/absolute/clean/project",
  "core_state": "/absolute/owner/core",
  "core_policy": {"path": "/absolute/owner/core.json", "sha256": "CORE_POLICY_SHA256"},
  "runtime_root": "/absolute/owner/runtimes",
  "bootstrap": {
    "controller_path": "/absolute/owner/runtimes/reviewed",
    "package_sha256": "FROZEN_AGENT_WORKFLOW_SHA256",
    "source_sha256": "CLEAN_REPO_SOURCE_SHA256",
    "head": "CLEAN_GIT_COMMIT"
  },
  "cadence": {"interval_seconds": 60, "backoff_seconds": 5, "max_backoff_seconds": 60},
  "budget": {"wall_seconds": 3600, "max_requests": 20},
  "adapter": "conversation_pull",
  "external_provider_authorized": false
}
```

The hashes and commit above are placeholders, not authority. Bootstrap requires
the exact clean reviewed checkout and physical frozen package. Initialize Core
with its immutable local test/producer policy beforehand. The continuation owner
state, Core policy/state and runtime root remain outside the editable repo.
Continuation pins its policy in `continuation.sqlite` and `identity.json`; a
missing or corrupt existing identity cannot silently create a new owner epoch.
Events retain discoveries, reservations, run bindings, stop reasons and promotions.

Core operations run the selected physical package's project CLI using its own
`PYTHONPATH`. They do not import the editable candidate's Engine. Existing Core
permissions, frozen commands, lease fencing and verification stay authoritative.

## Pull and bridge lifecycle

```sh
python3 -B -m agent_workflow.continuation --state /absolute/owner/continuation --policy /absolute/owner/continuation.json init
python3 -B -m agent_workflow.continuation --state /absolute/owner/continuation tick
python3 -B -m agent_workflow.continuation --state /absolute/owner/continuation status
```

A due tick runs existing bounded owner producer discovery. Only server-owned
machine reproduction and fixed acceptance bindings for the **current** source can
produce a worker request. Arbitrary imported notes, passing checks and old queued
tasks from invalidated source revisions do not qualify. Persisted current producer
findings can be reconciled after a discovery completed before a bridge crash.
No qualifying work records `idle`, a reason and an absolute `next_due`. Early,
stopped, expired and unreviewable-resource ticks do no discovery. Physical Core
timeouts record bounded backoff; integrity faults block for owner diagnosis.
A pending request's physical Core status timeout also retains that absolute due
time across restart. Early pulls return `not_due` without another status process
or changes to failure count, due time, request, reserved pair or deadline. At the
due time, polling resumes against the same actual Core run.

Worker admission spends two request slots together, reserving independent review.
The request carries its id, kind, actual task id, optional run id and exact
source/version/physical-package identities. It contains no fencing token or new
commands/permissions. The bridge explicitly claims that task with its selected
controller, privately retains the normal Core token and calls
`bind_run(request_id, run_id)`. Binding observes the real active Core lease,
baseline and workflow. If binding was lost or delayed until the worker changed
source and reached `verified` or `ready_review`, explicitly supply that existing
run ID. Continuation checks its requested task, immutable claim baseline,
physical workflow and current Core policy, then obtains physical Core
`next_step(run_id=...)` guidance for that exact run/task/stored state. Both
`source_match` and `evidence_valid` must be true, and the current source must
still equal that verified candidate. An unrelated semantic goal blocker cannot
erase valid base machine evidence. Original Core lease/deadline and continuation
deadline must remain live after the guidance process returns.

Binding never rewrites the request baseline, chooses a run automatically, creates
another run, renews a deadline, or spends/refunds slots. Repeating the same valid
binding retains its request and reservation; a different run is rejected.
Changed but unverified source, failed or stale proof, wrong identities, expired
resources and policy/runtime drift remain rejected. An already accepted unbound
run is outside this recovery path; bind before acceptance. This is recovery of a
specific recorded run, not exactly-once external execution or publication.
A live bound run reports `in_flight`; actual `ready_review`
creates the corresponding review request without another reservation.

Use module actions `bind_run` and `promote` with `--args` containing the method's
named arguments. The existing Core CLI and MCP endpoints continue to handle
claim, progress, change, verification and independent acceptance. Continuation's
module CLI only manages its owner ledger; attaching it to a conversation does
not make MCP an agent scheduler.

## Independent promotion and stopping

`promote(request_id, receipt_path, receipt_sha256, controller_path,
expected_version)` requires the review request's actual accepted Core run and
fresh successful machine evidence. The approved receipt is a regular SHA-bound
owner file outside the checkout, with exactly these fields:

```json
{"schema_version":1,"approved":true,"reviewer":"independent-reviewer","run_id":"ACTUAL_RUN","core_state":"/absolute/owner/core","source_root":"/absolute/clean/project","source_sha256":"ACCEPTED_SOURCE_SHA256","controller_package_sha256":"EXECUTED_WORKFLOW_SHA256"}
```

The reviewer must differ from the actual worker. Receipt text cannot accept a
Core run. Commit the accepted clean exact source and copy its `agent_workflow`
package into a new immutable directory under `runtime_root`. Promotion verifies
that physical copy, refuses in-flight Core work and compares `expected_version`
before atomically changing the owner pointer. It retains the original deadline,
spent requests and history. A later worker must claim through the newly selected
physical package; an old package cannot mutate the new version's Core run.

If Core reaches `accepted` while the bridge is absent, the next tick reconciles
the bound worker intent to its already reserved independent review, even if no
tick observed `ready_review`. Repeated ticks retain that review's identity and
the original deadline and spent pair. `accepted` still requires the independent
receipt and every promotion check above. After an ambiguous promotion response,
query `status` for the active version and pending intent before retrying.

Verified recovery lesson: Git `299ea2057790586049256cd44df459fb0e8676d2`
left an accepted run attached to a worker intent when an intermediate poll was
missed; promotion then rejected the non-review intent. The frozen
`accepted_checkpoint_guard.py` regression reproduces real local Core acceptance
without that poll, checks restart-stable review reconciliation, and rejects a bad
receipt before exercising promotion and lost-response status recovery. Apply this
lesson when an observed durable state has advanced past an optional poll: inspect
actual state and reconcile the existing reservation rather than redispatching.
It does not establish exactly-once external model or publication side effects.

`stop(reason)` persists an explicit stop and preserves pending intent. `resume()`
only observes that state; it cannot clear stop or refund resources. The separate
owner action `clear_stop(reason)` clears stop with a reason, while retaining the
original deadline and spent reservations. Expired or terminal bound attempts
block for bridge reconciliation rather than minting replacement requests.

The bridge must reconcile durable requests against actual Core history after an
ambiguous dispatch. Request replay does not prove exactly-once model launches,
and this initial layer does not cover every crash point across the separate Core
and continuation databases. Same-user ledgers and hashes support review and
diagnosis, not protection against an administrator editing every proof. The six
frozen native fixtures exercise real local Core/producer commands, idle/restart,
stop/resources and accepted version handoff with zero model/provider calls.

## Quality policy continuation

An existing validated Core `quality_policy` may replace producer authority for
this same owner ledger. When both descriptors are present, quality admission is
authoritative. The policy schema, absolute wall deadline, request ceiling and
cadence remain unchanged. Tick observes the selected physical Core `next_step`
using an existing task anchor; an empty project blocks without creating a task.

A current independently justified finding reserves the ordinary worker/reviewer
pair. Missing or stale semantic assessment reserves one original request slot as
`quality_review_requested`, with `kind:quality_review` and no run. This stable
intent survives restart and cannot bind a worker run or promote a release.
The host obtains an independent assessment, then invokes:

```sh
python3 -B -m agent_workflow.continuation --state /absolute/owner/continuation resolve_quality_review --args '{"request_id":"ACTUAL_REQUEST_ID"}'
```

Resolution rereads the physical Core's fresh assessment and unchanged source,
Core policy, quality policy and controller identities. It accepts no caller
verdict, clears only the pending intent and preserves deadline and spent slots.
A complete current schema2 assessment may resolve its review while retaining
`goal_planning_required`, `goal_research_required`, `goal_validation_required`,
`goal_capability_required`, `goal_approval_required` or `goal_task_unavailable`.
These finite typed waits grant no work, permission or global completion. The
successful journal stores the validated projection and request identities. Due
ticks and restarts observing that exact current projection stay explicitly blocked
without spending another review slot. A changed qualified projection may request
review, and a genuinely admitted task may reserve its worker/reviewer pair using
only the original remaining resources. Stale or incomplete assessments and identity
drift cannot inherit acknowledgment or suppress a fresh required review.
Identity drift blocks with the same request retained. Native clock jumps and
injected transport timeouts test recovery; they do not prove elapsed multi-day
operation or external agent dispatch.

`stop_no_value` pauses discovery with `stopped:false` and persistent due time.
A fresh novel independently reviewed finding can reopen it under the remaining
original resources; stale assessment can request semantic review only. Explicit
`stop(reason)` is still a hard user stop requiring `clear_stop(reason)`.
Continuation dispatch remains a host responsibility, separate from MCP, research
collection, semantic review, task acceptance and publication.


Verified late-binding regression: R4 Git
`acd8b7fda8424e30d5825802508313c88f935826` equated the immutable claim baseline
with the current changed source, rejecting a real successfully verified run after
a missed binding call. `late_bind_guard.py` preserves that failure and exercises
changed verified/ready-review recovery, restart, repeated binding, independent
acceptance and version-CAS promotion. It also rejects unverified, failed, drifted,
wrong-task/workflow, expired and accepted-unbound cases. Native fixtures and a
bounded real local self-use run do not establish external agent dispatch or
multi-day unattended reliability.
