# Local failure evidence

`agent_workflow.diagnostics` retains a bounded JSON projection before disposing of an owned failing fixture. It exports terminal timing and exit categories, matched PID identities, log hashes and known protocol event names, typed Core lease/state observations, Git HEADs and hashed dirty paths. It excludes raw logs, databases, prompts, tokens, arbitrary event bodies and error text. Missing or malformed evidence marks the record incomplete; it does not verify a process identity.

```python
from agent_workflow.diagnostics import retain_failure, run_fixture

# Read existing owned evidence without initializing a controller.
result = retain_failure(fixture_root, output_root, error=error,
                        initial_git={str(project): initial_head})

# A callback receives a private temporary directory. A failure commits one
# sanitized JSON record before cleanup; success cleans up without an artifact.
result = run_fixture(callback, output_root)
```

The defaults allow 64 eligible evidence reads and 262144 output bytes. Traversal has a separate fixed ceiling, SQLite projections have row and execution limits, and file/log reads have size limits. Exhausted limits reject without partial output. Roots cannot overlap. Symlink roots, descendant symlinks and escaped initial Git references reject; stored log references can only match the known call-ID log under that Runner's logs directory. Temporary platform aliases such as macOS `/var` are canonicalized. JSON artifacts have unique names and never replace earlier records.

Run the original local native target with its original assertions and two-second call budget:

```sh
python3 -B -m agent_workflow.diagnostics /path/to/trusted /path/to/source /path/to/output --delay-seconds 3
```

Run from the controller source directory (or set `PYTHONPATH` to it). An explicit delay affects only the disposable native worker. The command returns zero when the original assertions pass and one when they fail; a failure result points to the sanitized artifact and its SHA256. Success produces no failure artifact. The result also binds the executed diagnostic module, subject source and trusted target/common/native backend hashes, and reports whether those sources stayed unchanged.

These actors are native protocol fixtures, with zero provider calls and zero successful model turns. Injecting a delay demonstrates a timeout mechanism; it does not establish the cause of historical failures whose inner evidence was deleted. No historical evidence, original test, controller budget or owner policy is changed by this wrapper. The wrapper captures a cycle result before the original Git HEAD assertion and shares `run_fixture`'s cleanup lifecycle. It is synchronous local execution, not a future wake or ongoing model dispatch.

Cleanup also removes owned read-only directory snapshots. It restores owner permissions only on verified real directories below the exact private root, uses descriptor-relative no-follow operations, and unlinks symlinks without touching their targets. Traversal and depth are bounded. Every fixture result includes `cleanup_complete`; an unremovable tree reports `passed: false`, `cleanup_complete: false` and a sanitized `cleanup_error` dictionary. A previously committed failure artifact remains accessible and unchanged. An incomplete cleanup leaves a private local remnant rather than exporting raw evidence, and never reports a passing fixture. Cleanup does not change frozen controllers, output permissions, parent directories or unrelated files.

## Semantic and deadline gates

`budget.call_seconds` is a positive owner-selected ceiling no greater than the wall budget; it is not a universal two-second product SLA. The shipped Runner and Evolution examples use 180 and 360 seconds. The original native target helper chose two seconds. A naturally observed worker timeout prevented its first output/claim and CAS integration; the pre-protocol cause and historical failure identity remain unknown.

A separately reviewed owner policy splits these concerns: the CAS semantic gate uses a ten-second disposable fixture profile while executing all 12 original target assertions unchanged, including real repair, machine verification, independent native review, receipt, local CAS and call accounting. A separate two-second negative gate exercises actual timeout, matched PID/guardian/start identity, group termination, no claim/reviewer/receipt/CAS, and complete cleanup. Its deliberate three-second delay is controlled deadline evidence, distinct from the natural zero-output failure. Production budgets, original trusted files and initialized policies remain unchanged. The failed earlier verification remains failed; new-owner machine verification and independent acceptance are required before deployment.
