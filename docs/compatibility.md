# Compatibility matrix

This matrix distinguishes tested combinations from documented paths. A
documented command is not a support claim until the complete installation has
been repeated in that environment.

## Installation environments

| Environment | Level | Evidence and limitations |
| --- | --- | --- |
| Ubuntu 24.04 on WSL2, x86_64 | Full live validation | Release `v0.6.7` installed from its wheel with Python 3.12.3 and OpenJDK 21.0.12; setup, bridge build, non-live and DEV live preflight, MCP initialization, and 19-tool discovery passed on 2026-08-28. |
| Ubuntu in GitHub Actions | Automated CI | Python tests, contracts, E2E simulations, bridge tests, build, and wheel smoke tests run without a live Helix target. |
| Windows in GitHub Actions | Automated CI | Python and packaging validation run without an external Developer Studio installation, Client Gateway, or live Helix target. |
| Native Windows | Documented, not fully validated | `Scripts\*.exe` paths and PowerShell checksum verification are documented; the complete external installation remains to be repeated. |
| macOS | Not validated | No installation or live-connectivity claim is made. |

## Runtime and external components

| Component | Required or tested version | Status |
| --- | --- | --- |
| Python | 3.12 | Required; 3.12.3 used for the full validation. |
| Java JDK | 17 or later, with `jdk.compiler` and `jdk.jartool` | Required; OpenJDK 21.0.12 used for the full validation. |
| BMC Developer Studio / AR API | 21.30.x | User-supplied proprietary dependency; validated through the authorized local manifests. |
| BMC Helix Client Gateway | Locally configured for the target | Required for live connectivity; not distributed by this project. |
| MCP transport | Standard input/output | Verified with the Python MCP SDK through initialization and discovery of all 19 tools. |

The project does not redistribute BMC libraries, configure remote environments,
or expand the selected account's permissions. SQL execution and database
metadata additionally require an AR System administrator account.

## MCP clients

The setup command emits a generic absolute stdio command suitable for clients
that support local MCP servers. Protocol initialization and tool discovery are
tested independently of any client UI. A specific desktop client should be
treated as locally validated only after it starts the emitted command with the
same user, dotenv path, and network context.

See [Installation](installation.md) for the tested workflow and
[Troubleshooting](troubleshooting.md) for safe diagnostics. Please report a
successful additional platform combination without including private targets,
paths, credentials, or organization-specific configuration.
