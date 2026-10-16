# Host supervisor boundary and bounded one-shot fixture

This page describes **optional launch/fixture adapters**, not a prerequisite for
normal use. An already running authorized agent can call the shared Core CLI or
its host's mounted MCP tools directly; the host owns model login and wake-up.
An independent CLI-launch initialization failure (including EPERM) does not make
that separate path a Core release requirement. Keep its diagnostic evidence,
without retrying login or launching a nested model merely to prove ordinary
agent-internal Core use. See [primary host workflow](HOST.md).

The one-shot fixture slice below is implemented. The broader provider and wake
contract remains a design proposal, not an installed scheduler or a verified
unattended model integration. Keep the persistent Core task ledger and owner policy authoritative.
The CLI and MCP are alternative Core transports, not competing state stores.

## Existing pieces and remaining responsibilities

| Piece | Available behavior | Responsibility still outside it |
| --- | --- | --- |
| Core CLI / minimal stdio MCP | Import, fenced claims, heartbeat, progress, machine verification, task/run history and advice | Wake a host, start an AI or kill its process |
| Continuation | Durable owner deadline, cumulative request charges, stop and version promotion | Independent background lifetime and provider access |
| HostBridge | Reserve/begin, exact Core reconciliation, expired-ticket retry and receipt-bound finish | Prove a real host dispatch or cancel a real AI |
| Runner / guardian | Existing bounded subprocess path and process control, with an owner-pinned `exec-v1` adapter | A generally interchangeable provider protocol or persistent OS wake-up registration |
| Offline app-server parser | Validate supplied protocol observations | Open a live session, approve an action or establish provider readiness |

Presence of these code paths is not proof of a real backend run or a working
wake source. A backend-specific attempt must supply its own retained evidence.

Core leases fence state changes; they do not terminate an AI process or prevent
same-user file writes. A Continuation stop blocks further intents but does not
itself cancel an already running backend. A host must perform both operations and
retain their separate outcomes. Unknown cancellation remains unknown execution,
not permission to reuse a consumed ticket. Expired fencing tokens cannot be used
for cleanup. Preserve stored state and effective expiry separately until a normal
Core operation reconciles them.

## Proposed minimal host interface

Keep vendor-specific code behind four operations: `start`, `observe`, `cancel`
and `reconcile`. Each takes an opaque durable dispatch key and returns structured
host evidence with a host execution ID, sequence/cursor and observation time.
`start` also takes an exact owned work directory, fixed executable/adapter identity,
role, original deadline and the permitted resource ceiling. This interface does
not itself grant permission or imply an external exactly-once guarantee.

Persist intent **before** calling the host, then persist the returned execution ID.
The durable link is request ID -> host execution ID -> Core run ID -> source and
controller hashes. After losing a start reply, reconcile the exact dispatch key;
if the host cannot prove the outcome, stop as uncertain rather than start again.
Use Core progress and heartbeat only within the still-live original owner grant
and run limits; model text is not a heartbeat acknowledgement. Missing observations
must not renew a lease. Expiry creates a new charged worker/review pair through
`retry_expired`, never a refund or silent rebind. Quality admission must be current
for the replacement; review and publication credentials remain separate.

Start with a bounded **one-shot** host command invoked by an owner-selected wake
source. It observes stop/deadline, reconciles history, performs at most one admitted
dispatch boundary and exits. A wake source may later be an existing scheduler or
an OS job chosen and authorized by its owner. No wake source is configured by
this proposal. Preserve the original grant across invocations; a new process
must not manufacture a new wall budget or reconstruct spend from memory.

## Protocol references and evidence limits

MCP stdio has the client launch the server and exchange JSON-RPC messages. Its
connection lifecycle describes initialization, operation and shutdown. These
transport rules do not establish a persistent Iterail scheduler or a model
launch grant. See the official [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).

OpenAI's [app-server documentation](https://learn.chatgpt.com/docs/app-server)
describes stdio startup, thread start/resume, turn start, event observation and
turn interruption. That makes it one possible execution adapter, not the core
architecture. Documentation alone does not prove a usable executable, account,
permission profile or detached host connection. Other backends need their own
verified adapter; an executable found in PATH is insufficient.

Before claiming unattended support, independently verify a real host's bounded
start/observe/cancel behavior, lost-start-reply reconciliation, process restart,
heartbeat loss and hard stop. Bind its evidence to exact task/run/source IDs.
Run a distinct worker and reviewer under one immutable grant, including rejection
and interruption recovery. Deterministic process fixtures can validate ledger
and adapter state transitions; they cannot prove a paid provider or autonomous
wake-up integration. No new service, provider, credentials or global configuration
is installed by this change.


## Implemented one-shot slice

`agent_workflow.hostsupervisor.HostAdapter` defines `start(intent)`,
`observe(intent)`, `cancel(intent)` and `reconcile(intent)`. Every observation
contains `execution_id`, `dispatch_key`, `state`, integer `sequence` and numeric
`observed_at`. Identity/cursor regression rejects; missing or stale running
observations remain `uncertain`. An observation is never Core proof or acceptance.
The Python protocol has no model dependency. Its first implementation,
`LocalProcess`, is an explicitly authorized **POSIX local fixture** using the
existing guardian; Windows execution is unverified.

`OneShot(continuation, adapter).run(action)` serializes callers on the existing
Continuation directory. On the first `tick`, HostBridge reserves and begins a
currently admitted request. An immutable host intent is committed in the same
Continuation SQLite ledger **before** `adapter.start` is called. Adapter identity
is bound on first use and cannot be replaced on restart. The executing host
package must match the current reviewed physical Continuation runtime; changed
controller bytes refuse a new launch. The intent carries the
original deadline, role, source/controller packet and opaque execution identity.
`source_root` identifies the candidate checkout; `workdir` separately binds the
actual owner-selected fixture cwd. The wrapper and guardian entry recheck the
original stop/deadline and cancellation mailbox before child launch.
Budget counters remain solely in Continuation; host observation grants no lease
renewal. Once an intent exists, all subsequent invocations only observe,
reconcile or cancel it. This thin slice intentionally handles **one intent per
owner grant**, including after terminal completion: it does not auto-dispatch a
reviewer, successor, replacement or quality resolver. Existing HostBridge/Core
APIs remain necessary for independently authorized work and receipts.

A consumed HostBridge ticket with no host intent is uncertain and cannot be
started again. A lost start reply, missing local job, failed spawn or missing
observation similarly cannot refund spend or permit redispatch. A successful
fixture process does not bind a Core run or accept/promote any code. Current
quality admission is checked by HostBridge before dispatch; independent Core
verification, reviewer attribution, quality assessment and promotion gates are
unchanged. See `tests/packaging/host_supervisor.py` and the existing quality and
promotion regressions.

## Explicit fixture owner setup and command

Create a fresh ordinary Continuation using the reviewed Core/Continuation setup
in [CONTINUATION.md](CONTINUATION.md). Keep its original grant; do not create a
new grant to retry an uncertain fixture. Place a trusted, **no-model** Python
fixture script and its work directory outside the editable checkout. No fixture
policy is inferred from a task body or public feedback.

Write a separate owner JSON file with exactly these keys (replace placeholders
with existing absolute resolved paths and SHA256 values):

```json
{
  "schema_version": 1,
  "fixture_only": true,
  "continuation_state": "/absolute/owner/continuation",
  "executable_path": "/absolute/resolved/python3",
  "executable_sha256": "SHA256_OF_EXECUTABLE_BYTES",
  "script_path": "/absolute/owner/fixture.py",
  "script_sha256": "SHA256_OF_SCRIPT_BYTES",
  "cwd": "/absolute/owner/fixture-work",
  "max_seconds": 10,
  "output_bytes": 2048
}
```

The policy, executable and script must be outside the candidate. Hash the exact
JSON bytes and keep that hash with the owner grant. The fixture executable runs
`-I -B` against a private job snapshot of the approved script bytes, with no
task-supplied argv. Both the initial caller and the final guardian spawn gate
validate the owner-pinned original script/interpreter hashes; the final gate also
checks exact argv, policy identity and snapshot hash. Cooperative edits observed
before that gate reject without executing replacement content. Later original
file edits cannot change the approved snapshot being executed. This is not a
filesystem transaction or protection from malicious same-UID rewrites of the
private snapshot, interpreter or dependencies. Imported dependencies are not
frozen by this single-script fixture. The environment has a private HOME,
fixed PATH/LANG and an execution ID; it does not inherit provider credentials.
`fixture_only` is an owner assertion about trusted code, not an OS sandbox or
proof that arbitrary code cannot access the account. Do not select a model
launcher under this fixture grant.

```bash
python3 -m agent_workflow.hostsupervisor   --state /absolute/owner/continuation   --host-policy /absolute/owner/host-policy.json   --host-policy-sha256 EXACT_JSON_SHA256 tick
```

Use the same arguments with `observe`, `reconcile` or `cancel`. Each command
returns structured JSON and exits; none installs a wake source or loops as a
scheduler. Owner stop/deadline prevents a fresh dispatch. A later invocation
requests cancellation of an existing intent after stop/deadline. Cancellation
is asynchronous: a request is not evidence that execution stopped. The fixture
wrapper also enforces the original absolute deadline and the smaller per-process
ceiling without another invocation.

For a fully self-contained no-model diagnostic, run:

```bash
python3 -B tests/packaging/host_supervisor.py
# After tools/check_distribution.py:
/absolute/distribution/wheel/venv/bin/python -I -B   tests/packaging/host_supervisor.py --installed
```

The test owns temporary projects and processes. It exercises real start,
completion, cancel, guardian timeout, parallel callers, restart and lost replies,
as well as stop/expiry/quality refusal. It changes no user setup.

## Process identity, cancellation and limits

The local adapter records a unique execution ID and nonce in the owned job
mailbox. Only the original guardian holds the actual subprocess handle/group.
A restarted caller writes an exact nonce-bound cancel request; it never sends a
signal to a stored PID. The existing guardian handles EOF, bounded output and
process-group cleanup. Terminal observations require its matching result with
confirmed group cleanup. PID files remain diagnostic only. Missing/mismatched
identity or result fails closed. Original owner stop and child termination are
separate events; an already admitted launch can race with a later stop and must
be cancelled/observed. There is no atomic OS-spawn/SQLite transaction.

The bounded wrapper may outlive the one-shot caller only until its fixed
resource cutoff. This is a temporary fixture process, not an OS service. A crash
before host launch may consume an intent without execution; no exactly-once
external guarantee is claimed. OS power-loss durability and hostile same-UID
file/DB mutation are outside the tested cooperative process-restart contract.
Escaped sessions or a killed guardian retain the existing guardian limitations.
No paid model, detached AI host, provider routing, authenticated external cancel,
automatic wake, long-running self-development or worker/reviewer execution is
verified by these fixture checks.


Recovery initialization validates the immutable owner policy, adapter identity,
reviewed controller and nonce-bound job, but does not reopen mutable launch
inputs. Editing or deleting the original script/executable, or losing the launch
cwd, does not by itself revoke observe/cancel/reconcile of an existing job.
A new start still validates all launch inputs and cannot silently repin them.
Cancellation continues through the existing guardian mailbox, never a recovered
PID. A final launch refusal retains `launch-rejection.json`; without a matching
actual guardian result it remains uncertain rather than reporting completed.

The initial launch records cwd device/inode identity. The final guardian gate
requires a canonical directory without symlink components, outside source_root,
with the same device/inode. Both symlink substitution and ordinary directory
replacement observed at that gate reject. This is a cooperative point-in-time
check, not atomic open-directory execution; a malicious same-UID rename after
the final check and inode reuse are not guaranteed against.

## Explicit multi-intent Flow (local fixture slice)

`agent_workflow.hostflow.Flow` is a separate opt-in API over the same
Continuation, HostBridge and adapter protocol. It does **not** enlarge an existing
OneShot grant: first initialization refuses any existing host intent. OneShot's
original one-intent ceiling remains unchanged. Do not mix the two APIs on a flow
ledger; OneShot refuses a multi-intent history.

Before first dispatch, the owner supplies a separate regular JSON file outside
source and pins its exact byte SHA256. It has exactly these fields:

```json
{
  "schema_version": 1,
  "continuation_state": "/absolute/owner/continuation",
  "continuation_identity_sha256": "SHA256_OF_EXISTING_IDENTITY_JSON_BYTES",
  "adapter_identity": "local-process-v1:EXACT_HOST_POLICY_SHA256",
  "allowed_roles": ["worker", "review"],
  "max_intents": 4,
  "poll_seconds": 2
}
```

The roles are a nonempty unique subset of `worker` and `review`. Quality resolver
and expired-worker replacement dispatch are deliberately excluded from this
slice: use existing separately authorized APIs, never silently replace a consumed
intent. `max_intents` is a positive integer no larger than the original
Continuation request ceiling. Polling is 1–60 seconds. The original owner identity,
started/deadline, adapter and policy bytes are bound durably; restarts cannot
repin them. The original Continuation budget still charges worker/review pairs,
and remains authoritative independently of the additional host-intent ceiling.

```sh
python3 -B -m agent_workflow.hostflow \
  --state /absolute/owner/continuation \
  --flow-policy /absolute/owner/flow.json --flow-policy-sha256 EXACT_FLOW_SHA256 \
  --host-policy /absolute/owner/local-process.json --host-policy-sha256 EXACT_HOST_SHA256 tick
```

Each `tick` dispatches **at most one** new intent. Subsequent due ticks observe
that exact intent, reconcile real Core history through HostBridge and may admit
the next role. A completed adapter process is not Core proof: worker-to-review
requires actual current Core `ready_review` evidence and identity. A completed
review process merely waits for the existing independent assessment, Core
acceptance, exact receipt and promotion/CAS APIs. Flow neither synthesizes a
receipt nor accepts/promotes a run. Only a later current, quality-admitted
Continuation request may produce another worker. A newly promoted package must
be invoked through its current physical reviewed runtime.

`observe` and `reconcile` never start a new process. `cancel` persists the original
Continuation stop before competing for the nonblocking host lock, then requests
cancellation of the retained dispatch when possible. The owner ledger lock is
not held during adapter start, so a slow start cannot itself prevent durable stop. Cancellation remains
asynchronous and unknown until the adapter proves it. Stop/deadline bypass poll
backoff and prevent new starts; a busy caller may need a later cancellation
invocation to reach the adapter. A launch already in progress still has the
existing cooperative spawn/stop race and adapter hard cutoff; no atomic OS-spawn
transaction is claimed.

Flow stores absolute `next_due` across restarts. Early `tick` performs no adapter
operation or Core discovery. Idle Core discovery uses Continuation's existing
cadence/backoff. Unknown, failed, cancelled or expired adapter outcomes never
cause redispatch or refunds. Lost start replies reconcile the durable dispatch
key; a consumed bridge ticket without a host intent blocks for owner diagnosis.
Cursor and terminal identity are checked per request, so an earlier task's
terminal observation cannot masquerade as a successor's observation.

The Python interface is provider agnostic; the CLI intentionally uses only the
existing owner-pinned **no-model LocalProcess fixture**. No wake source, loop,
OS service, provider credentials, model CLI or real autonomous AI is installed or
verified. `tests/packaging/host_flow.py` uses explicit simulated journal/assessment
fixtures, actual Core verification and normal receipt/promotion, plus real
LocalProcess worker/review fixtures, CLI observation and guardian hard timeout.
It runs from source and an isolated installed wheel. Simulated reviewer metadata
is never described as an external independent AI reviewer.

新 Flow 必须有单独明确授权，不自动扩大旧 OneShot。每次 tick 最多派发一个意图；
进程完成不等于 Core 验收。未知不重派、不退款；取消先持久 stop，原期限和预算
保持不变。只验证本地 fixture、多步骤接续与恢复，未验收无人值守真实 AI。
