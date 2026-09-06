"""Automatic OpenClaw registration and runtime reload support."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from helix_mcp.installation.managed import ManagedInstallation

DEFAULT_SERVER_NAME = "helix"
MINIMUM_RELOAD_TIMEOUT_SECONDS = 60
EXPOSED_TOOLS = (
    "list_targets",
    "health_check",
    "list_forms",
    "list_form_fields",
    "query_form",
    "get_entry",
    "list_database_objects",
    "list_database_columns",
    "describe_database_object",
    "plan_sql_query",
    "get_sql_query_plan",
    "cancel_sql_query_plan",
    "execute_sql_query",
    "plan_create_entry",
    "apply_create_entry",
    "plan_update_entry",
    "apply_update_entry",
    "get_write_plan",
    "cancel_write_plan",
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class OpenClawIntegrationError(RuntimeError):
    """OpenClaw could not be configured without exposing command output."""

    code = "OPENCLAW_INTEGRATION_ERROR"


@dataclass(frozen=True, slots=True)
class OpenClawRegistration:
    """Sanitized result of registering the managed MCP launcher."""

    server_name: str
    reloaded: bool
    probed: bool


def find_openclaw_command(
    candidate: str | Path | None = None,
) -> Path | None:
    """Resolve an optional OpenClaw command without starting it."""

    if candidate is None:
        discovered = shutil.which("openclaw")
        return Path(discovered).absolute() if discovered else None
    raw = str(candidate).strip()
    if not raw:
        return None
    discovered = shutil.which(raw)
    resolved = Path(discovered or raw).expanduser().absolute()
    return resolved if resolved.is_file() else None


def register_openclaw_server(
    *,
    launcher: Path,
    workspace: Path,
    server_name: str = DEFAULT_SERVER_NAME,
    openclaw_command: str | Path = "openclaw",
    connect_timeout: int = 30,
    request_timeout: int = 150,
    runner: CommandRunner = subprocess.run,
) -> OpenClawRegistration:
    """Register, reload and probe one managed stdio server in OpenClaw."""

    normalized_name = server_name.strip()
    if not normalized_name:
        raise OpenClawIntegrationError("OpenClaw server name cannot be empty")
    if connect_timeout <= 0 or request_timeout <= 0:
        raise OpenClawIntegrationError("OpenClaw timeouts must be positive")
    resolved_openclaw = _require_command(openclaw_command)
    resolved_launcher = launcher.expanduser().absolute()
    resolved_workspace = workspace.expanduser().absolute()
    if not resolved_launcher.is_file() or not resolved_workspace.is_dir():
        raise OpenClawIntegrationError(
            "managed installation is not ready for OpenClaw"
        )
    stdio_command, stdio_arguments = _stdio_invocation(resolved_launcher)
    definition = {
        "command": str(stdio_command),
        "args": list(stdio_arguments),
        "cwd": str(resolved_workspace),
        "connectionTimeoutMs": connect_timeout * 1_000,
        "requestTimeoutMs": request_timeout * 1_000,
        "toolFilter": {"include": list(EXPOSED_TOOLS)},
    }
    previous = _inspect_definition(
        resolved_openclaw,
        normalized_name,
        runner=runner,
    )
    try:
        _set_definition(
            resolved_openclaw,
            normalized_name,
            definition,
            runner=runner,
        )
        reload_openclaw(
            resolved_openclaw,
            runner=runner,
            timeout=max(MINIMUM_RELOAD_TIMEOUT_SECONDS, connect_timeout + 30),
        )
        completed = _run(
            [
                str(resolved_openclaw),
                "mcp",
                "probe",
                normalized_name,
                "--json",
            ],
            runner=runner,
            timeout=max(60, connect_timeout + request_timeout + 15),
            action="OpenClaw MCP probe",
        )
        _validate_probe(completed.stdout, normalized_name, resolved_launcher)
    except Exception as exc:
        _restore_definition(
            resolved_openclaw,
            normalized_name,
            previous,
            runner=runner,
        )
        if isinstance(exc, OpenClawIntegrationError):
            raise
        raise OpenClawIntegrationError(
            "OpenClaw registration failed"
        ) from None
    return OpenClawRegistration(
        server_name=normalized_name,
        reloaded=True,
        probed=True,
    )


def reload_managed_openclaw(
    installation: ManagedInstallation,
    *,
    runner: CommandRunner = subprocess.run,
) -> None:
    """Dispose cached runtimes for one OpenClaw-managed installation."""

    if installation.client != "openclaw":
        raise OpenClawIntegrationError(
            "managed installation is not connected to OpenClaw"
        )
    reload_openclaw(
        _require_command(installation.openclaw_command or "openclaw"),
        runner=runner,
    )


def reload_openclaw(
    command: str | Path,
    *,
    runner: CommandRunner = subprocess.run,
    timeout: int = 90,
) -> None:
    """Ask OpenClaw to dispose its cached MCP runtimes."""

    resolved = _require_command(command)
    _run(
        [str(resolved), "mcp", "reload"],
        runner=runner,
        timeout=timeout,
        action="OpenClaw MCP reload",
    )


def _inspect_definition(
    command: Path,
    server_name: str,
    *,
    runner: CommandRunner,
) -> dict[str, object] | None:
    completed = _execute(
        [str(command), "mcp", "show", server_name, "--json"],
        runner=runner,
        timeout=60,
    )
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise OpenClawIntegrationError(
            "OpenClaw MCP definition is invalid"
        ) from None
    if not isinstance(payload, dict):
        raise OpenClawIntegrationError("OpenClaw MCP definition is invalid")
    return payload


def _set_definition(
    command: Path,
    server_name: str,
    definition: Mapping[str, object],
    *,
    runner: CommandRunner,
) -> None:
    _run(
        [
            str(command),
            "mcp",
            "set",
            server_name,
            json.dumps(definition, ensure_ascii=False, separators=(",", ":")),
        ],
        runner=runner,
        timeout=60,
        action="OpenClaw MCP registration",
    )


def _restore_definition(
    command: Path,
    server_name: str,
    previous: Mapping[str, object] | None,
    *,
    runner: CommandRunner,
) -> None:
    try:
        if previous is None:
            _run(
                [str(command), "mcp", "unset", server_name],
                runner=runner,
                timeout=60,
                action="OpenClaw MCP rollback",
            )
        else:
            _set_definition(
                command,
                server_name,
                previous,
                runner=runner,
            )
        reload_openclaw(command, runner=runner)
    except Exception:
        return


def _validate_probe(output: str, server_name: str, launcher: Path) -> None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        raise OpenClawIntegrationError(
            "OpenClaw MCP probe returned invalid data"
        ) from None
    if not isinstance(payload, dict) or payload.get("diagnostics"):
        raise OpenClawIntegrationError(
            "OpenClaw MCP probe returned diagnostics"
        )
    servers = payload.get("servers")
    server = servers.get(server_name) if isinstance(servers, dict) else None
    launch = server.get("launch") if isinstance(server, dict) else None
    if not isinstance(launch, str) or str(launcher) not in launch:
        raise OpenClawIntegrationError(
            "OpenClaw MCP probe did not use the managed launcher"
        )


def _stdio_invocation(command: Path) -> tuple[Path, tuple[str, ...]]:
    if os.name != "nt" or command.suffix.casefold() not in {".bat", ".cmd"}:
        return command, ()
    command_shell = os.environ.get("COMSPEC", "").strip()
    if not command_shell:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        command_shell = str(Path(system_root) / "System32" / "cmd.exe")
    return _require_command(command_shell), ("/d", "/s", "/c", str(command))


def _require_command(candidate: str | Path) -> Path:
    resolved = find_openclaw_command(candidate)
    if resolved is None:
        raise OpenClawIntegrationError("OpenClaw command was not found")
    return resolved


def _execute(
    command: list[str],
    *,
    runner: CommandRunner,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        raise OpenClawIntegrationError("OpenClaw command failed") from None


def _run(
    command: list[str],
    *,
    runner: CommandRunner,
    timeout: int,
    action: str,
) -> subprocess.CompletedProcess[str]:
    completed = _execute(command, runner=runner, timeout=timeout)
    if completed.returncode != 0:
        raise OpenClawIntegrationError(f"{action} failed")
    return completed
