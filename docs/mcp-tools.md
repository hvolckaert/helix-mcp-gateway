# MCP tool catalog

Helix MCP Gateway uses the official MCP Python SDK and returns structured
results. The MCP layer adapts schemas only; targeting, authorization, bounds,
and BMC communication remain in application services.

## Classification

Risk is the maximum inherent impact before deployment policy and BMC account
permissions reduce it.

| Tool | Class | External effect | Risk | AR administrator required |
| --- | --- | --- | --- | --- |
| `list_targets` | read | Local catalog only | Low | No |
| `health_check` | read | Local connectivity probes | Low | No |
| `list_forms` | read | Reads visible form names | Medium | No |
| `list_form_fields` | read | Reads policy-visible metadata | Medium | No |
| `query_form` | read | Reads bounded business data | High | No |
| `get_entry` | read | Reads one business record | High | No |
| `list_database_objects` | read | Reads database metadata | High | Yes |
| `list_database_columns` | read | Reads database metadata | High | Yes |
| `describe_database_object` | read | Reads database metadata | High | Yes |
| `plan_sql_query` | plan | Stores exact proposed SQL locally | High | No at plan time |
| `get_sql_query_plan` | read | Returns stored SQL plan | High | No |
| `cancel_sql_query_plan` | plan | Cancels local pending plan | Medium | No |
| `execute_sql_query` | execute | Executes bounded read-only SQL | High | Yes |
| `plan_create_entry` | plan | Stores proposed values locally | High | No |
| `apply_create_entry` | execute | Creates a Helix entry | Critical | No; write permission required |
| `plan_update_entry` | plan | Reads current and stores proposed values | High | No |
| `apply_update_entry` | execute | Updates a Helix entry | Critical | No; write permission required |
| `get_write_plan` | read | Returns stored current/proposed values | High | No |
| `cancel_write_plan` | plan | Cancels local pending plan | Medium | No |

The metadata and SQL administrator requirement comes from AR API. All form
operations still require the ordinary permissions of the selected BMC account.

## Target and health tools

### `list_targets`

Accepts optional `include_disabled` and returns DEV, QA, and PROD with safe
capabilities. It excludes hosts, URLs, ports, credentials, and complete policy
configuration. It is local, read-only, non-destructive, and idempotent.

### `health_check`

Requires `environment` and accepts `force_refresh`. It checks bridge liveness
and authorized local connectivity without querying entries or executing SQL.
The result contains only component status, latency, stable error codes, and
cache state.

## Form reads

### `list_forms`

Requires `environment`; accepts `name_contains`, `offset`, and `limit`. It
returns only form names visible to both the BMC account and gateway policy.

### `list_form_fields`

Requires `environment` and `form`; accepts `name_contains`, `offset`, and
`limit`. It returns policy-visible field `id`, `name`, and `datatype`. Field ID
is authoritative when duplicate names exist.

### `query_form`

Requires `environment`, `form`, and an explicit `fields` list. Optional inputs
are `qualification`, `sort`, `offset`, `limit`, and `include_total`. It returns
bounded rows and pagination metadata after policy, sensitive-field, and rate
checks.

### `get_entry`

Requires `environment`, `form`, `entry_id`, and explicit `fields`. It retrieves
one record without requiring a qualification and returns only requested,
policy-visible values.

These four tools access BMC Helix and are read-only, non-destructive, and
idempotent. Metadata may come from a bounded local cache; entry values never do.

## Database metadata

### `list_database_objects`

Requires `environment`. Optional inputs select schema, name fragment, object
kind, system schemas, offset, and limit. Results are filtered by the SQL object
policy.

### `list_database_columns`

Requires `environment`, `schema`, and `object_name`; accepts pagination. It
returns column name, type, nullability, and position.

### `describe_database_object`

Requires `environment`, `schema`, and `object_name`. It returns bounded object
identity, type, and column metadata with a `truncated` indicator.

These tools execute internal catalog SQL through AR API and therefore require
an AR System administrator account even though they do not read business rows.

## SQL planning and execution

### `plan_sql_query`

Requires `environment` and exact `sql`; accepts `limit`. It validates policy,
objects, aliases, bounds, and read-only structure, then stores a temporary plan.
It performs no SQL call, so administrator permission is not verified yet.

The result includes the exact SQL, environment, limit, `plan_id`, digest,
status, expiry, server UTC time, and remaining seconds. The client must display
the plan and end the turn without execution.

### `get_sql_query_plan`

Requires `environment` and `plan_id`. After approval, the client retrieves the
same plan and verifies that it remains pending rather than creating a
replacement.

### `execute_sql_query`

Requires `environment`, `plan_id`, and `plan_digest`. It executes the exact
pending plan once through AR API. The bridge repeats SELECT-only validation and
requires administrator permission. Results contain approved columns, bounded
positional rows, row count, applied limit, and truncation state.

### `cancel_sql_query_plan`

Requires `environment` and `plan_id`. It invalidates a pending plan without
executing SQL.

Persisted SQL plans are encrypted. A completed, cancelled, expired, or
digest-mismatched plan cannot be executed.

## Human-approved writes

### `plan_create_entry`

Requires `environment`, `form`, `values`, and `reason`. It validates exact
create allowlists and stores normalized proposed values without calling Helix.

### `apply_create_entry`

Requires `environment`, `plan_id`, and `plan_digest`. It creates exactly the
approved entry. A successful AR API create may return `entry_id: null` for a
form that does not provide an identifier.

### `plan_update_entry`

Requires `environment`, `form`, `entry_id`, `values`, and `reason`. It reads
the current values and `Modified Date`, then stores a digest-bound optimistic
precondition without changing the entry.

### `apply_update_entry`

Requires `environment`, `plan_id`, and `plan_digest`. It updates the entry only
when the current `Modified Date` still matches the reviewed plan.

### `get_write_plan`

Requires `environment` and `plan_id`. It returns the reviewable plan state,
including current and proposed values when the pending operation needs them.

### `cancel_write_plan`

Requires `environment` and `plan_id`. It invalidates a pending plan without
writing to Helix.

Apply tools must never be called in the same turn as plan creation. Any
transport or protocol uncertainty during apply permanently changes the plan to
`outcome_unknown`; writes are never retried automatically.

## Audit and errors

Every tool call produces one closed-schema audit event without arguments or
payloads. Failures return a stable code and `operation_id`; native exception
text remains private.

Notable safe errors include `FORM_NOT_FOUND`, `FORM_FIELD_AMBIGUOUS`,
`FORM_FIELD_NOT_QUERYABLE`, `ARAPI_ADMIN_REQUIRED`,
`DATABASE_QUERY_ALIAS_REQUIRED`, `DATABASE_QUERY_ALIAS_INVALID`, and
`DATABASE_QUERY_WILDCARD_NOT_ALLOWED`.

## Transport and lifecycle

The default transport is `stdio`:

```text
helix-mcp
```

`streamable_http` uses `/mcp`, JSON responses, and stateless mode. Until MCP
client authentication is implemented, its listener is restricted to loopback.

Application lifespan starts the managed Java bridge only when needed and stops
only the child process it owns.
