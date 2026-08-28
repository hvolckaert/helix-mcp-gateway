# MCP end-to-end validation over stdio

The project has two real-transport validation levels: a reproducible local E2E
used in CI and an opt-in live check against an authorized environment.

## Reproducible local E2E

`tests/e2e/test_stdio_local.py` builds an isolated temporary stack:

1. compile the production `ArapiBridge.java` against minimal AR API doubles;
2. create fictional JAR manifests, policy, and credentials;
3. start `python -m helix_mcp.server` as a fresh process;
4. connect an MCP `ClientSession` through `stdio`;
5. let the normal lifespan manage the temporary Java bridge.

It does not read the user's `.env`, use BMC JARs, or connect to an external
service. Records live only in the test Java process.

```text
HELIX_LOCAL_E2E_TESTS=1 \
python -m pytest -q -s -m local_e2e tests/e2e/test_stdio_local.py
```

The test covers the exact 19-tool catalog, explicit targeting, bridge health,
form metadata, queries, direct reads, policy errors, SQL planning/execution,
create/update plan application, cancellation, idempotency, persistence across
restart, optimistic conflicts, PROD write rejection, closed-schema audit, and
clean process shutdown.

## Opt-in live E2E

`tests/e2e/test_stdio_live.py` is skipped unless `HELIX_LIVE_TESTS=1`. It
requires the authorized local AR API runtime and network connectivity.

The default live flow calls `list_targets`, `health_check`, `list_forms`,
`list_form_fields`, `query_form`, and `get_entry`. Optional environment
variables enable database metadata discovery and create/cancel a write plan,
but the automated live test never invokes an `apply_*` tool.

Use only a form, qualification, and fields explicitly approved for testing.
Do not publish the values supplied through `HELIX_LIVE_*` variables or the
returned data.

Real writes remain manual, use a designated non-production test record, and
require review and explicit approval of the exact plan. Automated or manual
tests must never write to PROD.
