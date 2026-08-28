# AR API bridge

The local Java bridge provides controlled access to AR System forms through
the official AR API. It supports form and field discovery, bounded queries,
direct entry reads, create and update primitives, and read-only SQL through
`ARGetListSQL`.

## Security boundaries

- The HTTP listener accepts loopback clients only.
- Only `GET /health` and the documented `POST /v1/*` endpoints are exposed.
- Request bodies are limited to 64 KiB and responses to 8 MiB.
- Reads are limited to 128 fields and bounded result sets.
- Writes accept at most 32 scalar fields with bounded values.
- Bodies, credentials, responses, and exception text are never logged.
- One AR API session is created per request and closed in `finally`.
- Deletion, attachments, and bulk operations are not implemented.

The `POST` endpoints use `application/x-www-form-urlencoded`. Query requests
require explicit pagination. When the qualification is empty, the bridge uses
a condition on the core Request ID field so the request also works on servers
that reject unqualified searches.

`/v1/entries/prepare-update` reads the requested values and `Modified Date` in
one operation. `/v1/entries/update` serializes updates per entry, reads the
timestamp again, and returns HTTP 409 before `setEntry` if it changed. Native
AR error 309 is also mapped to a conflict.

The bridge supports scalar CHAR, DIARY, INTEGER, ENUM, BITMASK, REAL, DECIMAL,
ULONG, TIME, DATE, and TIME_OF_DAY values. A successful create on a display,
join, or compound form may return `{"entry_id":null}` when AR System does not
provide an identifier. The gateway does not attempt an unsafe lookup to infer
one.

## SQL

`/v1/sql/query` requires `ARServerUser.isAdministrator()`. It performs a second
SELECT-only check, rejects comments, multiple statements, and write keywords,
then calls `getListSQL(sql, limit + 1, false)`. Rows are positional; Python
matches them to aliases from the previously approved SQL plan.

A non-administrator account, or AR error 304, produces HTTP 403 with the stable
code `ARAPI_ADMIN_REQUIRED`. Credentials travel only from the Python process
over loopback and are not retained by the bridge.

## Build

The official BMC JARs are not copied or committed. Point the build at an
authorized local installation:

```text
HELIX_ARAPI_JAR=/path/to/arapi.jar ./build.sh
```

The output is `build/helix-arapi-bridge.jar`.

For a wheel installation, use `helix-mcp-setup` or
`helix-mcp-build-bridge`. Both validate the local AR API libraries and replace
the compiled bridge only after a successful build.

The currently supported runtime uses the AR API 21.30.x libraries installed by
BMC Developer Studio. The preflight expects one `arapi`, one `arapiext`, and
one `arlogger` JAR from the same supported series. No BMC binary is included in
project artifacts.

## Automated tests

The isolated suite compiles the production `ArapiBridge.java` against minimal
test doubles under `src/test`:

```text
sh arapi-bridge/test.sh
```

It covers JSON serialization, bounds, invalid input, scalar writes, loopback
host validation, session cleanup, error redaction, SELECT-only SQL,
administrator checks, and optimistic update preconditions. Java 17 warnings
are treated as errors. Test doubles are never packaged in the production JAR
or Python wheel.

The opt-in Python contract starts a temporary Java runtime and connects the
real Python client:

```text
HELIX_JAVA_BRIDGE_TESTS=1 \
python -m pytest -q -s -m java_bridge \
tests/integration/test_arapi_java_bridge.py
```

All traffic remains on a random loopback port and temporary JARs are removed
after the test.

## Manual execution

The classpath must contain the bridge and the locally installed AR API
dependencies:

```text
java -cp "build/helix-arapi-bridge.jar:/path/to/lib/*" \
  com.example.helix.bridge.ArapiBridge
```

Optional variables:

- `HELIX_ARAPI_BRIDGE_PORT`, default `8090`;
- `HELIX_ARAPI_BRIDGE_THREADS`, default `4`.

Normal gateway startup manages this child process automatically.
