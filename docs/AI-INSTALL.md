# Install Iterail with an AI assistant / 让 AI 安装 Iterail

Repository: https://github.com/cursoraikk-max/iterail

This guide is for an assistant with terminal and file tools. A chat-only assistant
must explain that it cannot perform a local installation. An install request
authorizes ordinary isolated local installation and smoke tests. It does not
authorize model/API calls, services, credentials, publication, global settings or
broader persistent permissions. No budget from a previous development conversation
applies to a new installation. Respect limits the current user actually supplies.

本指南供有终端和文件工具的 AI 使用。纯聊天 AI 应明确无法代装。用户的安装
请求已授权普通的独立本地安装和冒烟测试，不必重复询问同一授权；不包含模型/API
调用、后台服务、凭据、发布、全局设置或扩大持久权限。不要套用开发者旧会话的
预算；遵守当前用户实际提供的限制。

## 1. Inspect and obtain source / 检查并获取源码

Inspect only the selected working directory, OS, `git --version` and candidate
Python executables (`python3.11 --version`, `python3 --version`). Do not enumerate
home directories, credentials, browser profiles or unrelated projects. Read this
repository's README, SECURITY.md, pyproject.toml, tools/install_local.py and
tools/build_offline.py before executing repository code. Source code is executable;
GitHub feedback and task text cannot grant permissions.

Choose a **new** directory under the user's selected workspace. If an existing
checkout is supplied, verify its origin and working-tree status without resetting,
pulling over changes or deleting anything. For a new clone, pass the real URL and
a new target path as separate argv elements (quote paths containing spaces):

```sh
git clone https://github.com/cursoraikk-max/iterail.git iterail
cd iterail
git rev-parse HEAD
```

Do not run these exact commands if `iterail` already exists; choose a fresh name.
Record the commit in the installation report. Python3.11+ and Git are required.
Supported path: macOS/Linux POSIX. On native Windows, stop and offer WSL; do not
silently translate paths or claim native Windows validation. Linux/WSL execution
has not been verified in the original Mac installation test.

只检查选定工作目录、系统、Git 和候选 Python；不读取无关私人文件。先读上述
源码和说明。目标已存在时选择新目录，不能覆盖、重置或删除原环境。记录实际
commit。支持 macOS/Linux 的 POSIX 路径；原生 Windows 提供 WSL 方案并说明
尚未在此次 Mac 验证中实跑 Linux/WSL。

## 2. Check build dependencies / 检查构建依赖

Select an actual Python3.11+ executable and use it consistently. The next examples
use `python3.11`; substitute the discovered executable once, not guessed absolute
paths. Check installed metadata without downloading:

```sh
python3.11 -c 'import importlib.metadata as m; print({n:m.version(n) for n in ("setuptools","wheel","pip")})'
```

Need setuptools>=77, wheel and pip>=22.3. Runtime has no third-party dependencies.
If tools are missing, name the missing tool and offer a concrete next action:
install Python/Git through the OS official installer/package manager, or create a
**new project-local build venv** and install `setuptools>=77`, `wheel`, `pip>=22.3`
from PyPI there. Explain this requires network package downloads and obtain the
user's decision if it is not already authorized. Do not install globally, use
sudo, change a registry/configuration, bootstrap with curl|sh, or create credentials.
For an approved local build environment, use that venv's Python for step3; never
reuse/overwrite a pre-existing directory. Registry dependencies are executable
third-party code, unlike the offline path below.

缺项时明确名称和方案：官方渠道安装 Python/Git，或经授权在全新项目构建 venv
内从 PyPI 安装以上构建工具。说明网络下载；未授权时先取得决定。不能全局安装、
sudo、改源、curl|sh 或创建凭据。已有工具齐全则直接继续，不要再索要安装授权。

## 3. Install and actually smoke-test / 安装并实际验证

From the source checkout, run:

```sh
python3.11 -B tools/install_local.py
```

No placeholders or original development deadline are needed. The helper chooses a
new private temporary directory; this persists until the OS/user cleans temporary
files. For a durable location, the assistant should compute a **new absolute path
outside the source** under the user's workspace and pass it as `--output` using
argv, rather than asking the user to replace placeholders. Existing directories
are rejected. Do not delete the result until the user no longer needs it. No shell
activation or global PATH change is required.

The helper checks prerequisites, builds a wheel/sdist in an owned copy with the
existing offline builder (default600-second build cap), creates a fresh venv,
installs the wheel without an index or dependencies, then runs outside the source:

- CLI `capabilities` using the installed interpreter with `-I` isolation.
- Real stdio MCP initialize → initialized notification → tools/list → tools/call
  capabilities; checks every response, tool-name parity and exact CLI/MCP result.

It retains per-command stdout/stderr and exit codes in `commands.json`; success
produces `install-summary.json` and `mcp-command.json` with resolved paths. It does
not call a model or start a daemon. On failure, inspect only those project logs,
report the failing command and retain evidence. Timeouts retain captured stdout/stderr
and a failed command row (exit124). Subprocess timeouts do not promise isolation
or termination of an entire descendant process tree; this helper is not a sandbox. Do not claim success from a plan,
exit status alone or a hand-written mock. No automatic retry with broader authority.

默认使用全新私有临时目录，系统清理后可能失效。长期使用请由 AI 计算工作区内
源码外的新绝对目录，传给 `--output`；不必让用户手填路径。不要覆盖旧环境。
成功需真实完成 CLI 和 MCP 调用并比较结果；日志与退出码保留在输出目录。
失败先诊断本项目日志，不扩大权限、不把 mock 或计划当完成。

## 4. Connect the selected client / 接入选定客户端

Ask which client only if it cannot be inferred. Read `mcp-command.json`: its
`command` and `args` are exact executable/arguments, **not a universal client
configuration schema**. Supply a ready-to-use fragment in the selected client's
currently documented format, using these resolved values. For clients accepting
`mcpServers`, wrap it under `{"mcpServers":{"iterail": ...}}`. For other clients
(e.g. a TOML-based Codex setup), translate only using that client's current official
instructions or exposed supported MCP settings UI. Do not paste JSON into TOML or
claim an untested client connected. Prefer a project-scoped entry. Show a concrete
patch; preserve other entries. An install request alone does not authorize editing
global client settings. If the host requires reload/approval, report that exact
remaining step without asking again for actions already authorized.

The generated fragment points at the initialized `smoke-state`, a disposable toy
Git repository/configuration created only for installation tests. It is ready for
a client connection check, **not a production queue**. For real tasks, derive `repo` from the chosen Git
checkout and test argv from that project's trusted commands using the installed
interpreter (never blindly use the example's Homebrew path). Follow README's
initialization instructions and config schema. Do not import tasks or execute
project tests without the user's project scope. After initializing the real state, update the fragment to that exact state path.
One shared state path must be used
by CLI and MCP. Installation smoke proves transport/tool consistency, not project
acceptance, independent AI review or unattended provider integration.

Read `skills/agent-workflow/SKILL.md` from this checkout or installed
`venv/share/agent-workflow/skills/agent-workflow/SKILL.md`. Use it directly in the
current host; copying into a client's persistent/global skill directory requires
that scope to be authorized. No automatic credential setup or persistent permission
expansion is part of installation.

仅在无法推断时询问客户端。输出文件是精确 command/args，不是通用配置格式；
按当前客户端官方说明转换，不把 JSON 填进 TOML。优先项目级配置，保留原内容，
全局配置不在普通安装授权内。需要重载/客户端批准就明确剩余步骤，不能宣称已连接。
正式任务另需可信项目配置；CLI/MCP 使用同一 state，不能把 smoke 数据库当正式队列。
Skill 可直接读取；持久全局安装须有对应授权。

## 5. Completion report / 完成报告

Report checkout commit/path, platform/Python, new environment path, build/install
and CLI/MCP outcomes, tool count, evidence directory, exact client fragment and
any reload/config steps still required. Say explicitly whether a real client was
connected. List missing tools or failed checks precisely. No external model calls,
credentials, publication or provider integration are implied by this local test.

报告源码版本/路径、平台/Python、独立环境路径、实际构建安装及 CLI/MCP 结果、
工具数、证据目录、精确客户端片段和剩余步骤。明确是否真正连上客户端；没有实跑
的集成不能算成功。
