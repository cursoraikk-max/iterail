# Owner-bound retrospective context replay

`agent_workflow.strategy.StrategyStore` is a small local ledger for comparing
SHA-bound context retrieval artifacts. It never changes Core tasks, acceptance,
permissions, budgets, or the runner. It makes no model or financial benefit
claim. Historical machine receipts/logs are rationale; the measured artifacts
come from a fixed deterministic local evaluator. This is retrospective replay,
not a prospective model experiment.

The owner supplies an immutable policy with the fixed parent goal, complete
ordered cohort, constraints, source version, evaluator identity, historical
references, artifact registry, and 10% minimum byte reduction. Proposals must
repeat those exact fields. A hypothesis is inert text; extra fields, caller
scores, changed rules, dropped goals, and unregistered references reject.

The standalone CLI requires a policy file outside the checkout and store:

```sh
python3 -m agent_workflow.strategy --store /absolute/store \
  --policy /absolute/owner-policy.json --policy-sha256 EXACT_FILE_SHA \
  preregister --proposal /absolute/proposal.json
python3 -m agent_workflow.strategy --store /absolute/store \
  --policy /absolute/owner-policy.json --policy-sha256 EXACT_FILE_SHA \
  evaluate --id REGISTERED_ID
python3 -m agent_workflow.strategy --store /absolute/store \
  --policy /absolute/owner-policy.json --policy-sha256 EXACT_FILE_SHA history
```

Registration persists metadata before evaluation reads the selected sealed
artifact bytes. Fresh store construction and registration do not read result
contents. Evaluation verifies the actual historical and selected artifact SHAs,
the exact source/evaluator/cohort, every goal exactly once, known byte/coverage
metrics, and all cost fields. After evaluation, restart and later operations
revalidate already-evaluated evidence; changed/missing artifacts reject.
Tokens, money, review/cleanup costs and other unavailable costs remain null and
produce `cost_status:unknown`; missing elapsed time or cost keys reject.

Retain requires every candidate goal to succeed, no goal to regress in
coverage, and aggregate context bytes to fall by at least 10%. Otherwise the
decision is rollback. Two distinct consecutive no-gain comparisons stop the
store; there is no reset endpoint. Retain resets the consecutive counter.
All reports preserve the original denominator, both complete cost records,
source and policy hashes, and evidence references, with `model_benefit:false`.
Renaming an already-registered artifact pair rejects. Repeating the same exact
registration/evaluation is idempotent and adds no history or no-gain count.

POSIX file locks serialize registration and evaluation across processes.
The store fsyncs its append ledger, atomically replaces binding/head files,
and pins ledger length, sequence and last hash. It binds the canonical policy,
the CLI's exact owner-policy file SHA, and the implementation file SHA. Missing,
truncated, malformed or changed history fails closed, including on restart.
Initialization requires a genuinely new store directory. An existing directory
with missing control files rejects, even if its lock or all its files were
deleted; use a separately authorized new directory rather than resetting history.
An interrupted multi-file write is left for explicit owner diagnosis; it is
never silently repaired into a successful decision. These are cooperative
same-user audit controls, not OS isolation or protection from an owner who
deletes or rewrites all evidence.

All JSON readers reject duplicate members and nonfinite constants; typed
integer fields reject booleans. Relative evidence paths cannot traverse outside
the owner root or follow evidence symlinks. Native fixture regressions include
the real unchanged historical receipt as a portable rationale fixture. They
prove local ledger behavior, not external AI integration or model gains.
