# Hosted CI / 托管持续集成

`.github/workflows/local-alpha.yml` now uses standard GitHub-hosted runners,
not a user's Mac or a self-hosted queue. It triggers on main pushes, pull requests
targeting main, and manual dispatch. Matrix: `ubuntu-24.04` and `macos-14`, CPython3.11.
Native Windows and other Python minors are not covered. Configuration is not a
passing result; check both matrix jobs on the exact commit in Actions before
claiming hosted validation or adding a status badge.

工作流使用标准 GitHub 托管 runner，替换原手动 self-hosted 队列；main push、面向
main 的 PR 和手动触发。矩阵为 Ubuntu24.04/macOS14、Python3.11，不含原生 Windows。
配置完成不等于 CI 通过，须核对准确提交两个 job 的实际结果后才添加状态徽章。

## Checks

1. Official checkout/setup-python Actions use fixed commit SHAs. Checkout does not
   persist its token or change global Git safe.directory. No dependency cache is used.
2. A fresh job-local venv downloads binary wheels for `pip==25.1.1`,
   `setuptools==80.7.1`, `wheel==0.45.1` from PyPI. These are build-only dependencies,
   matching the locally exercised toolchain, not new runtime dependencies. Python
   provisioning and Actions also require network access. Subsequent test/build/install
   entry points do not download packages or invoke providers.
3. `tools/check.py` requires all55 declared portable commands, original fixture
   hashes/acceptance, no skipped unit cases and unchanged tested source. Its own
   deadline is1100 seconds; the complete matrix job has a25-minute timeout.
4. `tools/check_distribution.py` runs `tools/install_local.py` against source and an
   extracted sdist. Each independently builds offline, installs a wheel into a new
   venv, initializes a disposable local config and compares real CLI capabilities
   with stdio MCP initialize/tools/list/tools/call (27 tools). It verifies installed
   docs/Skill/license and exact runtime bytes. Sdist files are extracted manually
   after rejecting traversal, links and special files; owner headers must be neutral.
   Sdist installation means an offline backend rebuild to a wheel, not pip fetching
   build dependencies. This does not connect a host client or exercise a paid AI.
   A separate real-child regression (`tests/packaging/distribution_diagnostics.py`)
   requires timeout stdout/stderr and a failed command row with exit124, while
   preserving ordinary nonzero exit diagnostics. The checker reuses the installer's
   command recorder; timeouts do not guarantee isolation of an entire process tree.
   `tests/packaging/worktree_project.py` also exercises the installed wheel in a
   real Git linked worktree: CLI init/import/claim/change/verify/ready_review and
   MCP status parity, plus broken gitfile/nested-root rejection and inherited Git
   environment isolation. This uses local fixture repos, not a host client or AI.
   `tests/packaging/gitfile_validation.py` separately calls the real config validator
   with only the Git subprocess boundary mocked: different root, missing executable,
   immediate timeout, and unchanged legacy `.git` directory behavior. It asserts the
   fixed argv, clean environment and10-second timeout setting without waiting10 seconds.
   CI runs this against source and, with `-I --installed`, the installed wheel;
   these deterministic branch tests supplement the real Git/CLI/MCP tests above.
5. Official pinned upload-artifact retains scoped summaries/command logs for7 days,
   including failures; it excludes source copies, environments and state databases.
   GitHub stores normal CI logs/artifacts; this is not a package release.

Only `contents: read` is granted. No repository secrets, deployments, services,
provider credentials, `pull_request_target`, third-party Actions or untrusted
PR-title/body interpolation in shell commands are used. PR source still executes
as test code on an ephemeral runner; the workflow is not a hostile-code sandbox.
Concurrency cancels obsolete runs of the same branch/PR; fail-fast is false so one
platform failure does not hide the other platform's result. Standard hosted runners
are intended for this public repository; do not switch to paid larger runners or
change billing settings if GitHub reports a restriction. Report the restriction.

权限仅 contents:read，不需仓库密钥、模型凭据或发布权限。PR 测试源码仍是可执行代码，
不能将临时 runner 当成恶意代码沙箱。相同分支/PR 新运行取消旧运行，两个平台独立报告。
遇到额度或付费限制应报告并暂停，不能切到付费大型 runner 或修改计费/账号安全设置。

## Local reproduction

With the documented Python3.11 build tools already installed, choose new output
paths outside source. `tools/check.py --output PATH --deadline UNIX_TIME` uses a
current explicit run deadline. `tools/check_distribution.py --output PATH` builds
and tests both distributions. No historical development budget applies to a new run.
The first hosted run [38764121662](https://github.com/cursoraikk-max/iterail/actions/runs/38764121662)
passed both matrix jobs at commit `db16c160e3347d0db3ddd670142b3f1e7f7af0fd`,
including full55, distribution checks and artifact upload. Later modifications
require new results. The README badge tracks main's latest run, not every commit.
This validates transport/tools on hosted Linux/macOS, not a real client connection,
external model/provider or unattended AI worker.

Official references:
[workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python),
[upload-artifact](https://github.com/actions/upload-artifact).
