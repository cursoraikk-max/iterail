# Optional host research ledger

Research is a separate host-invoked module/CLI sharing Core's existing SQLite
state outside the editable checkout. It persists fixed public research requests, quarantined observations and
independent receipts; it never searches, downloads, follows redirects, renders
HTML/Markdown, executes snippets or launches models. Core's 27 CLI/MCP actions,
frozen permissions, tests, leases and quality admission remain unchanged.

```sh
python3 -B -m agent_workflow.research --state /absolute/core-state --policy /absolute/descriptor.json init --args '{}'
python3 -B -m agent_workflow.research --state /absolute/core-state request --args '{"topic_id":"security"}'
python3 -B -m agent_workflow.research --state /absolute/core-state query --args '{}'
```

Library entry is `Research(engine, policy=None)` with request(topic_id),
observe(request_id, observation), review(receipt_path), convert(review_id) and
query(). Initialization alone accepts the descriptor `{path,sha256}` and pins it
outside the editable checkout. Reopen/query do not migrate research tables.
The query CLI opens existing SQLite state read-only without an Engine constructor,
Core reconciliation, counter updates or events. Errors are fixed bounded codes.

The strict policy contains exactly schema_version:1, goals, topics,
trusted_reviewers, review_directory, caps and max_age_seconds. Topics contain
owner-approved goal_id, fixed query, exact lower-case HTTPS hosts and relative
scope_paths. Caps contain max_requests, max_observations_per_request,
max_excerpt_bytes, max_total_bytes, max_reviews and max_mutations. Goals,
reviewers, topics and each topic list have at most 16 entries; unique record caps
are at most 128, excerpt cap 8192 UTF8 bytes, total excerpt cap 1048576 bytes,
mutation cap 4096 and evidence age at most 86400 seconds. All counters are
positive exact integers; bool is rejected. Every mutation after a valid policy
check spends an attempt, including malformed, duplicate and rejected calls.
Queries, reopen and init replay spend none; no counter or Core budget is refunded.

All file JSON and structured CLI input is bounded to 65536 UTF8 bytes. Duplicate
members, nonfinite numbers and extra fields reject. External policy and receipt
reads reject symlinks in any component, directories and special files; component
opens use nofollow and nonblocking flags, fstat and bounded max+1 reads. Receipts
must reside under the frozen external review_directory. File/hash controls give
cooperative-host auditability, not authentication against a malicious same-user
owner. Use physical absolute paths on systems where aliases are symlinks.

An available observation has exactly status:'available', url, captured_at,
capture_tool, collector, version, locator, license, complete and excerpt. Version
and license may explicitly be null. Capture time must be finite, within owner
max_age_seconds and at most 60 seconds future. Only approved parsed HTTPS DNS
hosts are accepted, with no userinfo, IP/local origin, non-443 explicit port,
query, fragment, backslash or control bytes. The module never dereferences URLs;
transport/DNS/redirect protection belongs to the host. Tool, origin, version and
license remain supplied assertions, not proof of authenticity or currentness.
Publisher times and redirect metadata are not claimed by this minimal schema.

Unavailable observations have exactly status:'unavailable', captured_at,
capture_tool, collector and reason:'host_search_unavailable'. This is durable
absence of search capability, never fabricated evidence or installation authority.
Available excerpts stay only in quarantine, separate from public metadata and
Core task bodies. Exact excerpt SHA is computed by the module. Canonical
observation SHA includes provenance and excerpt SHA; identity excludes capture
time so recapture time alone returns the first immutable observation.

A privileged independent receipt contains exactly schema_version, request_id,
observations, reviewer, reviewed_at, source_sha256, policy_sha256, goal_id,
finding_id, verdict, candidate and candidate_sha256. Observation entries have
exact observation_id/observation_sha256. The reviewer is allowlisted, distinct
from every collector and every actual Core run worker. Review binds current
source/controller/request/policy, fresh exact observation metadata/content and
the canonical candidate hash. Reject/discuss require null candidate/hash and
cannot convert. Admit requires available complete captures. Unknown version or
license can support a reviewed idea, but provides no permission to copy code.

Admit candidates contain only title, claim, inference and approved scope_paths;
commands, dependencies, code, raw evidence and permission fields reject. Candidate
prose is independently authored; sanitation/relevance remain reviewer judgment,
not a keyword classifier or semantic truth inferred from hashing. Normal security
examples and malicious/encoded instructions are both inert quarantined data.
Finite native fixtures cannot prove absence of all prompt injection or secret
leakage. Hosts must never put credentials/private source into public queries.

Conversion takes one exact review_id, revalidates its immutable receipt and
reserves source+finding identity independent of goal/URL/reviewer aliases. The
external_key is research: plus SHA256 of canonical source_sha256/finding_id JSON.
Core import owns its original transaction; acknowledgment records actual task
ID/revision/payload hash afterward. A crash after actual import reconciles the
same immutable task without duplicate SQL or hiding a committed import. Conflicts
stay blocked. Source drift retains imported inert history and blocks current
conversion. Same-finding aliases preserve the original task payload.

The task body contains only the fixed reviewed_research_candidate type, current
source, goal/finding, independently authored claim/inference/scopes and immutable
evidence references (IDs, excerpt SHA, approved URL, version and locator). Raw
excerpts, actors, receipt/policy paths and executable fields never propagate.
Research conversion grants neither a claim nor machine proof: independent current
quality assessment and ordinary Core fencing still govern work and acceptance.
Public query returns bounded IDs/hashes/status, fixed queries and usage totals,
with no raw excerpts, reviewer identities or candidate prose. It launches no work.
Continuation integration and periodic quality review are separate work.
