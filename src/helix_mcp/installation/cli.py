"""Console entry points for local installation and bridge builds."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from importlib import metadata
from pathlib import Path
from typing import Any

from helix_mcp.dashboard import (
    DEFAULT_DASHBOARD_PORT,
    DashboardProcessLauncher,
)
from helix_mcp.installation.bridge import build_bridge
from helix_mcp.installation.managed import activate_managed_installation
from helix_mcp.installation.setup import (
    default_install_paths,
    discover_arapi_lib_dir,
    setup_installation,
)
from helix_mcp.observability import public_error_code


def setup_main(argv: Sequence[str] | None = None) -> int:
    """Initialize a non-destructive per-user installation."""

    parser = argparse.ArgumentParser(
        prog="helix-mcp-setup",
        description=(
            "Compile the packaged Java bridge and create a non-destructive "
            "per-user Helix MCP Gateway configuration."
        ),
        epilog=(
            "The command prints sanitized JSON. Existing .env and YAML "
            "configuration files are never overwritten."
        ),
    )
    parser.add_argument(
        "--arapi-lib-dir",
        type=Path,
        metavar="DIR",
        help=(
            "directory containing the authorized arapi, arapiext, and "
            "arlogger JARs; otherwise use HELIX_ARAPI_LIB_DIR or "
            "unambiguous discovery"
        ),
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        metavar="DIR",
        help=(
            "directory for generated .env and helix.yaml files; defaults "
            "to the platform per-user configuration directory"
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        metavar="DIR",
        help=(
            "directory for the compiled Java bridge JAR; defaults to the "
            "platform per-user data directory"
        ),
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        metavar="DIR",
        help=(
            "directory for the encryption key, plan database, audit, "
            "metrics, and operation files; defaults to the platform "
            "per-user state directory"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate packaged resources and show target paths only",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="do not start or open the local configuration dashboard",
    )
    parser.add_argument(
        "--client",
        choices=("standalone", "openclaw"),
        default="standalone",
        help="client integration retained across managed updates",
    )
    parser.add_argument(
        "--server-name",
        default="helix",
        help="OpenClaw MCP server name (default: helix)",
    )
    parser.add_argument(
        "--openclaw-command",
        help="OpenClaw executable used to reload the managed MCP definition",
    )
    parser.add_argument(
        "--no-managed",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args(argv)
    try:
        managed_activated = False
        result = setup_installation(
            arapi_lib_dir=arguments.arapi_lib_dir,
            config_dir=arguments.config_dir,
            data_dir=arguments.data_dir,
            state_dir=arguments.state_dir,
            dry_run=arguments.dry_run,
        )
        if not arguments.dry_run and not arguments.no_managed:
            server_command = _installed_server_command()
            openclaw_command = arguments.openclaw_command
            if arguments.client == "openclaw" and not openclaw_command:
                openclaw_command = shutil.which("openclaw")
            managed = activate_managed_installation(
                workspace=result.paths.data_dir,
                version=_package_version(),
                server_command=server_command,
                dotenv_path=result.dotenv_path,
                client=arguments.client,
                server_name=(
                    arguments.server_name
                    if arguments.client == "openclaw"
                    else None
                ),
                openclaw_command=openclaw_command,
            )
            result = replace(result, server_command=str(managed.launcher))
            managed_activated = True
        dashboard: dict[str, object] | None
        if arguments.no_dashboard:
            dashboard = None
        elif arguments.dry_run:
            dashboard = {
                "command": "helix-mcp-dashboard",
                "args": ["--dotenv", str(result.dotenv_path)],
                "url": (f"http://127.0.0.1:{DEFAULT_DASHBOARD_PORT}/"),
            }
        else:
            dashboard = (
                DashboardProcessLauncher(
                    dotenv_path=result.dotenv_path,
                    errors_path=result.paths.state_dir / "errors",
                )
                .start()
                .to_dict()
            )
    except Exception as exc:
        _print_json({"status": "failed", "error_code": public_error_code(exc)})
        return 1
    payload = _paths_to_strings(asdict(result))
    payload["status"] = "ready_for_configuration"
    payload["codex_desktop"] = {
        "command": result.server_command,
        "args": (
            [] if managed_activated else ["--dotenv", str(result.dotenv_path)]
        ),
    }
    payload["dashboard"] = dashboard
    _print_json(payload)
    return 0


def bridge_main(argv: Sequence[str] | None = None) -> int:
    """Compile and atomically install only the local Java bridge."""

    defaults = default_install_paths()
    parser = argparse.ArgumentParser(prog="helix-mcp-build-bridge")
    parser.add_argument("--arapi-lib-dir", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=defaults.data_dir / "bridge" / "helix-arapi-bridge.jar",
    )
    arguments = parser.parse_args(argv)
    try:
        libraries = discover_arapi_lib_dir(arguments.arapi_lib_dir)
        result = build_bridge(libraries, arguments.output)
    except Exception as exc:
        _print_json({"status": "failed", "error_code": public_error_code(exc)})
        return 1
    _print_json(
        {
            "status": "built",
            "output_path": str(result.output_path),
            "package_version": result.package_version,
            "source_sha256": result.source_sha256,
        }
    )
    return 0


def setup_entrypoint() -> None:
    raise SystemExit(setup_main())


def bridge_entrypoint() -> None:
    raise SystemExit(bridge_main())


def _paths_to_strings(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _paths_to_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_paths_to_strings(item) for item in value]
    return value


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _installed_server_command() -> Path:
    executable_dir = Path(sys.executable).resolve().parent
    suffix = ".exe" if sys.platform == "win32" else ""
    sibling = executable_dir / f"helix-mcp{suffix}"
    if sibling.is_file():
        return sibling
    discovered = shutil.which("helix-mcp")
    if discovered:
        return Path(discovered).resolve()
    raise RuntimeError("installed Helix MCP entry point was not found")


def _package_version() -> str:
    try:
        return metadata.version("helix-mcp-gateway")
    except metadata.PackageNotFoundError:
        return "0.0.0"
