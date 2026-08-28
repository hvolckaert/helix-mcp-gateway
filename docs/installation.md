# Installing from a wheel

The distribution separates original open-source gateway code from proprietary
BMC libraries. The wheel contains Python code, Java bridge source, and generic
templates. It never contains BMC JARs, credentials, or organization-specific
configuration.

## Prerequisites

- Python 3.12;
- a Java 17 or later compiler;
- an authorized and configured local connectivity layer;
- BMC Developer Studio / AR API 21.30.x;
- credentials authorized for the selected environments.

## Python installation

Create a dedicated virtual environment and install a release wheel:

```text
python3.12 -m venv /path/to/helix-mcp/venv
/path/to/helix-mcp/venv/bin/python -m pip install \
  helix_mcp_gateway-<version>-py3-none-any.whl
```

Use the equivalent `Scripts\python.exe` path on native Windows.

## Guided setup

```text
helix-mcp-setup \
  --arapi-lib-dir "/authorized/path/to/DeveloperStudio/lib"
```

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

The generated `.env` contains no sample credentials. The user must supply the
required `HELIX_CREDENTIAL_*` values through an approved secret source.

The plan key contains 32 random bytes, uses mode `0600` on POSIX, and is never
included in `.env` content, logs, wheels, or the repository. Startup rejects
broad permissions or symbolic links for sensitive local state.

## Rebuilding the bridge

```text
helix-mcp-build-bridge \
  --arapi-lib-dir "/authorized/path/to/DeveloperStudio/lib" \
  --output "/path/to/helix-arapi-bridge.jar"
```

A failed build leaves the previous bridge untouched. The compiler receives a
reduced environment without Helix credentials. The output manifest records the
package version, bridge protocol version, and source SHA-256.

## Configuration check

After completing `.env` and reviewing the policy:

```text
helix-mcp-check --dotenv /path/to/.env
```

An optional live check verifies local connectivity without reading or writing
Helix data:

```text
helix-mcp-check --dotenv /path/to/.env --live --environment dev
```

Restart the MCP client after registering or changing the server command.

## Upgrades

Installing a newer wheel and running setup again rebuilds the bridge but does
not overwrite existing `.env` or YAML policy files. A release that changes the
configuration schema must provide a specific migration procedure.

Never reuse an example encryption key or store the key in the repository.
