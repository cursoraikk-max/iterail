# Offline app-server observations

`agent_workflow.appserver.OfflineSession` consumes supplied JSON objects or
JSON strings from the stable Codex app-server 0.162.1 protocol. It validates a
semantic projection of request correlation and thread, turn, item, error,
token-usage and command/file approval lifecycles. It does not validate every
field in the official schema. The projection pins the installed schema file
manifest SHA256
`69b19c71c7707c369ea913c0b4ad1e96bf5ed49e0f4606d70677d39b8fc0d638`.
The protocol reference is [the official app-server documentation](https://learn.chatgpt.com/docs/app-server).

Replay an existing local JSONL file:

```sh
python3 -m agent_workflow.appserver /absolute/observations.jsonl
```

For a supplied response, first register local correlation metadata using
`register_request(id, method)` or CLI `--requests '[{"id":3,"method":"thread/read"}]'`.
Registration sends nothing. JSON-RPC `jsonrpc` may be omitted on the observed
wire; if supplied it must be `2.0`. IDs distinguish signed int64 integers and strings,
excluding booleans. Duplicate JSON members, unknown methods or responses,
wrong lifecycle identities, duplicate phases, and unsupported versions reject.
Malformed observations leave the session unchanged. The cumulative UTF-8 input
limit defaults to 65,536 bytes and cannot exceed that cap. Local registered
request metadata is charged to the same byte budget before any state change.

The supported notification methods are `thread/started`, `turn/started`,
`turn/completed`, `item/started`, `item/completed`, `item/agentMessage/delta`,
`error` and `thread/tokenUsage/updated`. The supported server request methods
are `item/commandExecution/requestApproval` and `item/fileChange/requestApproval`.
A session may begin with `turn/started` without loaded thread history. Starting
turn items must be empty; each later item requires its own observed lifecycle.
This intentionally limited projection rejects other methods rather than
assuming they have safe completion semantics.

Agent text and response payloads are inert observations. Successful terminal
completion requires a matching `turn/completed` with status `completed`, no
open items, no pending approvals, and no prior nonretryable error. Failed or
interrupted turns set `failed`, never `completed`. A later correlated response
error clears global successful completion and preserves the failed summary.
Approval requests remain
pending; this offline adapter cannot approve, execute, or resolve them.
Snapshots are detached data and include protocol/schema identity and counters.

These observations never create a receipt, accept a Core task, launch a server,
send a model request, or change owner rules. Native replay verifies parser
behavior only; actual app-server integration remains unexercised. The existing
runner continues using its frozen `exec-v1` backend path.
