# Contributing to the local alpha

The approved source repository is https://github.com/cursoraikk-max/iterail; ordinary bugs and contributions
use repository Issues and pull requests. Confidential vulnerabilities
use only enabled GitHub private reporting, never public Issues. Work in an isolated
local checkout with Python3.11+, Git and no account/provider dependency. Review
[SECURITY](SECURITY.md) and [threat model](docs/THREAT-MODEL.md) first.

Run from a clean source checkout, keeping logs outside it:

```sh
python3.11 tools/check.py --output /absolute/check-results --deadline ORIGINAL_UNIX_DEADLINE
```

The frozen portable manifest runs55 commands (53 Core plus version/budget
supplements), with55 nested unit cases and no skips. Fixtures are controlled native
programs, not real model calls. Do not weaken failing tests, policy, permissions or
budgets. Fix implementation or obtain independently reviewed harness corrections;
preserve failures and source-bound evidence. Reviewers must inspect exact source
and actual machine commands/exits/logs; an Agent's success statement is insufficient.
Worker and independent reviewer must remain separate workflow roles.

## Offline build prerequisites

Have Python3.11, setuptools>=77, wheel and pip already installed in the chosen build
environment. Local validation uses setuptools80.7.1, wheel0.45.1 and pip25.1.1.
Nothing here downloads them. Missing inputs are a blocker, not permission to fetch.
The public build tool copies source into an owned directory before invoking the
existing backend, so build metadata does not change the validated checkout:

```sh
python3.11 tools/build_offline.py --output /absolute/new-build-output --deadline ORIGINAL_UNIX_DEADLINE
python3.11 -m venv --without-pip /absolute/install-env
python3.11 -m pip --python /absolute/install-env/bin/python install --no-index --no-deps /absolute/new-build-output/dist/EXACT_WHEEL.whl
```

Extract the sdist safely to another owned directory, build it to a wheel with the
same existing backend, then install into another fresh environment. This avoids
pip build-isolation downloads; do not describe it as a direct isolated pip sdist
build. Check CLI and stdio MCP27 from outside the source directory, and compare
installed README/license/docs/Skill byte-for-byte under share/agent-workflow/.
The build tool transparently normalizes tar owner IDs/names and PAX ownership/time
fields plus gzip filename/time. Member paths, types, modes and bytes are checked
unchanged; no source or test content is sanitized away. Build logs remain outside
the checkout. The optional absolute deadline is never renewed; omission gives a
600-second local build cap. No dependency download is performed.
Distribution metadata0.1.0a1 differs from unchanged runtime MCP serverInfo0.1.0.

CI uses GitHub-hosted Ubuntu24.04 and macOS14/Python3.11. Its job-local venv
downloads pinned build/development wheels from official PyPI; runtime and later
offline distribution installation need no provider or new runtime dependency.
The baseline run38857162020 passed at617bae1; changes require fresh exact-commit
results. CI configuration alone is not a passing result. No publication, service
registration or model call belongs in these checks.

## Incremental development checks

Use a separate Python3.11 project venv. Installation is an explicit network step:

```sh
python3.11 -m venv /absolute/dev-venv
/absolute/dev-venv/bin/python -m pip install --no-cache-dir --index-url https://pypi.org/simple --only-binary=:all: -r requirements-dev.txt
/absolute/dev-venv/bin/python -B tools/check_quality.py --output /absolute/new-quality-output
```

Ruff syntax/name checks cover the new public-redaction module, focused test and
quality command. Strict mypy initially covers only the new module; this is not a
whole-project type migration. Coverage reports branches/lines for that module,
with no threshold. None replaces full55, source/installed lifecycle, host, quality,
promotion or wheel/sdist CLI/MCP checks. Output/caches stay outside source.

Treat raw verification/guardian logs, databases and handles as private evidence.
A public projection is not the canonical artifact and its content hash is not the
raw log hash. See [logging boundaries](docs/LOGGING.md). Report passing, failing
and not-run commands separately, with exact source identity. Review templates
request sanitized reproductions, never credentials or raw operational logs.

## Provenance and release prerequisites

Keep the owner-confirmed "Copyright (c) 2026 TIM" MIT notice. The owner approved
the minimal public source repository; GitHub private vulnerability reporting must
be checked and enabled there rather than assumed. No email channel is offered. Imported helpers require
recorded provenance and compatible licenses; do not add uncertain artwork or
third-party copied code without review. Document source identity, commands, exit
codes, limitations and review evidence. Never include claim tokens, personal paths,
private databases or operational raw logs in distributions. Synthetic frozen test
literals are test data, not credentials. See docs/SOURCE-PROVENANCE.md.

## Repository contribution route

As verified by the owner on2026-10-16, main requires a PR and both existing
Ubuntu/macOS CI checks. No mandatory human approval count was added by that
rule (configured count0); workflow-level independent review still applies where
required. Use a review branch and PR, never direct main writes or force pushes.
Administrative bypass/exceptions are separately governed by the owner; this
source change does not alter repository settings. GitHub private vulnerability
reporting was verified enabled on the same date; keep confidential reports there.
