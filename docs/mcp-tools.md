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
| `get_form_field_definition` | read | Reads one policy-visible stored field definition | Medium | No |
| `list_form_views` | read | Lists views of an allowed form | Medium | No |
| `get_form_view_definition` | read | Reads declared display properties of allowed fields | Medium | No |
| `get_form_field_selection_values` | read | Reads one policy-visible ENUM definition | Medium | No |
| `get_form_field_menu_metadata` | read | Reads one policy-visible CHAR menu association | Medium | No |
| `resolve_form_field_menu_values` | read | Expands bounded values from an allowed field's CHAR menu | High | No |
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

## Read provenance

All 18 tools with the MCP read-only annotation return a required `provenance`
object alongside their existing result fields. This includes
`execute_sql_query`, `get_sql_query_plan` and `get_write_plan`. Tool inputs,
authorization, approval digests and the seven mutation/planning responses do
not change. Consumers with closed response schemas must accept this additional
field when adopting a release containing this change.

| Field | Meaning |
| --- | --- |
| `schema_version` | Provenance contract version, initially `1`. |
| `environment` | Explicit `dev`, `qa` or `prod`; `null` only for the multi-environment `list_targets` catalog. |
| `source` | Tool operation, backend (`arapi`, `gateway` or `health`) and applicable authorized identifiers: form, entry, field, view, database object or plan/digest. Unavailable identifiers are `null`; none are inferred. |
| `read_at` | UTC time when this Gateway read operation completed. |
| `data_read_at` | Original Gateway acquisition time for a cached primary result; otherwise the current read completion time. Neither timestamp is a BMC modification time or a transactional snapshot guarantee. |
| `cached` | Whether the primary result was served from a Gateway cache. |
| `cache_reads` | Consulted form, field or health caches, each with its original `read_at` and hit flag. A fresh field/view definition can have `cached=false` while relying on cached field metadata. |
| `policy_versions` | Map from environment to `sha256:` fingerprint of its effective policy rules. A normal read has one entry. `list_targets` includes exactly the returned environments, even when disabled environments are explicitly requested. An empty catalog has an empty map. |

Cache acquisition timestamps are stored with the cache entries, including when
the cache was populated outside an MCP call. Hits retain them; expiration and
explicit health refresh acquire a new timestamp. Cache TTLs still use the
monotonic clock. Timestamps describe the Gateway's observations only: upstream
AR API or BMC caches and source-record update times are not observable here.

Policy fingerprints hash the complete validated policy rules with sorted map
keys and unordered allowlist/denylist entries, under the `helix-policy-v1`
hashing contract. Policy labels, connection endpoints and credentials are not
part of the fingerprint. Changing rule values changes the fingerprint; merely
renaming the policy or reordering its lists does not. These hashes identify
Gateway policy configuration, not effective BMC account permissions, proof of
ownership, a signature or permission for a subsequent operation.

Source identifiers come from the authorized result. Provenance does not copy
SQL text, qualifications, field values, menu values, credentials or complete
policy rules. For SQL execution the approved plan ID and digest identify the
query. For collections, the block applies to the returned page; existing row,
field and view identities stay in the payload rather than being duplicated.
`get_*_plan` timestamps describe reading the local plan store, not refreshing
any Helix values captured when the plan was created. Failed/cancelled calls
retain the existing sanitized error contract and do not emit success provenance.

Clients should preserve this block with exported or reused results and should
not substitute `read_at` for an older cached acquisition timestamp. No new
Gateway configuration, migration, network call or credential is needed.

## Target and health tools

### `list_targets`

Accepts optional `include_disabled` and returns DEV, QA, and PROD with safe
capabilities. It excludes hosts, URLs, ports, credentials, and complete policy
configuration. Each descriptor includes `availability`: `available`,
`credential_not_configured`, or `disabled`. Targets without credentials expose
no backends or operational capabilities. They are returned only when
`include_disabled=true`. The tool is local, read-only, non-destructive, and
idempotent.

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

### `get_form_field_definition`

Requires `environment`, `form`, and an exact numeric `field_id`. Returns the
stored field identity, field type, required/optional/system/display option,
create mode, set-field and audit option codes, typed default, declared group
permissions, and datatype-specific limits. Recognized option codes include
their AR API constant name; unknown codes keep their number and a null name.

Defaults distinguish `present`, `unset`, and `unsupported`. Empty strings,
whitespace, zero, and false are preserved. Decimal defaults and range endpoints
use exact decimal strings. Complex values and values exceeding the metadata
bound are marked unsupported rather than stringified or silently truncated.

Limit properties include `max_length`, `length_units` (0 bytes, 1 characters),
`pattern`, CLOB storage, QBE and full-text options for CHAR; `low_range` and
`high_range` for INTEGER; ranges and precision for REAL/DECIMAL; and attachment
size/type/full-text options. ENUM returns its list style; use
`get_form_field_selection_values` for labels, and the existing menu tools for
menu associations and values. CURRENCY ranges and precision are available;
its allowable/functional currency lists are explicitly unsupported, and the
limit section reports partial coverage. Other unimplemented limit structures
report unsupported, including absent limit objects; they are not represented
as unlimited constraints.

These are declared metadata, not effective user permissions or a guarantee of
editability. View properties, gateway write policy, server permissions, and
workflow can impose additional restrictions. The service authorizes the form
and field before requesting detailed values, rechecks field identity afterward,
and applies the same sensitive-field exclusions and rate limits as other reads.
Defaults and permission payloads are omitted from diagnostic representations.

### `list_form_views`

Requires `environment` and `form`; accepts `name_contains`, `offset`, and
`limit` (default 100, additionally bounded by the target's maximum-row policy).
Returns each view's numeric ID, name, locale, and AR API view-type code and
symbol. Use the ID to distinguish standard and progressive views; a name alone
does not establish whether a view is PWA. The catalog is bounded to 4096 views.

### `get_form_view_definition`

Requires `environment`, `form`, and an exact `view_id`. Accepts optional unique
`field_ids` (1–128), `offset`, and `limit` (1–128, default 25, additionally
bounded by the target's maximum-row policy). Without `field_ids`, pagination
covers the policy-visible field catalog; with them, it covers the supplied
IDs in request order. `total` counts those authorized fields, including fields
absent from the selected view. Each field reports `present_in_view`. The
returned field array is sorted by ID within each page. An offset beyond the
catalog returns the view header and properties with an empty field array.

The response preserves view-level properties and separates each field's
`shared_properties` (display instance 0) from `view_properties`. It does not
merge them or infer inherited defaults. Each configured property has its exact
ID, known AR API constant name, typed value/status, and an optional value symbol.
Supported properties include labels, visibility, editing mode (`AR_DPROP_ENABLE`),
bounding boxes, row count, character display type, expand-box mode,
custom CSS style names, tab order, layout/parent
attributes, desktop/tablet/mobile visibility, and the progressive, padding,
margin and panel-alignment families.
Coordinates retain their normalized AR API units, without conversion to pixels.
Developer Studio applies a client-specific coordinate transformation; its X/Y
and width/height values cannot be compared directly with the raw bounding box.
Custom style names are returned as metadata, never evaluated as CSS or HTML.
Editing codes distinguish default, read-only, read/write and disabled.
Character display types and expand-box modes also include known symbolic codes.

`AR_DPROP_TAB_ORDER` is the stored value. Developer Studio may reindex fields
within their parent container and show a different computed tab order. This
tool does not reproduce that client-side index: authorized metadata can omit
fields excluded by gateway policy, which also changes such a calculation.

This is bounded coverage of stored metadata, not a complete reconstruction of
the rendered UI. Properties outside these families, complex values not yet
implemented, and strings longer than 8192 characters return `unsupported`;
their raw values are omitted. Configured null values return `unset`; a missing
property is absent from the array. References to parent fields excluded by
gateway policy return `redacted`. At most 512 properties per display instance
are accepted. View and field identities are checked against their catalogs,
and only authorized field IDs are sent to the detailed bridge lookup.

View settings do not establish effective editability: field options, user
permissions, gateway write policy and runtime workflow still apply. These
tools neither run UI workflow nor read entries. Property values are excluded
from diagnostic representations. The existing lightweight form/field catalogs
and Dashboard selectors retain their existing behavior.

### `get_form_field_selection_values`

Requires `environment`, `form`, and an exact numeric `field_id`. It returns
the policy-visible field identity and the configured ENUM number-to-label
mapping. Regular and custom selection lists are identified explicitly; custom
numbers are returned as configured and must not be inferred from list order.
For a non-selection field, `selection_style` is null and `values` is empty.
The form and field must pass the same account, policy, sensitive-field, and
rate-limit checks as other form metadata reads.

### `get_form_field_menu_metadata`

Requires `environment`, `form`, and an exact numeric `field_id`. For a
policy-visible `CHAR` field, it reports whether a character menu is associated
and, when present, returns the menu name and its `append` or `overwrite`
display style. Other field types and CHAR fields without a menu return
`has_menu=false` with null menu metadata.

This tool does not load, expand, or execute the menu definition. In
particular, it does not evaluate dynamic, query, or SQL menu contents. The
form and field pass the same account, policy, sensitive-field, and rate-limit
checks as other form metadata reads.

### `resolve_form_field_menu_values`

Requires `environment`, `form`, and an exact numeric `field_id`; accepts a
`limit` from 1 to 500, defaulting to 100 and additionally bounded by the
target's maximum-row policy. It first verifies that the form and field are
policy-visible, then resolves only the character menu actually associated with
that field. Arbitrary menu names are not accepted.

The response identifies the menu type and returns leaf `label`, `value`, and
`path` elements so nested menus retain their hierarchy. `truncated=true`
means the caller must refine the task rather than assume the returned set is
complete. Static menus return their configured values. Query, SQL,
data-dictionary, file, and server-side menus are expanded by AR System with
the selected account and can read their backing data. The operation never
modifies an entry, but its values can still contain business data and must be
handled like other form-read results. Menus that require unavailable runtime
field context may fail safely instead of returning guessed values.

### `query_form`

Requires `environment`, `form`, and an explicit `fields` list. Optional inputs
are `qualification`, `sort`, `offset`, `limit`, and `include_total`. It returns
bounded rows and pagination metadata after policy, sensitive-field, and rate
checks.

### `get_entry`

Requires `environment`, `form`, `entry_id`, and explicit `fields`. It retrieves
one record without requiring a qualification and returns only requested,
policy-visible values.

These form-read tools access BMC Helix and are read-only, non-destructive, and
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
The result includes post-write `verification`; a missing ID is applied but
cannot be verified. See [verification](form-writes.md#verification).

### `plan_update_entry`

Requires `environment`, `form`, `entry_id`, `values`, and `reason`. It reads
the current values and `Modified Date`, then stores digest-bound timestamp and
exact requested-field value preconditions without changing the entry.

### `apply_update_entry`

Requires `environment`, `plan_id`, and `plan_digest`. It updates the entry only
when both `Modified Date` and the requested original field snapshot still match
the reviewed plan. These optimistic checks do not eliminate the interval in
which an external writer can change the entry before the server write.
After a confirmed write, `verification` reports matching, differing and
unverified fields with the read time, without returning business values.
`status: applied` is distinct from `verification.status`; an unavailable
verification does not authorize retrying the write.

### `get_write_plan`

Requires `environment` and `plan_id`. It returns the reviewable plan state,
including current and proposed values when the pending operation needs them.

### `cancel_write_plan`

Requires `environment` and `plan_id`. It invalidates a pending plan without
writing to Helix.

Apply tools must never be called in the same turn as plan creation. Any
transport or protocol uncertainty in the write request permanently changes the plan to
`outcome_unknown`; writes are never retried automatically.
Verification read failures after confirmed writes instead return
`verification.status: unavailable` with `status: applied`.

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

`streamable_http` uses `/mcp`, JSON responses, stateless mode, and bearer-token
authentication. Configure a private token of at least 32 characters with
`HELIX_MCP_HTTP_BEARER_TOKEN`; startup fails closed when it is absent. The
listener remains restricted to loopback.

Application lifespan starts the managed Java bridge only when needed and stops
only the child process it owns.
