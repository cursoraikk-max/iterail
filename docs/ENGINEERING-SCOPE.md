# Shared engineering scope and runtime preflight

An optional `engineering_scope` project descriptor binds Core states to one
owner-created cumulative budget. Existing unbound repositories keep their old
behavior. This is a cooperative engineering guard against accidental reset,
not protection against an account owner deleting or rewriting Git metadata,
policies or databases. It installs no scheduler or backend.

## Owner setup before freezing tasks

Use an existing Python3.11+ environment with the required build dependencies.
Place an owner JSON policy outside the editable repository. Replace all paths,
times and limits below with the original grant; deadline is absolute Unix time,
not a duration restarted by each process. The ledger must be outside source.

```json
{
  "schema_version": 1,
  "scope_id": "owner-project-round-1",
  "repo": "/absolute/project",
  "ledger": "/absolute/owner/scope.sqlite",
  "started": 1792200000,
  "deadline": 1792203600,
  "max_dispatches": 8,
  "max_repairs": 3,
  "python": "/absolute/build-venv/bin/python",
  "build_requirements": {"setuptools": "77", "wheel": "0.45", "pip": "22.3"},
  "tests": [["/absolute/build-venv/bin/python", "-m", "unittest", "discover"]]
}
```

Hash the exact JSON bytes and initialize explicitly:

```sh
python3 -B -m agent_workflow.scope init --policy /absolute/owner/scope.json --sha256 EXACT_BYTES_SHA256
```

This runs bounded version/metadata/help probes, never the project's test argv,
and records executable paths, physical binary hashes, Python version, selected
environment distribution versions, test argv and cwd. Invoking a venv Python
retains that venv's identity; resolving its binary for hashing does not switch
the probe to the base environment. Missing packages or unsupported Node
`--test` block before ledger creation. Opaque shell/env wrappers are unsupported;
select the effective direct interpreter instead. Other executable arguments
are recorded, not proven to work. The probes do not log in, install dependencies,
run a model, or establish provider readiness. Installed dependencies themselves
are not recursively hashed.

Successful initialization writes an exclusive descriptor marker named
`iterail-engineering-scope.json` in Git's metadata/common directory and creates
the external ledger. A later state cannot omit/replace that descriptor. Missing
or mismatched ledger/marker fails closed, never auto-creates another grant. Git
worktrees share a common marker: this slice still pins one exact repo/cwd and
does not relocate a scope into a different checkout. Existing worktree support
without a scope remains unchanged. Owner initialization can diagnose an interrupted
setup; workers never repair missing owner state or repin a policy.

Add the exact descriptor to the ordinary project configuration before Core init:

```json
{"engineering_scope": {"path": "/absolute/owner/scope.json", "sha256": "EXACT_BYTES_SHA256"}}
```

The policy repo and test argv must exactly match that configuration. New config
freezing checks the environment, and each fresh claim rechecks the stored
fingerprint. Runtime/version/dependency/argv-executable drift blocks before debit.
Return to the original approved environment rather than making a new state to
evade this block. Actual tests and exact-source independent review remain required.

## Core / CLI / MCP use

Scoped `claim` requires a named worker, exact task ID and stable owner work-request
ID. Keep the same request ID when retrying a lost reply. In another state,
reconcile the original state instead of creating a fresh request for unknown work.

```sh
python3 -B -m agent_workflow --state /absolute/state claim --args '{"worker":"worker-a","task_id":"task-ID","request_id":"scope/work-1"}'
python3 -B -m agent_workflow --state /absolute/state scope_status
python3 -B -m agent_workflow --state /absolute/state scope_preflight
```

The same fields are exposed by MCP. The three added actions are `scope_status`,
`scope_preflight` and `scope_repair`; scope-enabled Core/MCP expose30 tools.
Unscoped MCP retains27 tools and rejects scope calls. The pure CLI capabilities
query reads no state and describes the default27 plus optional scope tool metadata.
`scope_status` reconciles authoritative origin Core links but starts nothing.
Module `agent_workflow.scope status` reads the stored budget snapshot. Outputs
contain owner paths/argv and are private evidence, not ready-made public reports.

Before local claim, the shared scope lock serializes duplicate/concurrent callers.
A durable reservation permanently charges one **logical Core worker admission**;
it is not a count or proof of external model/process starts. Core commits the
request/run link with its local run, whose deadline is capped by the original
scope. A separate shared-ledger bind records the result. Replaying the same
request/payload recovers the original run without debit; changed payload rejects.
Origin state pointers never transfer a token to another state. Historical replay
can report an original outcome after deadline, but cannot dispatch.

The two databases are not an atomic distributed transaction. Crash after reserve
but before an authoritative local link is **uncertain**. Missing origin state,
expired execution, or failed recovery blocks every fresh scope claim. A local
accepted/failed/invalidated run is a known Core terminal outcome; it is not
evidence that an unrelated external process stopped. Hosts must reconcile their
own backend before treating its outcome as known. No reservation is refunded.

Repeated claims of the same immutable `(source, external_key)` work identity
consume a retry repair unit even when local task IDs/state directories differ.
Within one live run, call `scope_repair` **before** a distinct repair round:
fields are `run_id`, private `token`, stable `repair_id`, and bounded `reason`.
Reusing its ID/payload is idempotent; changing its payload rejects. A retry claim
already charges its own repair unit, so do not manually charge that same retry.
Independent in-run repairs are separate units. This API cannot detect arbitrary
same-user file edits; the portable Skill and owner workflow supply that discipline.
Counts and deadline never decrease across failed claims, stop/restart or states.

An owner may close a positively diagnosed **not-started** uncertain reservation
using the separate module operation `resolve-not-started`, with an external exact
receipt file and SHA. The receipt must have exactly `scope_id`, `request_id`,
`origin_state`, `outcome:"not_started"`, and `reason`. An existing actual Core link
rejects that diagnosis. It preserves every spent counter and deadline. This is
owner authority, not a Core/MCP worker tool or a way to declare acceptance.

## Validation limits

### MCP compatibility contract

`agent_workflow.mcp.FIELDS` retains the original 27 tool names and exact input
schemas from public baseline `a4e22f6e2f4ca86d1709a7d69102633b1381c94c`.
The machine-readable snapshot is `tests/packaging/legacy_mcp_fields.json`.
`SCOPE_FIELDS` separately declares `scope_status`, `scope_preflight`, and
`scope_repair`. `tool_fields(engineering_scope=True)` returns a fresh merged
catalog; the default catalog stays at 27 even after a scoped instance runs.
The original portable host-readiness assertion remains unchanged.

Only scoped `claim` adds mandatory string fields `task_id` and `request_id` to
mandatory `worker`. The default `claim` keeps mandatory `worker` and optional
`task_id`, with no `request_id` property. The other 26 existing schemas are
identical in both catalogs. All schemas reject additional properties.

| Optional tool | String arguments | Authority and effects |
| --- | --- | --- |
| `scope_status` | none | Reads/reconciles existing owner ledger; no claim, repair charge, project command, policy change or model start. Origin recovery can append reconciliation events. |
| `scope_preflight` | none | Checks frozen executable identity before bounded capability probes. Does not run project test argv, claim work, charge repair units, change policy, or establish provider readiness. |
| `scope_repair` | required `run_id`, `token`, `repair_id`, `reason` | Requires an active owned run and unexpired owner deadline. Charges one repair unit; identical key/payload replays without debit. Conflicts and exhaustion reject. Never increases caps, changes deadline, refunds spend, or starts a backend. |

Without an initialized owner scope, these three tools are absent and calls return
JSON-RPC `-32602` (unknown tool). Missing/extra/wrong-type arguments return
`-32602` before Core dispatch. Validly shaped requests rejected by Core return
MCP `isError:true`; CLI returns `ok:false`. No worker MCP operation initializes,
replaces or relaxes owner policy; owner initialization and uncertain-outcome
receipts remain separate local owner actions.

`tests/packaging/mcp_contract.py` compares every legacy name/schema against the
snapshot, checks all 27 adapter routes and malformed-input rejection, and uses
real stdio/Core processes for scoped claim, status/preflight parity, private-token
repair authorization, duplicate/conflicting IDs, budget exhaustion and fixed
deadline. Its adapter-only fixture proves routing, not all underlying business
semantics; the complete portable 55-contract gate supplies broader behavior
coverage. Run it from source and an isolated installed wheel with `--installed`.

`tests/packaging/engineering_scope.py` exercises real Core/SQLite states, actual
concurrent local callers, CLI/MCP stdio parity, cumulative limits and real wall
expiry. Lost replies/digest delay use explicit local fault injection. A native
Node-version fixture verifies capability refusal without running test argv; it
does not claim a real Node16 installation. No external model, host wake source,
long-term unattended worker or hostile same-UID isolation is proved.
