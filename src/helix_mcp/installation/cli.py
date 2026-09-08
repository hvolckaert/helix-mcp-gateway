"""Console entry points for local installation and bridge builds."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import webbrowser
from collections.abc import Sequence
from dataclasses import asdict, replace
from importlib import metadata
from pathlib import Path
from typing import Any

from helix_mcp.dashboard import (
    DEFAULT_DASHBOARD_PORT,
    DashboardProcessLauncher,
    dashboard_browser_url,
)
from helix_mcp.dashboard_runtime import DashboardRuntimeManager
from helix_mcp.installation.bridge import build_bridge
from helix_mcp.installation.managed import (
    ManagedInstallation,
    activate_managed_installation,
    load_managed_installation,
    supports_transactional_updates,
)
from helix_mcp.installation.openclaw import (
    find_openclaw_command,
    register_openclaw_server,
)
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
        help="keep the managed dashboard available without opening a browser",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=DEFAULT_DASHBOARD_PORT,
        help=f"loopback dashboard port (default: {DEFAULT_DASHBOARD_PORT})",
    )
    parser.add_argument(
        "--client",
        choices=("auto", "standalone", "openclaw"),
        default="auto",
        help=(
            "client integration retained across managed updates; auto "
            "registers OpenClaw when its command is available"
        ),
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
        managed: ManagedInstallation | None = None
        client_integration = arguments.client
        result = setup_installation(
            arapi_lib_dir=arguments.arapi_lib_dir,
            config_dir=arguments.config_dir,
            data_dir=arguments.data_dir,
            state_dir=arguments.state_dir,
            dry_run=arguments.dry_run,
        )
        if not arguments.dry_run and not arguments.no_managed:
            server_command = _installed_server_command()
            previous = load_managed_installation(result.paths.data_dir)
            openclaw_command = _selected_openclaw_command(
                requested_client=arguments.client,
                requested_command=arguments.openclaw_command,
                previous=previous,
            )
            client_integration = (
                "openclaw" if openclaw_command is not None else "standalone"
            )
            managed = activate_managed_installation(
                workspace=result.paths.data_dir,
                version=_package_version(),
                server_command=server_command,
                dotenv_path=result.dotenv_path,
                client="standalone",
                dashboard_port=arguments.dashboard_port,
            )
            if client_integration == "openclaw":
                assert openclaw_command is not None
                try:
                    register_openclaw_server(
                        launcher=managed.launcher,
                        workspace=result.paths.data_dir,
                        server_name=arguments.server_name,
                        openclaw_command=openclaw_command,
                    )
                except Exception:
                    _restore_managed_installation(
                        previous=previous,
                        workspace=result.paths.data_dir,
                    )
                    raise
                managed = activate_managed_installation(
                    workspace=result.paths.data_dir,
                    version=_package_version(),
                    server_command=server_command,
                    dotenv_path=result.dotenv_path,
                    client="openclaw",
                    server_name=arguments.server_name,
                    openclaw_command=openclaw_command,
                    dashboard_port=arguments.dashboard_port,
                )
            result = replace(result, server_command=str(managed.launcher))
            managed_activated = True
        dashboard: dict[str, object] | None
        if arguments.dry_run:
            dashboard = {
                "command": "helix-mcp-dashboard",
                "args": [
                    "--dotenv",
                    str(result.dotenv_path),
                    "--port",
                    str(arguments.dashboard_port),
                ],
                "url": f"http://127.0.0.1:{arguments.dashboard_port}/",
                "browser_requested": not arguments.no_dashboard,
            }
        elif managed_activated:
            assert managed is not None
            dashboard = (
                DashboardRuntimeManager(managed).install_and_start().to_dict()
            )
            dashboard["url"] = f"http://127.0.0.1:{arguments.dashboard_port}/"
            if not arguments.no_dashboard:
                webbrowser.open(
                    dashboard_browser_url(
                        result.paths.state_dir / "errors",
                        arguments.dashboard_port,
                    )
                )
        elif arguments.no_dashboard:
            dashboard = None
        else:
            dashboard = (
                DashboardProcessLauncher(
                    dotenv_path=result.dotenv_path,
                    errors_path=result.paths.state_dir / "errors",
                    port=arguments.dashboard_port,
                )
                .start()
                .to_dict()
            )
    except Exception as exc:
        _print_json({"status": "failed", "error_code": public_error_code(exc)})
        return 1
    payload = _paths_to_strings(asdict(result))
    payload["status"] = "ready_for_configuration"
    payload["client_integration"] = client_integration
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
    executable_dir = Path(sys.executable).expanduser().absolute().parent
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


def _selected_openclaw_command(
    *,
    requested_client: str,
    requested_command: str | None,
    previous: ManagedInstallation | None,
) -> Path | None:
    if requested_client == "standalone":
        return None
    candidates = (
        requested_command,
        (
            previous.openclaw_command
            if previous is not None and previous.client == "openclaw"
            else None
        ),
        "openclaw",
    )
    resolved = next(
        (
            command
            for candidate in candidates
            if candidate is not None
            if (command := find_openclaw_command(candidate)) is not None
        ),
        None,
    )
    if resolved is None and requested_client == "openclaw":
        raise RuntimeError("OpenClaw command was not found")
    return resolved


def _restore_managed_installation(
    *,
    previous: ManagedInstallation | None,
    workspace: Path,
) -> None:
    if not supports_transactional_updates(previous):
        return
    assert previous is not None
    activate_managed_installation(
        workspace=workspace,
        version=previous.active_version,
        server_command=previous.server_command,
        dotenv_path=previous.dotenv_path,
        client=previous.client,
        server_name=previous.server_name,
        openclaw_command=previous.openclaw_command,
        dashboard_port=previous.dashboard_port,
    )
