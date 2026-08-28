# Development and validation

## Requirements

- Python 3.12;
- Java 17 or later for the bridge;
- authorized local AR API and connectivity components only for live tests.

Automated tests do not read the user's `.env` or contact BMC Helix. Java tests
compile the production bridge against isolated AR API doubles and use temporary
loopback listeners.

## Local environment

```text
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
```

On native Windows, use `.venv\Scripts\python.exe`.

## Checks before a commit

```text
.venv/bin/python -m compileall -q src tests hatch_build.py
.venv/bin/python -m pip check
.venv/bin/python -m ruff format --check src tests hatch_build.py
.venv/bin/python -m ruff check src tests hatch_build.py
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
sh arapi-bridge/test.sh
git diff --check
```

The opt-in Java network contract is:

```text
HELIX_JAVA_BRIDGE_TESTS=1 \
.venv/bin/python -m pytest -q -s -m java_bridge \
tests/integration/test_arapi_java_bridge.py
```

The reproducible MCP E2E starts the real Python server over `stdio`, the real
bridge source compiled against doubles, and an in-memory fictional dataset:

```text
HELIX_LOCAL_E2E_TESTS=1 \
.venv/bin/python -m pytest -q -s -m local_e2e \
tests/e2e/test_stdio_local.py
```

It covers all 19 tools, policies, planning, create/update application,
idempotency, cancellation, optimistic conflicts, audit, and encrypted plan
recovery across process restarts.

The integration suite also builds two wheels from equivalent trees with
different source modes. Their bytes must match and every wheel entry must use
mode `0644`.

## Continuous integration

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`. Python
is checked on Linux and Windows. A Java 17 job runs the isolated bridge suite,
and CI also executes the loopback Python/Java contract and reproducible stdio
E2E. Workflows have read-only permissions and no Helix secrets.

## Test layout

- `tests/unit`: isolated behavior;
- `tests/contract`: public MCP contracts;
- `tests/security`: redaction and security boundaries;
- `tests/integration`: real local components over simulated transports;
- `tests/e2e/test_stdio_local.py`: complete isolated MCP/Python/Java stack;
- `tests/e2e/test_stdio_live.py`: explicit authorized live validation;
- `arapi-bridge/src/test`: minimal AR API doubles and Java tests.

## Live validation

Live tests are disabled by default, never run in CI, and must use an explicitly
authorized non-production target with fictional or approved data:

```text
HELIX_LIVE_TESTS=1 \
HELIX_LIVE_ENVIRONMENT=dev \
HELIX_LIVE_FORM='Example:ReadableForm' \
HELIX_LIVE_FIELDS='Request ID,Status' \
HELIX_LIVE_QUALIFICATION="'Status' = \"Enabled\"" \
.venv/bin/python -m pytest -q -s -m live tests/e2e/test_stdio_live.py
```

Live validation requires an explicitly approved record selector. Set
`HELIX_LIVE_QUALIFICATION`, `HELIX_LIVE_ENTRY_ID`, or both. When only an entry
ID is supplied, the test derives an exact qualification from the form's core
field ID 1. When both are supplied, the queried record must match the entry
ID. The test refuses to perform an unqualified record read.

Never copy returned identifiers, values, qualifications, endpoints, or account
details into documentation, screenshots, issues, or CI logs.
