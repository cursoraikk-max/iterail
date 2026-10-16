# Host supervisor boundary and proposed portable contract

This is a design proposal, not an installed scheduler or a verified unattended
integration. Keep the persistent Core task ledger and owner policy authoritative.
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
