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

On the first application of an approved plan,
`apply_create_entry(environment, plan_id, plan_digest)` calls
`ARServerUser.createEntry(form, values)` once. When that call returns a
usable AR entry ID, the bridge preserves it, including compound IDs such as
`0001|0002`. The gateway uses the ID for the verification read on the same
target and form. Both the apply result and a subsequent `get_write_plan`
report the ID stored for that plan. Two plans can request identical values
and have the same digest. Their plan IDs and results remain independent.

### CMDB join forms

When a successful native return contains no usable ID, the result is
`applied` with `entry_id: null`, `verification.status: unavailable`, reason
`entry_id_unavailable`, and all requested fields in `unverified_fields`.
The gateway performs no verification read in this case. A name, a CMDB
`InstanceId` discovered later, or the newest row cannot identify the record
created by the approved call. A lost create response is a distinct terminal
outcome, `outcome_unknown`. Neither case permits a second create.

On 2026-11-21, two separately approved synthetic plans in DEV
(`BMC.SAMPLE`) each returned `applied` and `reused_result: false` after one
attempt. For both calls the bridge classified the native return as `blank`
(empty or whitespace-only). All three ID presence flags were false.
Both MCP results had `entry_id: null` and `verification.status: unavailable`
with reason `entry_id_unavailable`.

| DEV join form | Requested fields left unverified |
| --- | --- |
| `BMC.CORE:BMC_ComputerSystem` | `DatasetId`, `Name` |
| `BMC.CORE:BMC_Dependency` | `DatasetId`, `Name`, `Source.DatasetId`, `Source.ClassId`, `Source.InstanceId`, `Destination.DatasetId`, `Destination.ClassId`, `Destination.InstanceId` |

#### Read-only review of the DEV create workflow

On the same date, ARAPI reads of the AR System schema and filter metadata
identified the following configuration. Both joins match their members on
field 179 (`InstanceId`). The counts cover enabled Submit filters mapped
directly to each join.

| Join in `BMC.CORE` | Primary member | Secondary member | Submit filters |
| --- | --- | --- | ---: |
| `BMC_ComputerSystem` | `BMC_ComputerSystem_` | `BMC_BaseElement` | 16 |
| `BMC_Dependency` | `BMC_Dependency_` | `BMC_BaseRelationship` | 3 |

Both joins have three `CMDB:Instance:InvokeCMDBEngine` filters with suffixes
`01`, `02` and `03`. They invoke phases 1, 2 and 3 of `bmc.cmdb.cmdbEngine`.
The first two assign the engine result to the display-only field `zCMDBEngInvokeResult`.
The third invokes the engine through a Run Process action. These definitions
describe the configured workflow. The evidence for the actual return from
each approved call is the `blank` category recorded above.

BMC documents a generated entry ID for a base-form create. Its Java example
uses a regular form. Submitting through a join runs filters that determine
which member entries to create or modify. Reading an existing join row
exposes its members' request IDs separated by `|`. The documentation gives
no guarantee that submitting to the join returns that composite ID.
[ARCreateEntry](https://docs.helixops.ai/bin/Service-Management/Innovation-Suite/BMC-Helix-Innovation-Suite/is252/Developing-an-API-program/AR-System-C-API-functions/Function-descriptions/ARCreateEntry/) · [Join forms](https://docs.helixops.ai/bin/Service-Management/Innovation-Suite/BMC-Helix-Innovation-Suite/is262/Developing-applications-by-using-Developer-Studio/Developing-the-application-interface/Defining-forms-to-hold-and-display-data/Join-forms/) · [Java create and read example](https://docs.helixops.ai/bin/Service-Management/Innovation-Suite/BMC-Helix-Innovation-Suite/is262/Developing-an-API-program/AR-System-Java-API-overview/Java-API-sample-code-for-managing-AR-System-records/)

In the Java SDK installed for the gateway, `ARServerUser.createEntry`
returns the same ID that `Entry.getEntryId()` and `ARServerUser.getLastID()`
expose immediately after the call. When the native return is blank, both
accessors yield a blank value. The display-only workflow result is outside
this method's return contract.

**Verification of both historical AR join submissions remains open.** The ARAPI
path exposed no documented way to bind either blank return to a specific
join row. For the computer system, closure requires an ID attributable to
the create and an exact-ID read confirming `DatasetId` and `Name`. For the
dependency, both endpoint CIs were read before approval. Closure requires an
exact-ID read of the **relationship**, confirmation of its requested fields,
and a check of each stored endpoint's dataset, class and instance against
its CI. The native returns for earlier, uninstrumented attempts remain
unknown. [DEV handoff](https://github.com/hvolckaert/helix-agent-skills/blob/5d4798f/docs/gateway-create-verification.md)

The final create audit event records the native return category
(`accepted`, `absent`, `blank`, `too_long`, or `control_character`) and
three presence flags: `bridge_has_entry_id`, `client_has_entry_id` and
`result_has_entry_id`. An older bridge yields `unreported` for the category.
A replay emits only the result flag. If a response is lost, missing flags
represent unobserved boundaries. No raw IDs, credentials or field values
are written to the audit event.

### Creating CMDB instances with the Java SDK

New plans for `BMC.CORE:BMC_ComputerSystem` and `BMC.CORE:BMC_Dependency` use
`operation: create` with `create_backend: cmdb_sdk`. The stored operation binds
the backend to the plan digest. Install a matching BMC `cmdbapi*.jar` in the
configured ARAPI library directory and rebuild the bridge. Planning generates
one CMDB GUID and includes it in the proposed values and the plan digest.
The same field permission checks cover the generated `InstanceId`.
Missing CMDB support stops planning. Existing AR plans retain their operation.

After approval the gateway starts a CMDB client-managed transaction, checks
that the approved key has no visible instance in that class and dataset,
submits one create with that key, and reads the instance back by key inside
the transaction. It checks the identity before committing. The value of
`CMDBInstance.getId()` alone is insufficient because the SDK can initialize
it before sending the create. An uncertain commit is terminal.
A successful commit returns `instance_id` even if a subsequent AR join read
is denied or ambiguous. `entry_id` holds the exact compound AR request ID
when visible. All existing apply, verification and replay rules apply.
On November 21, 2026, two separately approved, single-attempt DEV plans used
the CMDB Java SDK in the sample dataset. Each plan used its own reserved
identity. The exact-key SDK read and the independent compound-ID AR read
agreed for each committed instance.

| SDK create in DEV | Exact-record verification |
| --- | --- |
| Computer system | All three requested fields matched, including the reserved instance identity. |
| Dependency | All nine requested fields matched on the relationship itself. Its stored source and destination each matched an independently read CI by dataset, class and instance. |

This evidence covers these two SDK creates on the tested installation. The two
historical blank AR join returns still have no attributable compound ID.

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
