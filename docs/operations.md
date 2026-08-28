# Local operation

The MCP process manages only the Java bridge it starts. If a healthy bridge
already exists at the configured loopback URL, the gateway reuses it and does
not stop it during shutdown.

## Local model prerequisites

Each user runs a separate MCP server and must have:

- an authorized local connectivity layer configured and running;
- BMC Developer Studio / AR API from a supported release;
- Java and Python 3.12;
- authorized credentials and network access for the selected environments.

The gateway references the installed AR API libraries directly. Do not copy
those JARs into Git, wheels, containers, or project installers.

## Sanitized preflight

```text
helix-mcp-check --dotenv /path/to/.env
```

Preflight checks strict configuration loading, Java, the compiled bridge,
required AR API JAR manifests, and credential structure. It does not read
forms, execute SQL, or modify Helix.

The command emits one JSON object containing closed check names, status, and
stable error codes. It excludes paths, hosts, ports, secret references,
usernames, credentials, and exception text. Exit status is zero for `ready`
and one for `not_ready`.

Opt-in live connectivity checks remain read-only:

```text
helix-mcp-check --dotenv /path/to/.env --live --environment dev
```

`--environment` may be repeated. Without it, all configured environments are
checked. The check may start the local bridge temporarily and stops it when
finished. It does not execute SQL.

## Startup

Wheel installations should use `helix-mcp-setup`. Development checkouts can
use the platform launchers:

```text
./scripts/start-helix-mcp.sh
```

```text
.\scripts\Start-HelixMcp.ps1
```

Both run preflight before starting the MCP transport.

## Shutdown and restart

The default `stdio` server remains in the foreground and belongs to the MCP
client that started it. It does not create a daemon or PID file. Use an orderly
client shutdown or `Ctrl+C`; never kill generic Java processes.

Lifecycle events are emitted as structured JSON to `stderr`. Optional audit,
metrics, and operation files use separate closed schemas and restrictive local
permissions. A sink failure disables only that sink and does not stop MCP tool
execution.

Restart after changing connectivity, policy, or credentials. In-memory caches
and clients are discarded. Encrypted pending plans retain their original
expiry, but policy is checked again before application.

When redirecting the local connectivity layer to a different physical
installation, cancel pending plans and use a fresh plan database and key. The
gateway cannot infer that two identical loopback configurations lead to
different remote installations.

## Operational evidence

Keep live validation evidence outside the public repository. Evidence must be
sanitized and must not include entry identifiers, business values, account
details, endpoints, private topology, or client names. Public documentation
should describe reproducible procedures and fictional examples only.
