# Contributing to the local alpha

The approved source repository is https://github.com/cursoraikk-max/iterail; its
creation/publication must be verified separately. Ordinary bugs and contributions
use repository Issues and pull requests once available. Confidential vulnerabilities
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

CI configuration is a template for a pre-provisioned self-hosted environment with
these offline prerequisites. It has not run remotely. No secrets, downloads,
publication, scheduled daemon or provider execution belong in these checks.

## Provenance and release prerequisites

Keep the owner-confirmed "Copyright (c) 2026 TIM" MIT notice. The owner approved
the minimal public source repository; GitHub private vulnerability reporting must
be checked and enabled there rather than assumed. No email channel is offered. Imported helpers require
recorded provenance and compatible licenses; do not add uncertain artwork or
third-party copied code without review. Document source identity, commands, exit
codes, limitations and review evidence. Never include claim tokens, personal paths,
private databases or operational raw logs in distributions. Synthetic frozen test
literals are test data, not credentials. See docs/SOURCE-PROVENANCE.md.
