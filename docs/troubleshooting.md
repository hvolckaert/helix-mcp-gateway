# Troubleshooting

Start with the non-live preflight. It validates local configuration, policy,
credentials, bridge resources, and sensitive file permissions without
connecting to Helix:

```text
/path/to/venv/bin/helix-mcp-check --dotenv /path/to/config/.env
```

Do not paste `.env`, YAML content, credentials, SQL, form values, returned rows,
or raw bridge logs into an issue. The safe diagnostic unit is the failed check
name, stable public error code, runtime versions, and whether the failure is
local or live.

## Installation failures

### The wheel does not install

1. Confirm that `python3.12 --version` selects Python 3.12.
2. Verify the wheel checksum against the release `SHA256SUMS` file.
3. Confirm access to the configured Python package index for dependencies.
4. Run `/path/to/venv/bin/python -m pip check` after installation.

Do not install into a system Python or reuse an unrelated virtual environment.

### Setup reports AR API as pending

Pass the authorized directory explicitly:

```text
/path/to/venv/bin/helix-mcp-setup \
  --arapi-lib-dir "/authorized/path/to/DeveloperStudio/lib"
```

The directory must contain valid `arapi`, `arapiext`, and `arlogger` JARs from
an authorized BMC Developer Studio / AR System Java API installation. Setup
checks the BMC manifests and Java API classes, then compiles the bridge against
the selected release. When discovery finds none or several installations, setup
leaves AR API pending and opens the dashboard. Use **Advanced settings → Local
AR API bridge → Browse** to inspect files and choose the correct folder.

### The Java bridge does not build

Run `java --version` and `java --list-modules`, or use the dashboard's Java JDK
folder browser. A Java 17 or later JDK must
provide `jdk.compiler` and `jdk.jartool`; a standalone runtime is insufficient.
`ARAPI_BRIDGE_BUILD_ERROR` indicates a local build or library validation
failure. Use the explicit AR API path and keep the complete private diagnostic
locally, because filesystem paths may identify the workstation.

### Managed GitHub CLI setup fails

`GITHUB_CLI_INSTALLATION_ERROR` means setup could not safely provision or
validate the private GitHub CLI used for release attestations. Confirm that the
host is Linux x86_64, Linux arm64, or native Windows x86_64 and can reach the
public `github.com` release endpoints over HTTPS. A proxy may be used only when
it preserves normal TLS certificate validation.

Do not run `gh auth login`, install `gh` system-wide, or add it to `PATH` as a
workaround. Setup can leave GitHub CLI pending and still open the dashboard;
verified managed updates need it. Retry `helix-mcp-setup` after restoring public HTTPS access. Setup
will discard an incomplete download and reuse an existing managed executable
only when its pinned digest and command capabilities validate.

## Configuration failures

### Configuration is rejected

Check the paths returned by setup and use the generated `.env` explicitly.
Common stable codes include:

- `CONFIG_SOURCE_ERROR`: missing, unreadable, unsafe, or disallowed source;
- `CONFIG_TOO_LARGE`: a local configuration file exceeds its size limit;
- `CONFIG_ENCODING_ERROR`: the file is not valid UTF-8;
- `CONFIG_SYNTAX_ERROR`: malformed YAML or dotenv syntax;
- `CONFIG_STRUCTURE_ERROR`: unsupported keys or invalid value structure;
- `SINGLE_INSTANCE_CONFIG_ERROR`: the DEV/QA/PROD model is inconsistent.

Sensitive files must be regular files, not symbolic links, and must be readable
only by the current user on POSIX systems. The encrypted-plan key must contain
exactly 32 random bytes and use mode `0600` on POSIX.

### Credentials are missing or invalid

Each environment that will be used needs its own compact JSON value with
`username` and `password` members, for example:

```dotenv
HELIX_CREDENTIAL_DEV='{"username":"DEV_USER","password":"REPLACE_ME"}'
```

Add `domain` only when the account requires it. Never print the parsed value or
source the dotenv file in a shell. A successful non-live preflight validates
the credential structure, not whether the remote account can authenticate.

An absent or blank value is allowed and makes only that environment
unavailable. `list_targets(include_disabled=true)` reports it as
`credential_not_configured`, and its tools return
`TARGET_CREDENTIAL_NOT_CONFIGURED`. Configure and save that environment's
credential, then reconnect the MCP client. Do not duplicate a credential from
another environment merely to make the target appear available.

## Connectivity failures

### Non-live preflight passes, but live preflight fails

Confirm that the configured BMC Helix Client Gateway is running and that the
selected account may reach the exact target in `helix.yaml`. Then retry only
the intended non-production environment:

```text
/path/to/venv/bin/helix-mcp-check \
  --dotenv /path/to/config/.env \
  --live \
  --environment dev
```

`ARAPI_BRIDGE_PROCESS_ERROR` indicates that the managed bridge could not start
or complete its local protocol exchange. `ARAPI_BRIDGE_PROCESS_ERROR` and
`ARAPI_BRIDGE_BUILD_ERROR` deliberately omit command output and local paths.
Run the check from the same user and network context as the MCP client; a
container or network sandbox may not reach a host-local Client Gateway.

The live preflight checks connectivity and authentication without reading or
writing Helix business data.

### The MCP client does not start the server

Use the absolute command and dotenv paths returned by setup:

```json
{
  "command": "/path/to/venv/bin/helix-mcp",
  "args": ["--dotenv", "/path/to/config/.env"]
}
```

On native Windows the command ends in `Scripts\helix-mcp.exe`. Restart the
MCP client after changing its server configuration. Run the same command from
a terminal to distinguish client registration from gateway preflight errors.

### Several clients start the Java bridge at the same time

The dashboard and MCP clients may all see the local bridge as unavailable
before any of their Java children has opened the configured port. One child
can then exit because another child acquired that port first.

During startup, the gateway continues its bounded readiness checks even if
its child exits. It reuses the available bridge only after the normal health
and authentication checks succeed, and does not take ownership of a bridge
started by another client. Closing that borrower leaves the other bridge
running. The original owner still controls the bridge's lifetime.

If no acceptable bridge becomes ready, startup still fails with
`ARAPI_BRIDGE_PROCESS_ERROR`. Its sanitized message distinguishes an exited
child from a running child that never became healthy. It does not include
Java output, credentials, or local paths. A cancelled readiness wait cleans
up its own child. These checks require no configuration changes.

### Tools remain connected after the bridge owner closes

The process that starts Java owns that child and stops it during shutdown.
Other MCP clients and the dashboard can reuse the same authenticated bridge,
so closing its owner can leave those clients running after Java has exited.
The concurrent-startup fix in 0.17.1 handles a port bind race at startup;
it does not restore the bridge for clients that were already connected.

The gateway now checks authenticated readiness before each backend request
and fresh bridge health probe. A surviving client can start or reuse a
replacement bridge before sending the operation. Recovery is serialized
within each application and keeps the existing bounded startup checks across
processes. An owned child that is still running but fails a health check is
reported as unavailable, without killing or replacing it. A closed application
cannot restart Java.

No request is replayed after transmission. If Java stops while a create or
update is in flight, its outcome may be uncertain; recovery does not bypass
the existing planning, approval, conflict, or single-use execution rules.
This change adds one local readiness probe to backend requests. Cached reads
retain their existing acquisition timestamps and cache behavior.

The operational log records `arapi_bridge_started`, `arapi_bridge_exited`,
`arapi_bridge_owner_stopping`, `arapi_bridge_recovery_started`, and
`arapi_bridge_recovered`. These events contain no credentials, backend data,
local paths, or endpoint addresses. Older logs do not establish which client
owned a bridge that disappeared.

## Safe evidence for a report

Include:

- gateway release and wheel checksum;
- operating system, architecture, Python version, and Java version;
- preflight check name and stable public error code;
- whether non-live preflight passed;
- whether the Client Gateway was running;
- the MCP client name and version, when relevant.

Exclude credentials, targets, personal paths, organization names, policy
content, SQL, form names, field names, values, rows, and raw diagnostics. Follow
the private vulnerability process in [`SECURITY.md`](../SECURITY.md) for a
suspected security issue.
