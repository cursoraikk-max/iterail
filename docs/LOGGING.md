# Public projections and private evidence / 公共导出与私有证据

The shared `agent_workflow.redaction.public` policy (`public-v1`) is used by
Runner public status/feedback/CLI serialization and Evolution public CLI output.
Runner keeps its legacy `REDACTED` marker; Evolution uses `[REDACTED]`. The matching
rules are shared. Diagnostics' existing allowlisted projection and handoff's
explicit-known-token scrub remain separate stricter contracts.

The policy handles common credential keys, labelled bearer/basic values,
provider-shaped API keys, Core48-hex fencing tokens, explicit caller-known
secrets and bounded nested/common encodings. It omits fields deeper than16 or text longer than128 KiB. It preserves ordinary
40/64-hex identity/hash values and usage counters. Repeated projection is stable.
Identity paths and non-secret serialized JSON bodies are kept byte-for-byte;
masking their formatting or paths could invalidate controller consumers without
changing a raw hash pointer. Before sharing an artifact, separately map personal
paths to reviewed logical references and hash that resulting artifact.
Unknown unlabelled opaque secrets and arbitrary imported formats cannot be
reliably identified; this policy does not prove absence of secrets or grant
permission to publish task bodies/logs. Review artifacts and minimize data.

## Hash and capture contract

Guardian/Core raw stdout/stderr, captured prefixes, byte counts, source/proof
bindings and their existing canonical hashes are not rewritten by this policy.
They remain owner-private. Runner's existing token-only private reservation
metadata representation is intentionally preserved for receipt compatibility.
Public projection returns new values without mutating stored evidence.

A `raw_*sha256` field still identifies original private bytes, even beside a
sanitized preview. It must not be reinterpreted as the hash of that preview.
Compute a separate explicitly named public-artifact hash when exporting bytes.
Do not substitute a redacted projection into canonical receipts or machine
acceptance. Truncated raw prefixes are not complete logs or final tails.

Core CLI/MCP and private worker ctl calls may return authorized private evidence;
this batch does not turn every local interface into a public logging service.
Never upload raw databases, handles, environments, credential files or operational
logs merely because one public serialization path has a redactor.

## Validation and limits

`tests/packaging/public_redaction.py` uses only fake credentials, both public
wrappers, and a real native subprocess/Core verification. It verifies that raw
evidence still contains the expected original bytes/hash while public output
omits the fake values. Wheel-installed execution tests the same contract.
No provider/model is called. Ruff/mypy/coverage are a focused development
baseline; lifecycle, quality, promotion and installation tests remain mandatory.

公共脱敏是展示层投影，不改写原始私有证据；原始哈希仍指向原始字节，而不是脱敏
预览。需要公开文件哈希时另算并明确命名，不能把预览当验收回执。公共导出不等于
获得发布权限，也不能证明任意秘密已被找出。原始日志、数据库、环境和令牌必须受
私有访问控制保护。本批仅测试假凭据，无真实模型或凭据读取。
