# Two-phase form writes

Helix entry creation and update use the mandatory workflow:

```text
plan -> human review -> explicit approval -> apply
```

Planning never modifies Helix. Applying requires the exact `plan_id` and
`plan_digest` returned for the reviewed plan.

## Policy

Each policy declares one access mode:

- `read_only`: create and update are structurally forbidden;
- `read_write`: forms and fields must pass either their exact allowlist or an
  explicitly enabled broad write scope.

Create and update scopes are independent. A field permitted during create does
not become updateable automatically. With explicit `writable_forms`, each form
must use either an exact field mapping or appear in the corresponding
`allow_all_creatable_fields_for_forms` or
`allow_all_updatable_fields_for_forms` list. Those lists permit every
non-sensitive requested field only for the named operation and form.

`allow_all_writable_forms` permits every form already included in the general
form scope. That dynamic form mode requires both global
`allow_all_creatable_fields` and `allow_all_updatable_fields`; exact or
per-form field scopes cannot completely describe forms that may appear later.
Sensitive-field protections are enforced at runtime before either exact or
broad field authorization. New forms and fields can enter a broad scope
automatically, so broad write options default to false. Every environment,
including PROD, starts read-only but may explicitly enable the same controlled
write workflow.

Deletion, attachments, and bulk writes are not exposed.

## Create

`plan_create_entry` receives `environment`, `form`, `values`, and a reason of
at least ten characters. After policy validation, it stores a temporary plan
and returns normalized values for review without calling Helix.

After approval, `apply_create_entry` supplies the environment, plan ID, and
digest. The Java bridge invokes `ARServerUser.createEntry()` with typed scalar
values. A valid create on a display, join, or compound form may return
`entry_id: null`; the gateway does not perform a potentially ambiguous lookup
to infer an ID.

## Update

`plan_update_entry` also receives `entry_id`. It reads only proposed fields and
the core `Modified Date` through AR API, returns current values for review, and
binds the timestamp and an exact requested-field fingerprint into the plan
digest. The fingerprint includes field IDs, datatypes, presence and normalized
values, is independent of request field order, and does not round decimals
through Python floats. Review values preserve nulls and exact decimal strings.

During apply, the bridge serializes updates for the same entry, reads
`Modified Date` and the requested original values again, and calls `setEntry()`
only when both match. A mismatch, including one within the same timestamp
second, or native AR error 309 becomes `FORM_WRITE_CONFLICT` and requires a new
plan and approval. Changes outside the requested fields are detected only when
Modified Date changes.

This lock coordinates callers using the same bridge. An external writer can
still act between the comparison and `setEntry()`; the native AR timestamp
check also has finite resolution. This is not an atomic compare-and-set across
all clients. No multi-entry transaction or batch guarantee is provided.

## Verification

After AR API confirms a create/update, the gateway reads only the written
fields and compares them with the typed values sent to the server. The apply
result remains `status: applied` and adds a separate `verification` object:

| Verification status | Meaning |
| --- | --- |
| `matched` | All requested values matched the verification read. |
| `different` | At least one requested field differed; some others may be unverified. |
| `unavailable` | No difference was confirmed, but the read failed or some values could not be compared. |

The object lists `matched_fields`, `differing_fields`, `unverified_fields`, and
the bridge's UTC `read_at` when a read completed. It contains field names, not
original, proposed or observed business values. A completed read also includes
`provenance` identifying the environment, form/entry, read/acquisition times and
effective policy fingerprint. A replay preserves this historical observation,
including its provenance; `reused_result` identifies the stored apply result.
A difference can reflect server workflow or another writer; it does not
establish causation or trigger
automatic correction/rollback. Asynchronous workflow or later writes may
change the record after this observation.

Read failures, unsupported values, and creates without an addressable entry
ID explicitly leave verification unavailable. They do not turn a confirmed
write into a failed write or cause it to be replayed. Repeating an applied plan
returns its stored comparison and original observation time, not a new read.
If the write response itself is lost, existing `outcome_unknown` semantics
still apply. A fresh separately authorized read is needed to investigate.

Comparisons support bounded scalar originals, exact decimal numbers, dates and
times. Strings are limited to 8192 characters and fingerprint material to
131072 characters. Unsupported original values fail update planning closed;
they are never ignored or converted with an arbitrary object string. In
particular, complex diary histories cannot be used as update preconditions.
Diary append results are explicitly unverified rather than compared as scalar
replacements. Missing read-back fields are unverified, not treated as null.

### Compatibility

Update plans created before value fingerprints were introduced must be created
and approved again. Python and Java must be upgraded together: checked updates
use `/v1/entries/update-checked`, with no fallback to the old route. Old stored
applied results remain reusable but have `verification: null`; they are not
retroactively marked verified. Configuration and the MCP tool inputs do not
change.

## Persistence and failure semantics

- With `HELIX_WRITE_PLAN_DB_PATH` and `HELIX_WRITE_PLAN_KEY_PATH`, business
  values are encrypted in SQLite using AES-256-GCM.
- The separate key contains 32 bytes and must have private local permissions.
- Plans have bounded capacity and absolute expiry that does not reset after a
  restart.
- The SHA-256 digest binds operation, target, form, entry, values, reason, and
  optimistic timestamp and value preconditions.
- A successful repeated apply returns the stored result without sending a
  second request.
- A process interruption, timeout, transport error, or invalid response during
  apply changes the plan to `outcome_unknown`; it can never be retried.
- Terminal plans discard payloads, value fingerprints and reasons. Required
  terminal metadata remains encrypted for status and idempotency.
- The bridge accepts at most 32 supported scalar fields per write.
- Apply calls are rate-limited per target, share one budget across local
  managed MCP processes, and are never retried automatically.

`get_write_plan` inspects a plan and `cancel_write_plan` invalidates a pending
plan. Neither modifies Helix.

Automated write tests use an in-memory AR API double. Any real write requires a
designated non-production test record, an approved policy, and explicit human
approval. Public documentation and evidence must contain fictional values only.
