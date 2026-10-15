# Optional UI/UX investigations

UI/UX maintenance is opt-in. The project owner records intended users, key flows,
approved references, design tokens, viewports, accessibility requirements and a
reasoned review rubric. These standards are context, never execution authority.
Projects with `has_ui:false` return `not_applicable` without capture or tasks.

Copy [the policy sample](../uiux.policy.example.json) outside the editable project,
choose an absolute owner-controlled `artifact_root`, and SHA-256 pin that file.
Initialize a separately reviewed Core state with this optional descriptor:

```json
{"uiux_policy":{"path":"/absolute/owner/uiux.json","sha256":"EXACT_FILE_SHA256"}}
```

All existing project fields remain required. Omitting this descriptor preserves
the ordinary workflow: discovery/query return disabled `not_applicable` without
creating a Store or tasks, and initialization/evidence/review reject. The owner may also attach the
same descriptor as Evolution `project.uiux`; it cannot be set through task text.
The policy remains frozen after initialization. `uiux_init` takes the complete
policy as a JSON string and requires exact equality with the bound file.

Core, CLI and MCP share one local Store. `uiux_discover` takes no source argument:
it hashes the configured repository, respects the frozen cadence and imports due
reviews as actual inert local Core tasks with stable deduplication keys. Source
changes create fresh review runs. Record, review and query recheck the actual
source; stale evidence cannot export new tasks even without another discovery.
Manual discovery runs when an authorized caller invokes it. The optional
Supervisor tick below also evaluates cadence while that Supervisor is running.
No OS daemon or external scheduler is installed, and neither path creates a
trusted browser or capture executor.

```sh
python3 -m agent_workflow --state /absolute/state uiux_discover
python3 -m agent_workflow --state /absolute/state uiux_query
```

## Automatic discovery in the existing Supervisor

An owner-bound Evolution `project.uiux` enables automatic discovery through the
existing `Supervisor.cycle()` → `maintenance_tick()` route. It runs only while
the approved Supervisor is running. The original owner stop, persistent block,
deadline and remaining worker/reviewer pair admission gates apply before the
tick. A closed gate pauses discovery and imports without advancing due time or
failure count; the tick cannot clear a stop or restore spent budget.

The timer checks persisted `next_due` before doing source, discovery or task
work. When due, it hashes the actual configured repository and persists a new
periodic audit, even if an earlier audit is still pending. Source changes are
checked on this automatic cadence; early ticks do no source work. A successful
tick schedules the next due time at current time plus the owner interval.
Projects with `has_ui:false` return `not_applicable` and create no scheduler,
Store, Core task or state-directory files.

UI tasks live in the designated Core state `/absolute/owner/uiux-core`, with
`tasks.sqlite`, the persistent `uiux-maintenance.sqlite` timer ledger and the
shared `uiux/` evidence Store. `uiux-maintenance.identity.json` pins the owner
binding, and `uiux-maintenance.lock` serializes the whole tick across instances.
This optional scheduler uses POSIX `flock`; native checks cover macOS. Windows
scheduler behavior remains unverified, independently of CLI/MCP portability.
The Core derives the original owner's local test
commands, acceptance, permissions and bounded budgets. Use the approved
controller to inspect and claim these actual queued tasks:

```sh
python3 -m agent_workflow --state /absolute/owner/uiux-core status
python3 -m agent_workflow --state /absolute/owner/uiux-core claim --args '{"worker":"approved-ui-investigator"}'
python3 -m agent_workflow --state /absolute/owner/uiux-core uiux_query
```

The tick imports inert audit proposals with stable keys and `commands:[]` /
`permissions:{}`; it does not launch a backend or claim the tasks. These tasks
remain available for a separately authorized worker under the ordinary Core
lifecycle. Core code acceptance still leaves production visual verification
false.

Import failures retain the exact pending key/body before a retry. Persistent
backoff is `min(interval_seconds, 30 * 2**min(failure_count - 1, 10))` seconds.
Restart retries that pending intent before creating another periodic audit;
successful retry clears the failure count. The live gate is checked again after
discovery and around import. A late pause retains the intent without counting a
failure or advancing due time, including when the import already completed;
replay uses the same deduplication key.

When source changes before a pending retry, the exact old proposal is archived
with reason `source_changed` and exposed in scheduler status `stale_intents`.
The next due check creates a fresh source-bound audit key; it cannot export the
old intent against changed source. Immutable Store, scheduler and pending-intent
identity or source-scope checks raise a typed `UIUXIntegrityError` before any
stale intent is archived or cleared. Corrupt owner data is retained for diagnosis;
blocked checks preserve retry counters, due time and the pending proposal.
Ordinary import callback `RuntimeError` and `ValueError` still use the durable
same-intent backoff above. Invalid owner policy or scheduler history
returns `blocked` with failure class `integrity`, rather than inventing retry
backoff. Enabled work records a bounded `uiux_maintenance_checked` result event
in the owner ledger; `not_due` and `not_applicable` checks add no such event.
Scheduler status is metadata-only; native clock and callback fixtures prove
bookkeeping, never actual browser capture or visual quality.

`uiux_evidence` takes `run_id`, `stage` (`screenshot`, `interaction` or
`difference`) and an `evidence` JSON string. Its packet is:

```json
{"id":"screen-small","kind":"submitted","source_sha256":"CURRENT_RUN_SOURCE_SHA256","actor":"evidence-author","artifact":{"path":"/absolute/owner/artifacts/small.png","sha256":"EXACT_ARTIFACT_SHA256","viewport_id":"small","flow_id":"home"}}
```

Only `submitted` and `fixture` evidence are supported. Files must be regular,
bounded, SHA-matching files within the approved artifact root. Screenshots must
be static PNGs with the exact owner viewport dimensions. Standard PNG color
types and sample depths, including Adam7 interlace, are supported. Admission
checks chunk CRCs/order, palette requirements, scanline filters and a complete
single zlib image stream with the exact decoded scanline byte count. Inflation
is streamed within viewport-derived bounds and a 64 MiB decoded safety ceiling;
larger images and animated PNG chunks are unsupported. Invalid images are
rejected before evidence is written. This validates submitted file structure;
`production_visual_verified` remains false. Interaction JSON contains
`flow_id` and `steps` entries with `step`, `selector` and `outcome` (`passed` or
`failed`) matching the owner flow. Difference JSON is an investigation signal.
Missing viewports, interactions, browser capability or trustworthy capture remain
missing; native fixtures do not prove a production browser was exercised.

`uiux_review` takes `run_id`, `reviewer`, `rubric` JSON string, `verdict`
(`needs_improvement` or `acceptable`), `rationale` and `findings` JSON string.
The reviewer must differ from every evidence actor. Each rubric entry includes
`id`, `score` (0–4), a concrete `reason` and known `evidence_ids`. Each finding
includes `id`, `title`, `description`, `evidence_ids`, `major_brand_change` and
`brand_change_id` (string or null). Complete coverage and reasons are required;
scores, screenshot differences and reviewer assertions alone prove no quality.

Every v1 response retains `production_visual_verified:false`: there is no
trusted visual capture executor. A complete reasoned review may export minor
investigation proposals as actual Core tasks, with `commands:[]` and
`permissions:{}`. These tasks need the usual fresh claim, source change, machine
verification and independent review. Ordinary Core acceptance establishes code
workflow evidence, not production UI quality. Unapproved major branding findings
remain blocked proposals and never reach the task importer. An exact change ID
must already exist in the owner's frozen `approved_brand_changes` list.

This feature never launches a browser, starts a model, fetches references or
publishes work. Reference URLs are inert owner context.
Inspect supplied screenshots and interactions using separately authorized tools;
retain their provenance and limitations instead of asserting visual success.
