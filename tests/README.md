# Tests

- `unit`: isolated behavior;
- `contract`: MCP schemas and client contracts;
- `integration`: connected local components;
- `e2e`: complete reproducible or explicitly live MCP flows;
- `security`: target isolation, adversarial SQL, and secret redaction;
- `fixtures`: fictional, non-sensitive test data.

The Java bridge has an independent suite under `arapi-bridge/src/test`:

```text
sh arapi-bridge/test.sh
```

It compiles the production source against minimal AR API test doubles and does
not require proprietary libraries or external connectivity.

The opt-in Python-to-Java contract starts a temporary loopback listener:

```text
HELIX_JAVA_BRIDGE_TESTS=1 \
python -m pytest -q -s -m java_bridge \
tests/integration/test_arapi_java_bridge.py
```

The reproducible local E2E adds the real MCP server over `stdio` and exercises
all 19 tools, including plan/apply flows and encrypted persistence:

```text
HELIX_LOCAL_E2E_TESTS=1 \
python -m pytest -q -s -m local_e2e tests/e2e/test_stdio_local.py
```

All records and credentials in these suites are fictional and exist only in
temporary files or memory. Automated writes never target PROD. Live validation
is documented in [`docs/stdio-e2e.md`](../docs/stdio-e2e.md) and must be
enabled explicitly against an authorized environment.

`tests/integration/test_reproducible_wheel.py` also verifies that equivalent
source trees produce byte-identical wheels regardless of source file modes.
