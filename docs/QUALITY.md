# Optional project quality guard

Core initialization may freeze a `quality_policy` descriptor with exactly `path`
and `sha256`. Its bounded JSON file must be an absolute regular file outside the
editable checkout. Reads reject symlinks and special files, use nonblocking open
and enforce a one MiB bound before and during regular-file reads. Policy changes require separately reviewed state; workers have
no policy or assessment write endpoint. Without the descriptor existing lifecycle
behavior and the 27 CLI/MCP actions remain unchanged.

Policy fields are `schema_version` (1 or 2), unique nonempty `goals` and
`trusted_reviewers`, absolute external `assessment_path`, positive integer
`max_assessment_age_seconds`, `allowed_advisories` (complexity, documentation,
debt), boolean `ui_required`, and `bootstrap` containing an exact `external_key`
and `source_sha256`. Strict JSON rejects duplicate members and extra fields.

The privileged host writes a current independent assessment with exactly
`schema_version`, `source_sha256`, `proof_run_id`, `reviewer`, `created_at`,
`dimensions`, and `candidates`. Dimensions are goal_alignment, complexity,
architecture, safety, documentation, debt, and ui; each contains status, reason,
and evidence. Status is pass, block, advisory, unknown, or not_applicable (ui
only). Passing, blocking and advisory judgments require bounded evidence strings.
A non-UI project uses ui not_applicable. These inert references are independent
reviewer assertions, not machine proof of semantic truth.

Candidates contain task_id, positive task_revision, task_payload_sha256, goal_id,
finding_id, kind (feature or repair), rationale, and evidence. Payload SHA256 is
canonical compact sorted-key ensure_ascii=False JSON of source, external_key,
title, body, revision. Core separately resolves the actual task ID. The reviewer
must be allowlisted and different from the actual proof worker, selected worker,
and active implementation workers. This privileged cooperative host boundary is
not OS isolation against another same-user process.

Feature admission requires current passing Core proof, complete independent
assessment and a novel finding. Repair may use actual failed or stale verifier
history with a current independent repair assessment, or a blocking semantic
judgment backed by evidence. Core reuses its original proof reader and retained
verification events; no duplicated verifier or universal quality score is added.
Fresh proof and independent assessment of the actual run are required for local
acceptance. The bootstrap exception permits only evidence capture at its exact
frozen key/source; change, verification and acceptance reject source drift.

Claim atomically records quality_admitted in existing events. Source plus finding
identity prevents aliases across tasks, goal names and rewritten assessments;
same-task retry retains the existing attempt allowance. Every owned mutation
checks the pinned policy identity. Assessment source is not pinned on repair
mutations, since repairing necessarily changes source.

For schema1, read-only next_step appends exactly disposition, reason, resume_conditions,
source_sha256, policy_sha256, assessment_sha256, next_task_id, claim_allowed,
request_id and host_request. Five dispositions are continue, repair_or_tidy,
wait_review, wait_owner and stop_no_value. Fixed reason codes and concrete recovery conditions
expose no raw assessment, roles, logs, task body, policy path or token. Stable
request IDs bind input identities rather than polling time. claim_allowed is
quality eligibility; actual claim still checks leases, attempts and controller.

Schema1 `stop_no_value` means only that the current reviewed candidates are exhausted.
It does not mean research is complete or the overall product has no remaining work.
This scoped pause persists as a projection of the current source-bound assessment
and immutable admission history across restarts. A new current independently
justified finding can reopen work under unchanged resources. The host reconciles
claims and reviews before dispatch and owns fulfillment. This module launches no
agent, creates no scheduler/database, and does not change the optional
Continuation/Evolution idle/no-gain behavior or confer publication authority.


## Explicit owner goal coverage (schema2)

An owner may freeze schema2 using the same policy keys. Its assessment must also
use schema2 and add `goal_coverage`, covering every pinned policy goal exactly
once. Each row contains exactly `goal_id`, `status`, `reason`, `evidence`, and
`task_id`. Reason and at least one evidence reference are required. Status is
`actionable`, `needs_research`, `needs_capability`, `needs_approval`,
`needs_validation`, or `achieved`. These are independent reviewer assertions,
not authenticated permission or machine proof. Unknown, duplicate, missing or
extra goals, schema downgrade, and malformed coverage fail closed.

An actionable row can have no planned task yet (`task_id:null`, no candidate),
or bind exactly one same-goal candidate with the existing immutable task ID,
revision and payload hash. Every candidate must have this actionable mapping.
Other statuses require null task_id and no candidate. Current eligible work still
uses existing Core claim, admission, lease, attempt and verifier gates even when
another goal needs capability or approval. Completing and accepting an exact
version task does not require unrelated goals to be achieved.

After policy loading, every schema2 quality projection includes
`scope:"owner_goals"`, sanitized `goal_statuses` (goal_id and status only), and
`all_owner_goals_assessed_achieved`. The last field defaults to false on incomplete,
stale or invalid assessment and pending machine review. Raw reviewer reasons,
evidence and identities are not returned. Fixed goal-specific recovery conditions
explain the next host action without granting permission.

With no eligible task, unfinished coverage remains explicit: unplanned actionable
work yields `wait_review/goal_planning_required`; research and validation yield
`wait_review/goal_research_required` or `goal_validation_required`; capability
and approval yield `wait_owner/goal_capability_required` or
`goal_approval_required`. A mapped task that cannot proceed yields
`wait_owner/goal_task_unavailable`. None refresh authorization, attempts,
permissions, budgets, or deadlines. An allowed debt advisory cannot erase an
unfinished goal. No synthetic task is created just to continue.

Only fresh independent coverage marking every pinned goal achieved, together
with current passing Core proof and complete dimensions, permits
`stop_no_value/all_owner_goals_assessed_achieved`. This closes only the explicit
owner goal scope at that version: it does not prove unlisted goals, model/backend
readiness, future wakeups, or multi-day reliability. Task `done`, green tests,
and a completed research theme cannot establish overall goal completion.

Historical bootstrap/MVP plans, including a stop after two dogfooding tasks,
record earlier delivery boundaries. They do not replace current pinned owner
goals or justify stopping an ongoing authorized iteration. The host must retain
unfinished goals, investigate authorized local next steps, and report exact
capability, permission, research, or validation blockers. Existing schema1 callers
remain byte-shape compatible and must report their narrower scope honestly.
