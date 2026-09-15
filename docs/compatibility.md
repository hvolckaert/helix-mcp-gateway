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
| Native Windows, x86_64 | Full live validation | On 2026-09-15, release `v0.10.1` installed from its published wheel with Python 3.12.10 and JDK 17.0.12. Deferred AR API selection, Dashboard folder browsing, bridge compilation against BMC 25.1 libraries, non-live and DEV live preflight, AR API authentication, Kaazing connectivity, a read-only DEV form-catalog request (8,224 forms), MCP stdio initialization, and 19-tool discovery passed. DEV/QA/PROD Kaazing checks also passed during the earlier local Dashboard validation. The isolated Windows bridge used a separate loopback port because the WSL bridge occupied the default port. The BMC Java API generated an auxiliary `RegKey.reg` in the process working directory; the test removed it afterward. |
| macOS | Not validated | No installation or live-connectivity claim is made. |

## Runtime and external components

| Component | Required or tested version | Status |
| --- | --- | --- |
| Python | 3.12 | Required; 3.12.3 used on WSL2 and 3.12.10 on native Windows. |
| Java JDK | 17 or later, with `jdk.compiler` and `jdk.jartool` | Required; OpenJDK 21.0.12 used on WSL2 and JDK 17.0.12 on native Windows. |
| BMC Developer Studio / AR System Java API | 21.30.07 and 25.1 | User-supplied proprietary dependency. Both versions passed bridge startup, authentication, and form-catalog access against the configured DEV target; 25.1 also passed on native Windows. Other releases are accepted only when their BMC manifests and required classes validate and the bridge compiles against them. |
| BMC Helix Client Gateway | Locally configured for the target | Required for live connectivity; not distributed by this project. |
| GitHub CLI | Project-managed 2.100.0 | Setup provisions the pinned official executable for Linux x86_64, Linux arm64, and native Windows x86_64. It is used only for local release-attestation verification and requires no GitHub login. |
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
