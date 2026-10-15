# Local project evolution

This optional foreground supervisor coordinates bounded engineering work. Task state, commands, verified evidence and local integration remain in the existing Core/Runner workflow. MCP is a pull interface; it does not launch agents by itself.

Use `evolution.example.json` as an owner-reviewed template, replacing every absolute placeholder with project-local executables and an independent clean Git checkout. Tests/checks/health scripts and configuration belong outside the editable checkout and are SHA-bound. A check emits the strict exact-source finding contract documented in PRODUCER.md. Choose fixed allowlisted source paths; preserve tests and owner policy. `provider_authorized:false` intentionally rejects provider initialization until an explicit owner grant is recorded. Do not flip it merely to run an example. Calls, seconds and deadline are ceilings, not instructions to consume them.

ProjectSpec distinguishes `target` from `controller`. Both can carry factual purpose, goals, non-goals and dependency/capability `needs`. An ordinary target uses the shared immutable tool package and any owner-approved language, backend adapter and test commands. It requires no Python application or controller files. Target delivery integrates code into its local Git checkout without deploying a controller. A controller project additionally names exact `self_update` source paths; stable supervisor, guardian, governor, tests, policy and Skill are protected. Development workers cannot change these controls.

After independently validating the installed controller, initialize one owner epoch:

```sh
python3 -m agent_workflow.evolution --state /absolute/new-owner-state --config /absolute/owner-config.json init
python3 /absolute/new-owner-state/start.py --state /absolute/new-owner-state --max-cycles 3 start
python3 /absolute/new-owner-state/start.py --state /absolute/new-owner-state status
python3 /absolute/new-owner-state/start.py --state /absolute/new-owner-state stop
python3 /absolute/new-owner-state/start.py --state /absolute/new-owner-state resume
```

Always use the generated SHA-bound `start.py` after initialization. It loads the stable package independently of editable candidate source. The active release executes discovery and one worker attempt with its exact fingerprint. Before dispatch, the owner reserves a worker/reviewer pair; actual launches are counted separately. Failed workers do not spend reviewer calls. Crash reservations stay spent; resume never replenishes calls or extends the initial deadline. Auth/quota, recovery and integrity blocks remain persistent. Idle cycles use bounded backoff, and an empty registry does not create work. Status is a long-running ledger query, not a success claim.

Confirmed pre-session app-server initialization EPERM produces the separate
`infrastructure_blocked` category. Its first occurrence durably blocks and stops
the owner after the terminal child result is retained. A later cycle, restart or
ordinary resume cannot launch another worker or clear that block. Unknown backend
failures keep their existing classification and bounded retry behavior.

Status `accounting` reports confirmed `actual_backend_launches`,
`successful_model_turns`, `reviewer_launches` and
`unexecuted_reviewer_reservations`. Launches require matching terminal and PID
journals. A successful model turn additionally needs complete, untruncated,
SHA-bound raw output with a valid completed exec protocol and successful process
exit; exit zero or a completion string alone is insufficient. Reviewer reservations
count as known unexecuted only for finished pair grants with valid actual-use
evidence. `unknown_backend_launches`, `unknown_model_turns` and
`unknown_reviewer_reservations` retain uncertainty from missing, malformed or
incomplete evidence; zero confirmed observations never converts those unknowns
into a verified zero total. Charged `meta.calls` remains the original resource
ceiling accounting, independently of actual launches or completed turns.

Infrastructure recovery requires a supported, authorized runtime environment and
owner review of the retained error and drained journals. The status recovery
object explicitly denies automatic retry, reservation refunds and budget resets.
Resume preserves the block, original deadline and spent pair. Native simulated
backend fixtures verify this control behavior; they establish no external model
or app-server integration success and perform no IPC precheck.

Each accepted controller candidate needs exact failed baseline, fresh machine commands/exits/log hashes, an independent reviewer verdict, complete diff and local Git CAS receipt. The stable supervisor re-observes raw facts, requires drained journals, runs fixed health commands and atomically activates a new immutable release. Activation intent survives restart; rejected health retains the previous active release and records rejection. A stopped or corrupted owner history requires diagnosis, never deleting the ledger to gain a fresh budget. Same-user files are auditable and hash-fenced, not tamper-proof against a malicious OS administrator.

## Read-only Evolution metrics

`agent_workflow.metrics.evolution_summary(existing_owner)` accepts an existing
owner directory or its `evolution.sqlite` file. It opens owner, child Runner and
Core databases only through SQLite URI `mode=ro`, holds their read transactions
through the projection and closes every connection. It creates no state, runs no
subprocess, constructs no owner/runner, and changes no deadline, stop, policy,
reservation or evidence. Missing state rejects without initialization. Normal
SQLite WAL/SHM coordination can occur; the reader does not use `immutable=1`,
restore sidecars or bypass uncheckpointed facts.

The ordered original owner check IDs define `parent_goal_ids` and the fixed
registered denominator. Reservations, retries, copied results and releases add no
goals. `ledger.reserved_calls` preserves charged call slots;
`ledger.actual_calls` counts unique matching owner/child terminal and PID-backed
backend processes. Local probe/check/test processes and duplicated event/result
copies do not add backend calls. Missing process proof is represented by
`unknown_actual_calls` and findings rather than an invented launch total.

Historical `totals` and `by_release` success require the recorded canonical
receipt, actual accepted Core evidence, fixed machine argv and raw hashes,
independent reviewer artifact/protocol, local CAS and bound owner proof for that
source/release. A completion phase or self-score is insufficient. Missing,
malformed or escaped evidence produces unknown/incomplete results; evidence
references remain within owner state. Each accepted source is explicit under its
executing release. `current_source.verified_parent_goal_ids` includes only
receipts matching the observed current source, so historical acceptance does not
prove changed-source acceptance or current health.

`provenance.execution_mode` identifies native fixtures separately from provider
trials. `observations.completed_exec_turns` can include simulated native protocol;
`native_simulated_turns` keeps those separate from `successful_model_turns`.
Unverifiable protocol is `unknown_model_turns`, not successful model work.
Costs retain null amount/currency with status `unknown`; `model_benefit:false`
does not turn engineering fixtures into a claimed quality gain. The output
contains aggregates, identities and provenance, never raw prompts or tokens.

## Bounded verification and reviewed bootstrap recovery

A Core verification call reruns the entire fixed test bundle. Its aggregate window is bounded by the approved per-call ceiling; individual commands remain capped at 60 seconds. Admission reserves the separate worker, reviewer, individual-test, bundle and bookkeeping windows. A nonzero, interrupted or truncated Core process is a recovery failure before parsing its output; an empty response cannot become evidence of acceptance.

The operator-only `Supervisor.checkpoint_handoff(state, config, review_receipt, review_sha)` supports one narrow bootstrap recovery: the retained canonical aggregate-timeout incident, fully drained process journals, successful original individual machine checks, and a newly independently accepted exact-source Core run. It rejects unrelated blocks and missing or mismatched proof. Install that reviewed source in the original clean project before invoking the handoff. `checkpoint(state)` reads a hash-only ledger snapshot.

Recovery installs a unique reviewed stable closure in the same owner state and records a durable one-time intent. Original configuration, deadline, spent reservations, prior events, releases, failed candidates and immutable old closure remain historical. The old interrupted Core run is never accepted or reused. Replay cannot renew budget. This operator bootstrap repair is reported separately from later automatic controller improvements.

## Optional maintenance

Many projects need no lifecycle checks. For applicable dependencies, place an owner-approved `maintenance.example.json` outside the editable project; configure `project.maintenance` as `{"config":"/absolute/maintenance.json","sha256":"exact-file-SHA256"}`. Its ProjectSpec fields must match the supervisor's project metadata. The supervisor pins the policy, ticks its persistent scheduler within owner stop/deadline/budget gates, and records deduplicated proposals. The active release still has to reproduce a preconfigured fixed check before dispatch; source notices cannot create commands or alter authority.

The example watches the official Codex release API. `expected_current` is a canonical SHA256 of `{version,deprecated}` with sorted compact JSON, not a guessed latest model name. Available parsers are strict capability-manifest JSON, official GitHub release metadata and UTF-8 documentation digests. To watch model/API deprecations, configure the exact final official URL `https://developers.openai.com/api/docs/deprecations` with `text_digest`, baseline its actual current body hash and connect a trusted impact/capability check. A documentation change is a signal for investigation: this parser does not infer that a model is deprecated or better from prose. Approved hosts and exact paths are owner decisions. Responses are bounded, credential-free data; redirects, private addresses and unexpected authority fields reject.

The scheduler preserves absolute due times, last check, failure backoff/jitter, observed fingerprints, ignored versions and emitted findings across restart. Stop or exhausted budget prevents polling and preserves due work. No scheduler check calls a model, installs dependencies, publishes or opens an account. Lifecycle adaptation should test structured protocol, tool calls, streaming, context limits and quota behavior, then run a small approved canary and fixed health checks before local release. Increased spending, new provider/credentials or broader permissions require new authorization. Version changes alone do not establish quality or efficiency gains.

Native command adapters and injected maintenance responses test reliability; they are labeled fixtures. A public official read proves source access only. Report actual external worker/reviewer executions, automatic release transitions and their fingerprints separately.
