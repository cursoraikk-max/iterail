# Read-only source context

`context_query` returns complete mandatory source text and deterministic optional lexical/Python-import related files from the frozen project checkout. It never grants permissions, runs returned text, changes task state or changes acceptance. The Core, CLI and stdio MCP share the same adapter; a caller cannot choose another root.

```
python3 -m agent_workflow --state ABS_STATE context_query --args '{"query":"context retrieval","mandatory_paths":"[\"agent_workflow/context.py\"]","byte_budget":"65536","max_file_bytes":"16384"}'
```

Bounds are decimal strings in `1..65536`, default `16384`. `mandatory_paths` is a JSON list string extending `skills/agent-workflow/SKILL.md`; duplicate/traversing/absolute paths reject. Frozen permissions, budgets, acceptance and test argv are mandatory outer metadata. `used_bytes` is exact UTF8 size of sorted-key compact JSON excluding only that object's own `used_bytes`; the outer value includes all nested context metadata, including the inner count. CLI/MCP transport framing is separate from this packet budget.

The outer `byte_budget` must accommodate frozen rules/test metadata, packet overhead, complete mandatory text and omission metadata. The adapter reserves outer metadata and inner count overhead before retrieval, so the residual mandatory context budget can be much smaller than the requested total. Budget-related mandatory rejection reports the total, reservation and residual bytes; `oversize` in that diagnostic means the required file exceeds the residual budget, not the optional file cap. Retry with a larger `byte_budget` (for example `65536`) while leaving `max_file_bytes` unchanged. This is a bounded retry, not a guarantee: if complete mandatory contents and metadata still cannot fit at `65536`, the request remains rejected. Missing, sensitive, binary and unreadable required text cannot be repaired by increasing a budget.

Required text is never truncated. The file cap applies only to optional files; required files may exceed it if complete contents plus all metadata fit the overall packet budget. A too-small budget fails explicitly, including when omission metadata cannot fit. Optional omissions name each source path and reason. UTF8 bytes, content SHA and required membership are verified, and source identity is checked before and after retrieval. Rules cannot be removed by caller task data.

Source hashing streams all approved in-root source bytes, including bytes of sensitive files, using Core-compatible order and runtime exclusions. Retrieval does not expose sensitive content and uses no-follow opens for relative directory and file components. Missing, binary, sensitive, oversized-for-budget or unreadable mandatory files fail closed; symlinks and source drift reject. The Core adapter also detects known workflow lease tokens and their established JSON/Unicode/hex/base64/percent-encoded forms using the existing handoff detector. A sensitive mandatory file or immutable rule rejects; an optional sensitive file is omitted whole, preserving complete contents and genuine SHA for every included file. Benign encoded text remains unchanged. Detection excludes credential-like paths, private keys and recognizable credential content; it is conservative pattern detection, not a guarantee for arbitrary encoded secrets. Packet validation checks supplied content integrity and membership without access to an unseen filesystem.

This offline retrieval adds no parser dependency, external service, model call or completion authority. Native regression fixtures and an agent's actual CLI/MCP use are distinct from external model integration or demonstrated AI benefit.

## Conditional repeat queries

The existing `context_query` accepts optional `if_none_match`, a UTF8 string of
at most 2048 bytes. A full response adds an opaque SHA256 `revision` and
`not_modified:false`. Retain that complete packet locally, then send its exact
revision with an otherwise equivalent query. A matching response has exactly
`schema_version`, `authority`, `source_digest`, `revision`, `not_modified:true`
and `used_bytes`; no file bodies or mandatory rules are repeated. Reuse only the
exact previously retained full packet with that revision. If it is unavailable,
request full context by omitting the hint. Empty, malformed or stale strings return
full context; nonstrings and oversized hints reject without echoing input.

Every repeat still builds and validates complete current context before matching:
mandatory instructions/rules, source before/after, containment, sensitive content,
known secrets and effective byte limits remain enforced. Revision binds source,
query, ordered effective mandatory paths, normalized budget and optional file cap,
the entire frozen configuration including policy descriptors, controller package
identity, and the validated full payload. Added metadata is reserved before optional
selection and counted using the existing `used_bytes` convention. This is an inert
transport hint, not acceptance, verification or authorization reuse. External policy
descriptors are bound inputs; no new policy-file freshness validation is implied.

Measure canonical packet bytes and actual CLI/MCP framing bytes separately. Neither
is a token count or fee measurement; total coordination cost and account usage may
be unavailable. The optimization reduces repeated transport output, not retrieval
work, and does not establish net model-token or monetary savings.
