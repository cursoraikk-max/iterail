# Host workflow guidance

For a finite owner scope that is complete, or a host that keeps continuing an
already completed scope, follow the [completion and host-stop contract](HOST-COMPLETION.md).
Retain local admission stop separately from actual host termination acknowledgement.

The primary workflow is a running, authorized host agent calling Core through the
standard CLI or an actually mounted project-local MCP tool. Authentication and
wake-up stay with that host; Iterail does not require another model login or a
nested `codex exec`. When native Iterail MCP tools are unavailable, use CLI and
report that scope. A subprocess stdio test validates protocol behavior, not a
real Codex/Claude Code MCP connection. The optional launch adapters have separate
readiness evidence and cannot gate a different host call path solely because
that adapter cannot initialize. Follow normal claim, exact-source machine proof,
independent review and acceptance; another task needs its own valid claim.

The host-driven sequence remains bounded by its original owner grant. Do not
report it as detached 24x7 operation or infer unknown token costs. If no independent
reviewer mechanism exists, hand off at `ready_review`; do not review yourself.

A running host worker can connect the project-local MCP stdio interface, inspect `capabilities` with `{}`, then call `next_step` with exactly one known `task_id` or `run_id`. The Core method and CLI action share the same reader:

```sh
python3 -m agent_workflow --state /absolute/existing-state next_step --args '{"task_id":"task-EXISTING"}'
```

The CLI reads existing state before any Engine initialization and ignores `--config` for this query. Missing state remains missing. Guidance uses a read-only SQLite snapshot and bounded project-source hashing. It changes no policy, task, evidence, event, lease or attempt count, and executes no command. It returns fixed actions/reasons, source-match and evidence-valid observations, never tokens, titles, bodies, worker identities, policy bodies, command arguments or log text.

No attempt suggests `claim`; running work suggests `continue_work`; a current submitted candidate suggests `verify`. Active source or proof drift suggests `change` before fresh verification. Valid verified proof suggests `ready_review`; that milestone suggests `await_independent_review`. Verification in progress suggests `wait`, even with source drift. Expiry permits another claim only while the recorded attempt allowance remains; exhaustion suggests `owner_reassess`. An active package mismatch suggests `use_claimed_controller`, which does not assert that controller remains available. Revision inconsistencies and stale accepted proof also require owner reassessment. Drift in a trusted producer checker, owner descriptor or bound check requires `owner_reassess`, not a worker change to owner-controlled files.

Proof checks recorded baseline/candidate/workflow, exact task revision and policy, expected command list including any actual producer binding, strict integer zero exits, no execution errors, unchanged source and valid ownership at finish. The reader never reconciles status or invents review authority. Historical accepted proof may remain current after a controller upgrade when the project source still matches; `done` is a local stored milestone, with deployment `not_recorded` and `published:false`.

Every response is `authority:advisory_only` and `automatic_execution:false`. Acting on it still requires the existing owner grant, correct claimed controller and private fencing token where indicated. The host supplies agent dispatch, models and authentication; guidance starts no agent and establishes no provider readiness. The optional Runner has its separately configured execution profile. See [Core usage](../README.md), [Runner](RUNNER.md) and [handoffs](HANDOFF.md) for the underlying actions and evidence boundaries.

Owner projects may opt into the [quality guard](QUALITY.md). The host supplies
privileged independent source-bound semantic assessments and resolves the stable
worker/reviewer intent against actual Core history before dispatch. The appended
quality projection is read-only and advisory; Core separately enforces quality
admission at claim and policy identity on owned mutations. Existing lease,
controller and attempt checks still apply. A repair may restore a failed or stale
source before fresh proof and independent assessment; no-value stop resumes only
for newly justified current work under the original owner grant.

## Reusable bridge contract and no-model observation

`agent_workflow.diagnostics.host_contract()` describes the existing Continuation
`worker`, `review`, and `quality_review` packets. This is a model-independent,
structural contract, not a dispatcher. Use
`validate_host_request(packet, expected_version=active_version)` before handling
an inert packet. Exact keys, bounded IDs/hashes, role-specific task/run identities,
and a canonical absolute controller path without observed symlinks are required.
The optional expected version compares id, controller path and package hash. It
deliberately does not compare the active version's historical source with the
request's immutable baseline. The sanitized return omits the controller path.

A valid shape authenticates no owner, approval, executable or model. Reread the
actual durable ledger and reconcile its request/run before using existing fenced
Core calls. Validation creates no run, event, lease or reservation. Do not accept
unknown packet fields as a prompt or as expanded commands, budgets or permission.
Model selection, identity, execution grants, future wake and fulfillment remain
host responsibilities. The common Core CLI/MCP actions remain the main task
interface; these helpers add no MCP tool.

For a bounded observation using a selected project-local package:

```sh
python3 -B -m agent_workflow.diagnostics host-readiness --source-root /absolute/project
```

Optional flags `--executable /absolute/owner-selected-launcher` and
`--expected-executable-sha256 HASH` inspect static file metadata and bytes only.
The file must be a regular executable-mode file with no observed symlink leaf or
ancestor, read through a nonblocking no-follow regular descriptor, at most
16 MiB. `metadata_only` means a hash observation; it does not mean the launcher
can execute. Missing, nonregular, symlink, oversized, non-executable and hash
mismatch results remain explicit. Some large native binaries exceed this bound.
No executable is launched, including a version check. There is no account,
credential, environment, authentication or quota inspection.

An explicit optional local IPC observation is:

```sh
python3 -B -m agent_workflow.diagnostics host-readiness --source-root /absolute/project --probe-ipc --probe-root /absolute/owner/probes
```

The probe root must already exist, be canonical and outside the source checkout,
with no observed symlink ancestors. Only an owned temporary child is created and
removed. Probes bind/listen on IPv4 loopback port zero and a temporary Unix path,
with a short socket timeout and descriptor cleanup. They never resolve a host,
connect, send, accept or contact an external service. Results report
`available`, `blocked`, or `unsupported` and numeric errno when supplied by the
OS. Without `--probe-ipc`, no socket or temporary data is created. An IPC success
establishes only that specific local operation at that moment.

Every successful report retains `model_calls:0`, `executes_commands:false`,
`autonomous_dispatch:false`, `provider_ready:false`, and
`provider_readiness:"not_tested"`, even if all local checks succeed. It exposes
source/package hashes and fixed categories, never raw paths, prompts, file bytes,
exceptions, private packets or logs. Invalid inputs return sanitized JSON with a
nonzero CLI exit. Existing native fixture diagnostic invocation is unchanged.

Source and package identities reuse the existing bounded Core-compatible
`guidance._source` framing: 20,000 entries and 64 MiB streamed in 64 KiB chunks per
hash. Ignored runtime paths and nonregular entries such as FIFO match Core; FIFO
is never opened. Descendant symlinks are rejected. This is a cooperative static
filesystem observation, not an atomic snapshot, hostile concurrent replacement
isolation, OS sandbox proof, live provider readiness, or long-duration evidence.
Current exact-source Core verification and independent review remain necessary.

## Durable current-host dispatch bridge

`agent_workflow.hostbridge.HostBridge(continuation)` wraps a trusted existing
`Continuation` instance. It adds one dispatch table to that same owner SQLite
ledger and reuses its lock, clock, immutable policy, request reservations and
Core history. It does not create a second task database, scheduler, model client,
MCP tool, process runner or platform connection. Host execution stays outside the
module and requires the caller's existing authorization.

```python
from agent_workflow.continuation import Continuation
from agent_workflow.hostbridge import HostBridge
bridge = HostBridge(Continuation('/absolute/owner/continuation'))
reserved = bridge.reserve()                       # existing due tick / intent
one_time_ticket = bridge.begin(reserved['request_id'])
# The authorized host now uses its own platform tools to dispatch that ticket.
# A worker must claim its exact task using one_time_ticket['actor'].
# Keep the resulting Core fencing token only in the host's private 0600 handle.
observed = bridge.reconcile(reserved['request_id'])
```

`reserve()` is idempotent for the current immutable request. Dispatch keys are
SHA256 of Continuation's sorted compact JSON packet excluding only `run_id`;
actor is `host-` followed by the first 32 key characters. Binding the real run
therefore does not change dispatch identity. This actor is an opaque correlation
ID, not an authenticated user, allowlisted reviewer or permission grant.

`begin(request_id)` checks the current policy, physical version, actual Core task
and existing outcomes before committing `executing` and returning one ticket.
Its `dispatch_started:true` means that boundary was committed, not that the
platform launched an AI. Keep actual platform-call evidence separately. Repeated
begin, restart, uncertainty, rejection or completion never returns another ticket.
A reserved intent can instead adopt existing manual bootstrap work through
`reconcile`, retaining `dispatch_started:false` and without inventing a begin.

After a lost host reply, `reconcile(request_id)` inspects actual Core records by
exact task, derived actor, immutable claim baseline and physical workflow. Only
one matching real run can use existing `bind_run`, including its verified-source
late recovery. No match or ambiguous attempts yield durable `uncertain`; neither
is permission to dispatch again. The bridge does not choose the newest run, trust
a caller's run ID, recover a lost secret token, or prove exactly-once external
execution. Source, lease, deadline, attempt, admission and controller fences remain
Core/Continuation responsibilities and still apply. A later `reserve()` tick
advances bound work to its already reserved independent review.

For review tickets, the host dispatches an actual independent reviewer using its
existing allowlisted identity, not the opaque dispatch actor. The reviewer must
obtain current machine evidence and write the owner assessment; existing Core
validation and acceptance remain mandatory. A Boolean or host callback assertion
cannot mark work accepted. Then call
`finish(request_id, receipt_path, receipt_sha256, controller_path, expected_version)`
with the normal external independent receipt and clean immutable successor. It
records a finish intent and delegates existing accepted-Core promotion/CAS. A
lost promotion reply is reconciled from the actual `version_promoted` event and
selected version, without a second promotion or new resource reservation. This
automatic acknowledgment requires that event's selected version still be the
current active version. Reconcile an uncertain finish before advancing again;
if a later version has already superseded it, this helper does not automatically
acknowledge the older historical receipt. Preserve the record for explicit owner
history reconciliation rather than replaying promotion.

A valid, current `approved:false` receipt from a pinned quality-policy reviewer
records durable `rejected` and its SHA; Core acceptance, current version and spent
resources remain unchanged. Self, unknown, stale, malformed or wrong-run receipts
cannot become rejection or approval authority. Projects without a quality reviewer
allowlist cannot record trusted negative receipt authority through this helper.
Rejection does not authorize redispatch; a separate explicit owner action and
current proof are needed. Negative receipt recording uses a cooperative proof
observation followed by a ledger write, not an atomic source/lease snapshot at
commit time: source or lease can change between those observations. It records
only rejection metadata and grants no Core acceptance or promotion. Current
Core mutation fences and final promotion checks remain authoritative. A failed
finish keeps its intent for owner diagnosis.

Semantic `quality_review` tickets use existing resolution rather than inventing
semantic truth. Fresh complete independent schema2 results can fulfill the review
while the actual Core projection retains its finite typed remaining-goal wait.
Missing, stale or invalid authority retains the original request and a blocked
record. After an actual successful resolution loses its outer reply, reconcile
observes the exact `quality_review_resolved` journal entry for that same request,
current validated projection and source/policy/controller identities. It completes
the existing dispatch record without calling the resolver or dispatching again.
An earlier different request or stale projection cannot authorize completion.
Recovery conservatively requires the same current projection and identities: a
later assessment, source or physical version change cannot borrow an old event
to acknowledge that intent and requires owner diagnosis.
No external promotion receipt is involved; independent assessment remains the
semantic authority. Follow `docs/CONTINUATION.md` for duplicate-review suppression
and original resource limits. `status()` exposes only bounded dispatch identities,
phases and original start/deadline/spent totals; no tokens, task bodies, receipt
reviewer names or private paths. Historical `completed` records are not assertions
that current source or overall goals are still complete.

The bridge uses snapshots, ordinary Core/Continuation calls outside its held
lock, and record CAS on return. It is not an atomic distributed transaction or OS
sandbox. Native regression fixtures deliberately discard replies after real local
Core side effects; those controlled faults are distinct from actual AI/platform
execution. R7's initial worker implementation bootstraps manually under R6. Only
real subsequent bridge tickets and separately recorded host platform calls count
as AI dispatch, including the actual independent review and following real task.
