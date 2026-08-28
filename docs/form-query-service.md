# Form query service

## Search contract

`FormQueryService.search()` always receives an environment, form name,
explicit field list, limit, and offset. Qualification and sort expressions are
optional.

The service resolves the selected AR API backend and calls the local bridge:

```text
POST /v1/entries/query
```

The bridge resolves field names to IDs and invokes
`ARServerUser.getListEntryObjects()`. Pagination remains mandatory. For an
empty qualification, the bridge uses a condition on the core Request ID field
to support servers that reject unqualified searches.

## Direct reads

`FormQueryService.get_entry()` receives a form, entry ID, and explicit fields,
then calls:

```text
POST /v1/entries/get
```

It applies the same form policy, field allowlists, sensitive-field filtering,
and rate limit as search. Only requested fields may appear in the response.

## Validation order

Before external access, the service:

1. resolves the environment explicitly;
2. verifies `allow_form_reads`;
3. checks `allowed_forms` unless `allow_all_forms` is enabled;
4. rejects `sensitive_fields` and sensitive-name markers;
5. checks `allowed_fields_by_form` unless `allow_all_fields` is enabled;
6. applies `max_rows`;
7. applies the per-target rate limit;
8. verifies AR API availability.

The rate limiter is process-local. A distributed deployment would require a
shared coordinator.

## Metadata cache

`metadata_cache_ttl_seconds` controls a bounded process-local cache for form
names and field definitions. It stores no entries, field values,
qualifications, or credentials. Policy and rate limits still apply to cached
results. Set the value to `0` to disable the cache.

## Fields and form discovery

Queries request between 1 and 128 fields; implicit all-field reads are not
supported. Ambiguous names, control characters, and unsafe separators are
rejected.

`list_form_fields` calls `/v1/fields` and returns only field ID, name, and
datatype after policy filtering. Field ID is authoritative when a join or
synchronized form exposes duplicate names.

`list_forms` calls `ARServerUser.getListForm()` through the bridge. BMC first
limits results through account permissions; the service then applies its form
policy, name filter, pagination, row limit, and rate limit.

## Broad-read mode

A policy may explicitly enable:

```yaml
allow_form_reads: true
allow_all_forms: true
allow_all_fields: true
```

Account permissions, sensitive-field filters, row bounds, rate limits, and
bridge limits still apply. Broad mode cannot be mixed with equivalent positive
allowlists in the same policy.

## Response validation

The Python client strictly validates bridge response size, pagination, row
count, duplicate fields, and exact output shape. Query and result
representations omit qualifications and field values. Unexpected bridge
responses become sanitized stable errors.

Public examples must use fictional forms and values. Environment-specific
allowlists and live validation evidence belong in ignored local policy and
private test records, not in this document.
