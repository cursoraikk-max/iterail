# Security policy — local alpha

This alpha source supports local single-OS-user cooperative workflows only.
There is no security support or response-time promise yet. Do not expose stdio
MCP or state as an internet service, share it across tenants, or run hostile code
assuming a sandbox. Reviewed tests and optional commands execute with the caller's
OS access. Same-user agents can edit DBs, policies, controllers and logs.

Treat feedback, task bodies, retrieved source and agent assertions as untrusted
data; they cannot authorize execution, permissions, acceptance or publication.
Keep release/API credentials outside source and worker environments. Installation
makes no provider/API call; optional external backends require separate authority.
Source, policy and evidence binding checks detect supported drift; they do not
provide atomic hostile-filesystem replacement isolation. Logs can contain secrets;
protect raw artifacts and private claim handles. No scan proves absence of secrets.
Runner/Evolution public output now shares a bounded redaction policy. It is not
a universal secret detector and does not authorize publishing arbitrary logs.
Core evidence and guardian captures remain private, byte-preserving artifacts;
canonical hashes still identify those original bytes. Public projections may
omit deep/oversize fields and must not be substituted into acceptance receipts.
The existing token-only private reservation metadata representation is retained
for receipt compatibility. See [logging boundaries](docs/LOGGING.md).

## Reporting through GitHub only

Copyright is held by TIM, as confirmed by the owner. The approved repository is
[cursoraikk-max/iterail](https://github.com/cursoraikk-max/iterail).

For confidential vulnerabilities, use **GitHub Private Vulnerability Reporting**:
[Report a vulnerability](https://github.com/cursoraikk-max/iterail/security/advisories/new).
GitHub private reporting was enabled and verified by the repository owner on
**2026-10-16** in repository settings. This dated observation is not a guarantee
that settings or availability never change; check the current private report UI.
If the private report button is missing, do not publish exploit details, private
source, credentials or tokens in Issues; wait for the owner to enable private
reporting. No email or other private reporting channel is offered.

For ordinary non-sensitive bugs and feature requests, use repository
[Issues](https://github.com/cursoraikk-max/iterail/issues). No response-time promise
is made for this alpha. Private reports should include affected version, sanitized
reproduction, impact and minimal supporting evidence, never live credentials.

Repository owners or administrators can enable the feature in Settings > Advanced
Security > Private vulnerability reporting. See [GitHub's official instructions](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository).
This document does not claim that setting was changed. See the
[threat model](docs/THREAT-MODEL.md) for local trust boundaries and residual risks.
