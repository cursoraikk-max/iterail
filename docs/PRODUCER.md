# One-shot owner-bound local discovery

`producer_discover` is one bounded, explicit scan of a fixed owner registry. It creates candidate data, runs trusted local checks, and admits only a real typed failure for the exact stable source. It does not start AI agents or a service. An existing worker can pull an admitted task through the usual CLI or MCP.

The owner prepares a policy outside the editable repository and freezes its absolute path and SHA in initial project configuration:

```json
"producer_policy": {"path": "/absolute/owner/producer.json", "sha256": "SHA256_OF_EXACT_FILE_BYTES"}
```

Without this optional descriptor the producer is disabled. `producer_init` takes no policy argument. Existing state cannot be reconfigured; create a separately reviewed state for policy changes. Registry executables, scripts and sibling Python helpers are hashed at initialization and checked again. These are cooperative same-user audit controls, not a sandbox.

Copy [producer.policy.example.json](../producer.policy.example.json), edit its executable/script placeholders, and place the owner policy and a reviewed copy of [examples/owner_check.py](../examples/owner_check.py) outside the editable checkout. The sample only reads source and optional `example-status.txt` data; it performs no network or publication. A missing status file passes. A file containing exactly `needs-fix` plus newline gives an illustrative failure; it is not evidence of a real product defect. Do not use this illustrative check as development acceptance. Compute SHA from the final policy bytes before initial `init`; the sample does not silently target a private checkout.

```sh
python3 -m agent_workflow --state /absolute/state producer_init
python3 -m agent_workflow --state /absolute/state producer_discover --args '{"limit":"3"}'
python3 -m agent_workflow --state /absolute/state queue_status
```

A trusted check prints one JSON object with `id`, `passed`, `classification`, `source_stable`, `exactsourceSHA` and `evidence`. Exit 1 admits only `passed:false`, `classification:product_defect`, stable source matching both before/after digests and the registry ID. Exit 0 is a passing check; timeout, infrastructure errors, invalid JSON and source drift do not create tasks. The registry's reproduction acceptance is fixed. An admitted task separately binds the owner argv, registry/policy hashes and required post-fix exit 0 in server-owned SQLite rows. Core verification appends this typed check; passing project base tests cannot accept an unresolved owner defect. Task text never supplies acceptance commands.

Repeated explicit owner discovery keeps valid passing checks and admitted findings
cached for the same source and registry. A rejected owner-registry observation may
retry after restart only when its retained canonical evidence proves integer exit
2 with `passed:false`, `classification:infrastructure_error`, the exact current
stable source/check ID, unchanged owner policy and fixed argv, and untruncated
output. This reopens the same candidate for a bounded normal scan; it does not
admit a task from the infrastructure error. A recovered check must independently
reproduce the ordinary product-defect contract before admission.

Unknown errors, malformed reports, source drift and arbitrary offered rejection
data remain cached. Retry respects the original pending queue ceiling and the
per-cycle processed limit; a full queue records deferral. Original failed
reproduction events, evidence hashes and content-addressed logs remain historical
when a later observation replaces the candidate's latest evidence. No retry
changes checker/policy bindings, permissions or budgets, refunds resources,
starts a model, or creates an automatic polling service.

`producer_offer` accepts `source`, `external_key`, `check_id`, `title`, optional `body` and `source_ref`; `producer_scan` processes offered data explicitly. Allowed source labels are `failed_log`, `unresolved_review` and `owner_check`. Public labels and references remain claimed/unverified: no arbitrary reference file is read, no label grants priority or authority. Repeated immutable offer keys deduplicate; conflicting payloads reject. Actual machine findings deduplicate independently by check ID, exact source and registry SHA, so feedback aliases cannot create duplicate work. Source revisions remain separate facts rather than extra parent goals or delivery successes.

Pending candidates have equal source weight, server-time aging buckets and FIFO tie breaks. No incoming feedback can choose a score or clock. Queue bounds, one checkout lease and WIP 1 apply across CLI/MCP. Each `limit` is a hard count of processed reproductions, including rejection, blockage and deduplication; unprocessed candidates remain pending. The owner `max_new_per_cycle` caps this count. There is no automatic perpetual polling or policy adjustment.

Events preserve actual argv, exit, source, canonical evidence SHA and content-addressed raw stdout/stderr prefix references under `state/producer-logs`. Prefixes are capped with total-byte/truncation metadata; they are not necessarily full logs. Source labels and task bodies stay inert even after a finding is reproduced. Reviewers and new attempts still need fresh version-bound acceptance.
