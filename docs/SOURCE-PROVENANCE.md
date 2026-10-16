# Source provenance and release status

The initial alpha packaging carried the then-existing26 runtime Python files and
26 unit-test files without edits. That is a historical inventory, not the current
file count or a claim that later reviewed runtime changes never occurred. Current
changes are recorded in Git/CHANGELOG; the87 pinned historical fixture files
remain unchanged by this maintenance batch. The portable suite includes a pinned manifest and87 exact fixtures;
its relocated paths are harness adaptation, not relaxed behavioral assertions.
Historical CLI startup logs were removed for release privacy. The two replacement
files under startup-errors/ contain only a minimal synthetic warning/error signature,
without real timestamps, release/call identifiers or user paths. One startup helper
changes only fixture locations and provenance labels; exact classification,
no-session, no-second-launch/no-refund and conservative controls remain intact.
The manifest retains87 fixture files:83 are unchanged from the preceding minimal
candidate; the startup helper, neutral config and two synthetic inputs account for
four changed/replaced files. Public integrity hashes cover distributed bytes only;
private source-config/workspace/interpreter fingerprints are not distributed.
The two copies of mocked-local test receipt data contain no user information and
remain inert regression inputs, not current acceptance or external Agent evidence.
Synthetic token-shaped negative-test literals remain test data. No operational
session database, credentials, personal run logs or private run evidence is included.

The owner confirmed TIM as the rights holder and approved the minimal public
source repository at https://github.com/cursoraikk-max/iterail. The previous internal
placeholder attribution is replaced by "Copyright (c) 2026 TIM"; the MIT grant terms
are unchanged. This records the owner decision, not an invented legal assignment
or certification of every possible upstream right. Vulnerability reports use
GitHub Private Vulnerability Reporting only; the owner verified it enabled on2026-10-16. This dated settings observation is
separate from source validation and can change later. Never publish confidential details in
Issues when private reporting is unavailable. Only two project-authored Iterail SVG lockups are restored under the current owner instruction; concept references, raster previews and old draft documents remain excluded. See [brand provenance](BRAND.md). agent-workflow is the distribution name; registry and
trademark availability have not been checked. No registry release is authorized.

Other projects informed earlier design research; this alpha does not redistribute
their repositories or grant their trademark rights. Any future imported source
must carry explicit origin, pinned revision, applicable license and review record.
The portable fixtures and runtime remain subject to review; local test success is
not a copyright, security, provider integration or public-release certification.

## Portable AST compatibility boundary

Two fixed historical AST checks have an explicit Python3.11/3.14 serialization
mapping. It preserves raw target SHA, the12 original assertions and equal canonical
structure; other Python minor versions fail closed at this adapter. The portable
55-command suite targets3.11 and3.14 only; other minors are unverified. Interpreter
minimum metadata >=3.11 is not a promise that this full suite runs on every later
minor. Controlled compatibility preflights do not replace the full actual suite.
