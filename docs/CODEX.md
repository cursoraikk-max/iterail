# Codex long-running integration / Codex 长任务集成

**Official-documentation checked: 2026-10-16; the integrations below are not runtime-tested.** Check your actual Codex version, account and execution mode before use. This guide does not enable a goal, install hooks, start a service, change login/configuration, or grant permissions.

**官方文档核对日期：2026-10-16；以下集成尚未运行实测。** 使用前确认实际 Codex 版本、账号及执行模式。本文不会启用目标、安装 hook、启动服务、更改登录或配置，也不授予权限。

## Generic Core, optional host / 通用核心与可选宿主

Iterail exposes workflow state and evidence through CLI and MCP. An authorized host supplies the running agent, model authentication, scheduling and subsequent turns. An MCP response or durable continuation request cannot by itself wake an idle agent. Keep Codex commands in this optional integration layer, outside Core; Iterail needs no second model login.

Follow [Host guidance](HOST.md), [Continuation](CONTINUATION.md) and [Quality](QUALITY.md). Owner policy, leases, source-bound machine proof and independent review remain authoritative. A request packet is data, not permission. Never claim acceptance through self-report alone; without the required independent reviewer, stop at `ready_review`.

Iterail 通过 CLI/MCP 提供流程状态与证据；已授权宿主负责运行 Agent、模型认证、调度及下一轮。MCP 返回或持久化续跑请求本身不能唤醒闲置 Agent。Codex 命令仅属于此可选集成层，不写入 Core；Iterail 无需第二次模型登录。

继续遵守上述宿主、续跑和质量协议。Owner policy、租约、绑定源码的机器证据及独立审核仍是权威；请求包不是授权。不能靠自报验收；缺少规定的独立审核者时停在 `ready_review`。

## Preflight before freezing a task / 冻结任务前预检

Use the host-approved writable workspace and confirm the actual runtime and configured test commands work there before freezing owner policy/configuration. Prefer that existing authorized workspace to avoid unnecessary approval requests; do not change global configuration or assume approvals will never be needed.

先使用宿主已授权的可写工作区，确认实际运行时及配置的测试命令在该目录可用，再冻结 owner policy 和配置。优先沿用已有授权工作区，避免不必要审批；不更改全局配置，也不保证全程无需审批。

If a runtime/configuration problem requires a new state store, explicitly link the old and new state/task/run identities, preserve failed history, and confirm the old state has no active leases before proceeding. Do not silently refresh the original aggregate budget or deadline. If only the host maintains the cumulative ledger across states, say so explicitly; without a machine-enforced cross-state ledger, durable budget inheritance has not been verified. This is an operating requirement, not a new Core capability.

若运行时或配置问题确需另建 state，须明确关联新旧 state、task 和 run 标识，保留失败历史，并先确认旧 state 无活动租约。不能静默刷新原总预算或截止时间。若跨 state 累计总账仅由宿主维护，必须明确说明；没有机器约束的跨 state 总账，就不能宣称持久化预算继承已验证。这是操作要求，并非新增 Core 能力。

## Goal mode / Goal 模式

Official documentation describes `/goal` for interactive Codex CLI, desktop app and IDE extension. Enter it as a real user command in a supported host, not a string returned by an Iterail tool. CLI controls include `/goal pause`, `/goal resume`, `/goal edit` and `/goal clear`. Existing sandbox and approval boundaries remain; this does not guarantee uninterrupted execution. [Long-running work](https://learn.chatgpt.com/docs/long-running-work), [CLI developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

官方文档说明交互式 Codex CLI、桌面 App 与 IDE 扩展支持 `/goal`。在受支持宿主中由用户实际输入，不是让 Iterail 工具返回一段字符串。CLI 支持上述暂停、恢复、编辑及清除操作；原沙箱与审批边界不变，也不保证永不中断。

Adapt every placeholder to existing owner authorization before running. These prompts describe workflow intent, not new Iterail commands.

执行前，将占位符替换为既有 owner 授权范围。这些提示表达流程意图，不是新的 Iterail 命令。

### One authorized task / 单个已授权任务

```text
/goal Complete authorized Iterail task <TASK_ID> using its existing CLI or mounted MCP. Reconcile durable task/run state before acting; claim under the existing owner policy and retain leases, original budget and deadline. Record changes, run required machine checks bound to the exact current source, and obtain independent review before acceptance. Stop when accepted, cancelled, blocked on human input/authority, out of budget/time, or without justified progress. Report task/run IDs, evidence and blockers. Do not publish, expand permissions or invent work.
```

```text
/goal 使用现有 CLI 或已挂载的 MCP 完成已授权 Iterail 任务 <TASK_ID>。行动前核对持久化 task/run 状态，按既有 owner policy 认领，保留租约、原预算和截止时间。记录变更，执行绑定准确当前源码的机器检查，独立审核后才能验收。验收通过、用户取消、需人工输入或授权、预算或时间耗尽、或无合理进展时停止。报告 task/run ID、证据及阻塞。不要发布、扩权或凭空造任务。
```

### Preauthorized milestone or task set / 已授权里程碑或多任务集合

```text
/goal Deliver only owner-approved milestone <MILESTONE> from authorized Iterail task IDs <TASK_IDS>, within original total budget <BUDGET> and deadline <DEADLINE>. Reconcile durable state first. Independently claim each distinct task; record changes, obtain current-source machine verification and required independent review, then accept before claiming the next already authorized task. Preserve per-task leases and limits and the original aggregate bounds across resumes. Stop at the approved milestone, cancellation, budget/deadline, missing authority/reviewer, human blocker, or no justified progress. Report accepted and remaining IDs with evidence. Do not invent tasks, extend limits, publish or expand permissions.
```

```text
/goal 仅交付 owner 已批准的里程碑 <MILESTONE>，任务限 Iterail 已授权 ID <TASK_IDS>，遵守原总预算 <BUDGET> 和截止时间 <DEADLINE>。先核对持久化状态。每项任务独立认领，记录变更，通过当前源码机器验证及规定的独立审核，验收后再领取下一项已授权任务。恢复时保留每项租约和限额，也保留原总限额。达到批准里程碑、用户取消、预算或期限耗尽、缺权限或审核者、人工阻塞、或无合理进展时停止。报告已验收和剩余 ID 及证据。不造任务、不延长限额、不发布、不扩权。
```

## Optional Stop-hook adapter / 可选 Stop-hook 适配器

[Official hooks documentation](https://learn.chatgpt.com/docs/hooks) describes normal-stop handlers returning `{"decision":"block","reason":"specific next step"}` to request a continuation prompt. Handlers may use commands or already-connected MCP tools. Review and trust the exact definition through `/hooks`; obtain explicit approval before changing client configuration.

官方文档说明：正常收尾时返回上述结构可请求下一步提示；handler 可调用命令或已连接的 MCP 工具。修改客户端配置前须获明确授权，并通过 `/hooks` 审阅和信任具体定义。

**Proposed design, not shipped functionality:** synchronously read authoritative Iterail state, validate current evidence and remaining budget, then allow stopping or return one bounded next action. Preserve cancellation and human blockers; never invoke a model from the hook. No executable hook script is supplied: locking, recovery and reconciliation need integration tests first.

**设计建议，尚未交付：**同步读取 Iterail 权威状态，核对当前证据和剩余预算，再允许结束或返回一个范围明确的下一步。保留取消与人工阻塞；hook 不调用模型。锁、恢复与状态核对须先完成集成测试，因此这里不提供可执行 hook 脚本。

This applies to supported local Codex/local-only Work orchestration. Local tool execution does not make cloud orchestration load local command/plugin hooks; personal Work Cloud/dot does not gain those hooks from this setup. Interrupt hooks cannot restart an interrupted turn; asynchronous completion does not start an idle turn.

该方式适用于受支持的本地 Codex/本地 Work 编排。本机执行工具不代表云编排加载本地命令或插件 hooks；此配置不会为个人 Work Cloud/dot 提供同样能力。Interrupt hook 不能重启被中断轮次；异步完成也不会启动闲置轮次。

## One continuation driver / 一个主要续跑驱动

Choose one primary driver: Goal mode, a tested Stop-hook adapter, or a scheduler. Combining drivers requires shared ownership and tested coordination, rather than independent duplicate dispatch. Require stable request/run identity, atomic durable claims before dispatch, restart reconciliation, leases/fencing and no concurrent checkout writers. A lost response does not prove nothing ran. Retain original deadlines and attempt/request limits across resumes; reverify changed source and obtain independent review before acceptance.

选择一个主要驱动：Goal 模式、已测试的 Stop-hook 适配器或调度器。组合须共享 owner 并验证协调，避免独立重复派工。派工前必须具备稳定 request/run ID、原子持久化认领，重启后核对状态，遵守租约与 fencing，避免同一 checkout 并发写入。响应丢失不代表未执行。恢复不重置原截止时间或次数；源码改变须重新验证并独立审核。

Stop on cancellation, missing authority, human decisions, quota/error, exhausted budget or configured no-progress conditions. Waiting is not success.

遇取消、权限缺失、人工决策、额度或错误、预算耗尽、或满足无进展条件时停止。等待不代表成功。

## Later wakeups and limits / 后续唤醒与限制

- [Scheduled tasks](https://learn.chatgpt.com/docs/automations?surface=app) may continue a supported chat or create standalone chats. Local projects require the computer on, app running and files available. Cloud schedules have only supported cloud tools/context, not automatic local-folder access.
- An explicitly authorized external scheduler/service may use `codex exec resume <SESSION_ID> "<bounded next action>"` ([non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)). This is a host command, not Core. Use an explicit session ID for concurrent workflows. Service installation, durable access, concurrency and retries require separate design/authorization; this guide starts none. Codex retains authentication.
- Neither prompt nor hook guarantees recovery after exit, app closure, sleep, lost connectivity or quota exhaustion. Do not promise automatic quota-reset recovery. Record blockers and reconcile state before an authorized restart. A sleeping/off machine cannot execute a local scheduler.

- 定时任务可在受支持聊天继续，也可独立创建聊天。本地项目要求电脑开机、App 运行、文件可用。云定时任务仅有受支持的云工具与上下文，不自动获得本地目录访问。
- 明确授权的外部调度器或服务可使用上述 `codex exec resume` 宿主命令；并发流程指定会话 ID。安装服务、持续访问、并发与重试需另行设计和授权。本文不启动服务；认证仍由 Codex 管理。
- 提示词和 hook 不保证退出、关闭 App、休眠、断网或额度耗尽后的恢复，也不承诺额度恢复后自动续跑。记录阻塞，获授权重启前核对状态；休眠或关机时本地调度器无法执行。

## Evidence scope / 证据范围

| Path / 路径 | Evidence / 证据 |
| --- | --- |
| Running host agent → Iterail CLI / 已运行宿主 Agent 调用 CLI | Genuine development use was recorded with task/run IDs and source-bound evidence during development; private run evidence is not bundled here. See the [host contract](HOST.md) for the interface. Recorded CLI use does not prove every host integration. / 开发时记录了真实 task/run 与源码证据；私有运行证据不随包分发。宿主协议说明接口，CLI 使用记录不代表每种宿主集成均通过。 |
| MCP stdio protocol fixtures / MCP stdio 协议 fixture | Automated protocol checks; not a native Codex or Claude MCP connection. / 自动化协议检查，不等于宿主原生连接。 |
| Specific native host MCP connection / 具体宿主原生 MCP | Not verified by this guide. / 本指南未验证。 |
| Goal, Stop hooks, scheduled/external wakeup / Goal、hooks、定时或外部唤醒 | Documentation guidance only; not runtime-tested here. No 24×7 guarantee. / 仅文档指导，未实测，不保证全天无人值守。 |

Before unattended use, test in isolation: successful completion, justified continuation, duplicate delivery, restart after dispatch, stale proof, cancellation, approval-needed and quota/error paths, exhausted budget, competing-driver rejection. Check that none widens permissions, resets budgets, duplicates work or accepts unverified changes. Capability detection is not end-to-end acceptance.

无人值守前在隔离项目测试：完成、合理续跑、重复投递、派工后重启、过期证据、取消、待授权及额度/错误、预算耗尽、驱动冲突拒绝。确认不扩权、不重置预算、不重复执行、不验收未验证变更。能力检测不等于端到端验收。
