# Observability and public errors

The process writes logs to `stderr`; `stdout` is reserved for JSON-RPC when the
transport is `stdio`. Optional sinks provide a rotated tool-audit file, an
atomic aggregate metrics snapshot, and a separate rotated operation log.

## Structured logs

Each line is an independent JSON object containing UTC time, level, logger,
stable message, and allowlisted event metadata. Verbose `httpx` and `httpcore`
logs are disabled to avoid recording request URLs.

## Tool audit

Every tool passes through `ToolAuditor`. One final event records:

- random `operation_id`;
- tool name;
- operation class: `read`, `plan`, or `apply`;
- environment when applicable;
- `success`, `error`, or `cancelled`;
- duration in milliseconds;
- stable error code when applicable.

The event excludes arguments, forms, fields, qualifications, SQL, values,
rows, endpoints, secret references, credentials, exception text, plan IDs, and
digests.

`operation_id` propagates through asynchronous context during the call and is
restored afterward to prevent cross-call correlation.

## Optional audit file

```dotenv
HELIX_AUDIT_LOG_PATH=.local/state/helix-mcp/audit.jsonl
HELIX_AUDIT_LOG_MAX_BYTES=10485760
HELIX_AUDIT_LOG_BACKUP_COUNT=5
```

Relative paths resolve against `.env`. POSIX files use `0600`; Windows users
must retain restrictive profile ACLs. Symbolic-link destinations are rejected.

The file sink accepts only final `tool_call` events with its closed schema. A
failure disables the sink and emits only `audit_file_unavailable` with a stable
code to `stderr`; tool execution continues.

## Aggregate metrics

```dotenv
HELIX_METRICS_PATH=.local/state/helix-mcp/metrics.json
```

The atomically replaced snapshot contains schema and update times, tool,
environment, result, counts, bounded latency buckets, and stable error counts.
Cardinality is bounded. Unknown tools become `other`; invalid error codes
become `INTERNAL_ERROR`. It stores no tool inputs or business identifiers.

## Operation log

```dotenv
HELIX_OPERATION_LOG_PATH=.local/state/helix-mcp/operations.jsonl
HELIX_OPERATION_LOG_MAX_BYTES=10485760
HELIX_OPERATION_LOG_BACKUP_COUNT=5
```

This sink accepts only allowlisted lifecycle and sink-degradation events. It
does not duplicate tool-audit events and follows the same rotation, permission,
and symbolic-link rules.

## Public MCP errors

Internal exceptions become:

```text
tool execution failed (code=ERROR_CODE, operation_id=...)
```

Only stable uppercase codes are retained; undeclared exceptions become
`INTERNAL_ERROR`. `FORM_NOT_FOUND` may return at most three policy-visible form
suggestions to the requesting client. Suggestions and native AR API error text
are never logged.

`ARAPI_ADMIN_REQUIRED`, `FORM_FIELD_AMBIGUOUS`,
`FORM_FIELD_NOT_QUERYABLE`, and SQL validation codes expose no SQL, business
values, credentials, or free-form server messages.

Lifecycle events are `application_starting`, `application_ready`,
`application_stopping`, and `application_stopped`. Startup and shutdown failures
use sanitized stable codes.
