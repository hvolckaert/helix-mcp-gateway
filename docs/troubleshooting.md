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

### Setup cannot find the AR API libraries

Pass the authorized directory explicitly:

```text
/path/to/venv/bin/helix-mcp-setup \
  --arapi-lib-dir "/authorized/path/to/DeveloperStudio/lib"
```

The directory must contain valid `arapi`, `arapiext`, and `arlogger` JAR
manifests from BMC Developer Studio / AR API 21.30.x. Setup intentionally stops
when discovery finds multiple possible installations.

### The Java bridge does not build

Run `java --version` and `java --list-modules`. A Java 17 or later JDK must
provide `jdk.compiler` and `jdk.jartool`; a standalone runtime is insufficient.
`ARAPI_BRIDGE_BUILD_ERROR` indicates a local build or library validation
failure. Use the explicit AR API path and keep the complete private diagnostic
locally, because filesystem paths may identify the workstation.

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

Each enabled environment needs a compact JSON value with `username` and
`password` members, for example:

```dotenv
HELIX_CREDENTIAL_DEV='{"username":"DEV_USER","password":"REPLACE_ME"}'
```

Add `domain` only when the account requires it. Never print the parsed value or
source the dotenv file in a shell. A successful non-live preflight validates
the credential structure, not whether the remote account can authenticate.

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
