# Installing from a release wheel

The distribution separates original open-source gateway code from proprietary
BMC libraries. The wheel contains Python code, Java bridge source, and generic
templates. It never contains BMC JARs, credentials, or organization-specific
configuration.

```text
GitHub release -> checksum -> virtual environment -> wheel install
    -> guided setup -> credentials and policy review -> preflight
    -> MCP client registration
```

## Prerequisites

- Python 3.12;
- a Java 17 or later JDK with the `jdk.compiler` and `jdk.jartool` modules;
- an authorized and configured BMC Helix Client Gateway connection;
- BMC Developer Studio / AR API 21.30.x;
- credentials authorized for the selected environments;
- access to GitHub and the configured Python package index while installing.

Setup invokes the compiler through the Java modules, so a separate `javac`
executable does not need to be on `PATH`. Confirm the main prerequisites before
downloading:

```text
python3.12 --version
java --version
java --list-modules
```

See the [compatibility matrix](compatibility.md) for the environments that have
been tested rather than inferred.

## Download and verify a release

Download the wheel and checksum file from the
[GitHub release](https://github.com/hvolckaert/helix-mcp-gateway/releases) you
intend to install. With the GitHub CLI:

```text
gh release download vX.Y.Z \
  --repo hvolckaert/helix-mcp-gateway \
  --pattern "helix_mcp_gateway-*-py3-none-any.whl" \
  --pattern "SHA256SUMS"
sha256sum -c SHA256SUMS --ignore-missing
```

The checksum file also names the source archive. `--ignore-missing` permits
verification when only the wheel was downloaded. On native Windows, run
`Get-FileHash <wheel> -Algorithm SHA256` in PowerShell and compare the result
with the wheel entry in `SHA256SUMS`.

## Python installation

Create a dedicated virtual environment and install a release wheel:

```text
python3.12 -m venv /path/to/helix-mcp/venv
/path/to/helix-mcp/venv/bin/python -m pip install \
  /path/to/downloads/helix_mcp_gateway-<version>-py3-none-any.whl
/path/to/helix-mcp/venv/bin/python -m pip check
```

On native Windows, use the equivalent commands and keep using the full virtual
environment path in later steps:

```text
py -3.12 -m venv C:\path\to\helix-mcp\venv
C:\path\to\helix-mcp\venv\Scripts\python.exe -m pip install C:\path\to\wheel
C:\path\to\helix-mcp\venv\Scripts\python.exe -m pip check
```

## Guided setup

```text
/path/to/helix-mcp/venv/bin/helix-mcp-setup \
  --arapi-lib-dir "/authorized/path/to/DeveloperStudio/lib"
```

Use `Scripts\helix-mcp-setup.exe` on native Windows. Run
`helix-mcp-setup --help` for every destination option and its default class.

If the option is omitted, setup uses `HELIX_ARAPI_LIB_DIR` or accepts one
unambiguous installation in a supported local location. It never chooses
between multiple installations.

The command:

1. validates the `arapi`, `arapiext`, and `arlogger` manifests;
2. compiles the packaged bridge source in a temporary directory;
3. installs the bridge JAR atomically;
4. creates `helix.yaml` and `.env` only when absent;
5. generates a private random key for encrypted plans;
6. returns sanitized JSON with destinations and the MCP client command.

`--config-dir`, `--data-dir`, and `--state-dir` override the per-user defaults.
`--dry-run` validates packaged resources and reports destinations without
creating directories, compiling, or changing files.

The generated `.env` contains no sample credentials. Supply only the
environments that the local policy enables, using one compact JSON object per
line:

```dotenv
HELIX_CREDENTIAL_DEV='{"username":"DEV_USER","password":"REPLACE_ME"}'
HELIX_CREDENTIAL_QA='{"username":"QA_USER","password":"REPLACE_ME"}'
HELIX_CREDENTIAL_PROD='{"username":"PROD_USER","password":"REPLACE_ME"}'
```

An optional `domain` member may be included when required by the account. The
gateway parses this file directly; do not source it in a shell or print it for
diagnostics. Restrict its permissions to the current user and use an approved
secret source for real values.

Review the generated `helix.yaml` before connecting. Its names and policies
are fictional defaults, not knowledge of the user's systems. Replace targets,
allowlists, and limits with explicitly authorized values. PROD must remain
read-only.

The plan key contains 32 random bytes, uses mode `0600` on POSIX, and is never
included in `.env` content, logs, wheels, or the repository. Startup rejects
broad permissions or symbolic links for sensitive local state.

## Rebuilding the bridge

```text
/path/to/helix-mcp/venv/bin/helix-mcp-build-bridge \
  --arapi-lib-dir "/authorized/path/to/DeveloperStudio/lib" \
  --output "/path/to/helix-arapi-bridge.jar"
```

A failed build leaves the previous bridge untouched. The compiler receives a
reduced environment without Helix credentials. The output manifest records the
package version, bridge protocol version, and source SHA-256.

## Configuration check

After completing `.env` and reviewing the policy:

```text
/path/to/helix-mcp/venv/bin/helix-mcp-check --dotenv /path/to/config/.env
```

An optional live check verifies local connectivity without reading or writing
Helix data:

```text
/path/to/helix-mcp/venv/bin/helix-mcp-check \
  --dotenv /path/to/config/.env \
  --live \
  --environment dev
```

The non-live check should report `ready` before a live check is attempted. The
live check requires the configured Client Gateway to be running, but it does
not read or write Helix business data. See [Troubleshooting](troubleshooting.md)
if either check fails.

## Register the MCP server

Setup returns a sanitized MCP client command. Register its absolute paths with
the client instead of relying on shell activation. A generic JSON entry is:

```json
{
  "command": "/path/to/helix-mcp/venv/bin/helix-mcp",
  "args": ["--dotenv", "/path/to/config/.env"]
}
```

Use `Scripts\helix-mcp.exe` on native Windows. Restart the MCP client after
registering the server or changing its configuration.

## Expected installation time

A clean installation of release `v0.6.7` was measured on Ubuntu 24.04 under
WSL2 with Python 3.12.3 and OpenJDK 21.0.12:

| Step | Automated time |
| --- | ---: |
| Download release assets | 1.10 s |
| Create virtual environment | 4.39 s |
| Install wheel and dependencies | 11.57 s |
| Setup, bridge build, and configuration | 1.66 s |
| Non-live and live preflight | 1.35 s |
| MCP initialization and 19-tool discovery | 0.58 s |

The automated total was about 21 seconds. Allow **10–20 minutes** for a normal
first installation when prerequisites are already available; reviewing policy
and entering approved local values takes longer than the commands. Installing
the JDK, Client Gateway, Developer Studio, or obtaining access is outside this
estimate.

## Upgrades

Installing a newer wheel and running setup again rebuilds the bridge but does
not overwrite existing `.env` or YAML policy files. A release that changes the
configuration schema must provide a specific migration procedure.

Never reuse an example encryption key or store the key in the repository.
