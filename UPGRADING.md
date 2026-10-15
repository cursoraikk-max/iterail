# Upgrading and rollback — local alpha

There is no automatic database migration or compatibility promise for this alpha.
Do not point a new controller at an active old run and expect its token to work:
owned mutations are fenced by the original controller package digest.

1. Stop dispatch using the owner's explicit existing control; preserve original
   stop flag, start/deadline, requests spent, leases, attempts and pending intents.
   Inspect status/next_step and actual in-flight processes before copying state.
2. With processes quiescent, make a private consistent SQLite backup (including
   relevant companion ledgers and evidence, or use SQLite's backup API). A raw
   live main-file copy can omit WAL transactions. Store original frozen config,
   controller runtime, Git source and hashes alongside the backup. Protect tokens.
3. Validate the new exact-source distribution in an isolated environment and use
   only an explicitly authorized upgrade/activation path with fresh evidence.
   New source invalidates old acceptance. A package install alone does not migrate
   policy, accept work, activate a runner or grant credentials.
4. For rollback, preserve failed evidence and stop dispatch first. Roll back code/package only after owner review. Keep current authoritative
   ledgers and all spent budgets, stops, deadlines and history; do not restore an
   older operational DB to erase spent work. Backups are protected disaster-recovery
   evidence, not permission to roll back counters. If compatibility fails, keep
   dispatch stopped and diagnose/reconcile without restoring stale authority.

Old controllers/artifacts remain historical. Do not mix run tokens from one state
or controller with another. Re-run current tests and independent review after
changes; do not claim old proof is current. Schema changes require separately
reviewed migration and recovery tests, not hand-edited SQLite or copied proof.
