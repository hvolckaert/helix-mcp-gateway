# Read-only SQL through AR API

The gateway executes SQL through `ARServerUser.getListSQL()`. It does not open
a direct database connection or require separate database credentials. Each
environment reuses its AR API credential.

SQL execution and database metadata require an AR System administrator
account. A non-administrator may continue using permitted form tools; affected
SQL operations return `ARAPI_ADMIN_REQUIRED`.

## Policy and discovery

A policy may allow all SQL objects:

```yaml
allow_sql: true
allow_all_sql_objects: true
allowed_sql_objects: []
```

or use an exact `allowed_sql_objects` list. PROD should keep `allow_sql: false`.

Recommended workflow:

1. `list_database_objects`;
2. `list_database_columns` or `describe_database_object`;
3. `plan_sql_query`;
4. display the exact SQL and end the turn;
5. obtain explicit user approval;
6. retrieve and execute the same pending plan.

## SQL contract

`plan_sql_query` accepts `environment`, `sql`, and an optional `limit`. Plan
creation validates policy and SQL shape but does not prove that the account is
an administrator. That permission is enforced when AR API metadata or SQL is
executed.

`getListSQL` does not support bound parameters, so every literal appears in
the SQL shown for review. Placeholders are rejected.

Every selected expression must use an explicit, safe, unique alias:

```sql
SELECT
  item_name AS item_name,
  COUNT(*) AS related_count
FROM public.example_item
GROUP BY item_name
```

Missing aliases return `DATABASE_QUERY_ALIAS_REQUIRED`; duplicate or unsafe
aliases return `DATABASE_QUERY_ALIAS_INVALID`. The rejected SQL is never
included in those errors.

Output wildcards such as `SELECT *` are rejected. `COUNT(*) AS alias` is
allowed because it produces one named column. The guard also rejects multiple
statements, comments, DML, `SELECT INTO`, row locks, placeholders, and known
side-effect or delay functions. Objects are checked against policy before a
plan is stored, and the Java bridge repeats a SELECT-only validation.

AR API returns positional rows. Python derives output names from approved
aliases and rejects any row with the wrong width. The bridge requests
`limit + 1` rows, returns at most `limit`, and uses the extra row only to set
`truncated`.

`query_timeout_seconds` limits the gateway's HTTP wait. A timeout does not
guarantee immediate server-side cancellation, so queries should remain
selective and tightly bounded.

Current metadata discovery queries use PostgreSQL catalog views through AR
API. Other database engines require a specific metadata implementation; the
gateway still never connects directly to the database.

Persisted SQL plans are encrypted. SQL, literals, rows, and credentials are
excluded from audit and observability output.
