# Helix MCP Gateway

[![CI](https://github.com/hvolckaert/helix-mcp-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/hvolckaert/helix-mcp-gateway/actions/workflows/ci.yml)
[![Release](https://github.com/hvolckaert/helix-mcp-gateway/actions/workflows/release.yml/badge.svg)](https://github.com/hvolckaert/helix-mcp-gateway/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Policy-controlled MCP access to BMC Helix through the AR API.**

Helix MCP Gateway is an independent local MCP server that enables authorized
AI agents to interact with BMC Helix through the AR API. It provides bounded
reads, reviewable SQL, and human-approved write workflows across DEV, QA, and
PROD without redistributing proprietary BMC libraries.

The gateway is designed for BMC Helix professionals who already have an
authorized local runtime. It is not a hosted service and does not bypass the
permissions of the Helix account.

## Why it exists

Giving an AI agent direct, unrestricted access to an enterprise service
management platform is unsafe. This gateway places explicit policy and review
boundaries between the agent and Helix:

- every operation selects `dev`, `qa`, or `prod` explicitly;
- form and field access is constrained by policy;
- SQL is read-only, allowlisted, bounded, and executed only after review;
- creates and updates use a mandatory plan/review/apply workflow;
- PROD policies must remain read-only;
- audit, metrics, and public errors exclude arguments and business payloads.

Deletion, attachments, bulk writes, and direct database connections are not
exposed.

## Architecture

```text
MCP client
    |
    v
Python MCP gateway
    |  policy, limits, approval, audit
    v
Local Java bridge
    |
    v
BMC AR API -> authorized BMC Helix environment
```

The Python process communicates with a managed Java bridge over loopback. The
bridge uses the official AR API libraries already installed on the user's
machine. Those proprietary libraries are never copied into this repository,
the wheel, or a GitHub release.

## Capabilities

The server exposes 19 MCP tools covering:

- target discovery and health;
- form and field discovery;
- bounded form queries and direct entry reads;
- database metadata discovery through AR API;
- two-step read-only SQL planning and execution;
- two-step entry creation and update;
- plan inspection and cancellation.

See the complete [MCP tool catalog](docs/mcp-tools.md).

## Security model

- The effective BMC permissions always come from the selected Helix account.
- Configuration policies can reduce those permissions but cannot expand them.
- SQL execution and database metadata require an AR System administrator
  account.
- Write plans bind the environment, form, values, reason, and precondition to
  a digest that must be supplied during the later apply call.
- Interrupted writes become `outcome_unknown` and are never retried
  automatically.
- Optional persistent plans are encrypted with AES-256-GCM using a separate
  local key.
- Logs and audit records use closed schemas and omit tool arguments, SQL,
  credentials, form values, and returned rows.

Read [SECURITY.md](SECURITY.md) and the
[observability contract](docs/observability.md) before using the gateway with
a real environment.

## Requirements

- Python 3.12;
- a Java 17 or later JDK with the required compiler modules;
- an authorized BMC Developer Studio / AR API 21.30.x installation;
- a configured local BMC Helix Client Gateway connection to the permitted
  Helix environments;
- authorized per-environment credentials.

The exact supported setup and its current limitations are documented in
[Installation](docs/installation.md).

## Quick start

Install a verified release wheel in a virtual environment, then run guided
setup and preflight with explicit paths:

```text
python3.12 -m venv /path/to/helix-mcp/venv
/path/to/helix-mcp/venv/bin/python -m pip install /path/to/release.whl
/path/to/helix-mcp/venv/bin/helix-mcp-setup \
  --arapi-lib-dir "/authorized/path/to/DeveloperStudio/lib"
/path/to/helix-mcp/venv/bin/helix-mcp-check \
  --dotenv /path/returned/by/setup/.env \
  --live \
  --environment dev
```

The setup command generates local configuration, compiles the Java bridge
against the user's AR API installation, and creates the encryption key when
requested. It never overwrites existing credentials or configuration. Register
the absolute MCP command and `--dotenv` path returned by setup with the client.
The complete verified workflow is in [Installation](docs/installation.md).

For development from a checkout:

```text
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest -q
sh arapi-bridge/test.sh
```

Automated tests use fictional in-memory data and do not require access to BMC
Helix. Live tests are opt-in and must use an explicitly authorized target.

## Documentation

- [Documentation index](docs/README.md)
- [Installation](docs/installation.md)
- [Compatibility matrix](docs/compatibility.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Configuration](docs/configuration-loading.md)
- [Operations](docs/operations.md)
- [MCP tools](docs/mcp-tools.md)
- [Form reads](docs/form-query-service.md)
- [Human-approved writes](docs/form-writes.md)
- [SQL through AR API](docs/sql.md)
- [Development and testing](docs/development.md)

## Project status

The project is under active development. The current release line is suitable
for controlled evaluation by qualified BMC Helix professionals, not for
unattended production automation. Review the documented limitations and test
the exact policy against a non-production environment first.

## License and trademarks

Original project code is available under the [MIT License](LICENSE). BMC
software and libraries are not included and are governed by their respective
licenses.

This is an independent project. It is not affiliated with, sponsored by, or
endorsed by BMC Software, Inc. BMC, BMC Helix, and related product names are
trademarks of their respective owners.
