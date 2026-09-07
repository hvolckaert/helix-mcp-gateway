"""Stable launchers and metadata for managed gateway installations."""

from __future__ import annotations

import json
import os
import shlex
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from helix_mcp.installation.setup import SetupError

ClientIntegration = Literal["standalone", "openclaw"]
INSTALLATION_SCHEMA_VERSION = 2
SUPPORTED_INSTALLATION_SCHEMA_VERSIONS = frozenset(
    {1, INSTALLATION_SCHEMA_VERSION}
)
DEFAULT_MANAGED_DASHBOARD_PORT = 8766


@dataclass(frozen=True, slots=True)
class ManagedInstallation:
    """Active runtime behind one stable client-facing command."""

    schema_version: int
    active_version: str
    client: ClientIntegration
    launcher: Path
    dashboard_launcher: Path
    server_command: Path
    dotenv_path: Path
    dashboard_port: int = DEFAULT_MANAGED_DASHBOARD_PORT
    server_name: str | None = None
    openclaw_command: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in (
            "launcher",
            "dashboard_launcher",
            "server_command",
            "dotenv_path",
        ):
            payload[key] = str(payload[key])
        return payload


def installation_metadata_path(workspace: Path) -> Path:
    """Return the metadata path for one managed installation."""

    return workspace.expanduser().absolute() / "runtime" / "installation.json"


def stable_launcher_path(workspace: Path) -> Path:
    """Return the client-facing launcher whose path survives updates."""

    suffix = ".cmd" if os.name == "nt" else ""
    return workspace.expanduser().absolute() / "bin" / f"helix-mcp{suffix}"


def stable_dashboard_launcher_path(workspace: Path) -> Path:
    """Return the stable dashboard launcher whose path survives updates."""

    name = (
        "helix-mcp-dashboard.ps1" if os.name == "nt" else "helix-mcp-dashboard"
    )
    return workspace.expanduser().absolute() / "bin" / name


def versioned_runtime_paths(
    workspace: Path,
    version: str,
) -> tuple[Path, Path, Path, Path]:
    """Return Python, server, setup and dashboard commands for a release."""

    executable_dir = (
        workspace.expanduser().absolute()
        / "runtime"
        / version
        / "venv"
        / ("Scripts" if os.name == "nt" else "bin")
    )
    suffix = ".exe" if os.name == "nt" else ""
    return (
        executable_dir / f"python{suffix}",
        executable_dir / f"helix-mcp{suffix}",
        executable_dir / f"helix-mcp-setup{suffix}",
        executable_dir / f"helix-mcp-dashboard{suffix}",
    )


def load_managed_installation(
    workspace: Path,
) -> ManagedInstallation | None:
    """Load and validate managed installation metadata when present."""

    resolved_workspace = workspace.expanduser().absolute()
    path = installation_metadata_path(resolved_workspace)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SetupError(
            "managed installation metadata could not be read"
        ) from None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version")
        not in SUPPORTED_INSTALLATION_SCHEMA_VERSIONS
    ):
        raise SetupError("managed installation metadata is unsupported")
    client = payload.get("client")
    if client not in {"standalone", "openclaw"}:
        raise SetupError("managed installation client is unsupported")
    try:
        launcher = Path(str(payload["launcher"])).expanduser().absolute()
        server_command = (
            Path(str(payload["server_command"])).expanduser().absolute()
        )
        dotenv_path = Path(str(payload["dotenv_path"])).expanduser().absolute()
        active_version = str(payload["active_version"])
    except KeyError as exc:
        raise SetupError(
            f"managed installation metadata is missing {exc.args[0]}"
        ) from None
    if launcher != stable_launcher_path(resolved_workspace):
        raise SetupError(
            "managed launcher is outside the installation workspace"
        )
    dashboard_launcher = (
        Path(
            str(
                payload.get("dashboard_launcher")
                or stable_dashboard_launcher_path(resolved_workspace)
            )
        )
        .expanduser()
        .absolute()
    )
    if dashboard_launcher != stable_dashboard_launcher_path(
        resolved_workspace
    ):
        raise SetupError(
            "managed dashboard launcher is outside the installation workspace"
        )
    try:
        dashboard_port = int(
            payload.get(
                "dashboard_port",
                DEFAULT_MANAGED_DASHBOARD_PORT,
            )
        )
    except (TypeError, ValueError):
        raise SetupError("managed dashboard port must be an integer") from None
    if not 1 <= dashboard_port <= 65_535:
        raise SetupError("managed dashboard port must be between 1 and 65535")
    return ManagedInstallation(
        schema_version=INSTALLATION_SCHEMA_VERSION,
        active_version=active_version,
        client=client,
        launcher=launcher,
        dashboard_launcher=dashboard_launcher,
        server_command=server_command,
        dotenv_path=dotenv_path,
        dashboard_port=dashboard_port,
        server_name=(
            str(payload["server_name"]) if payload.get("server_name") else None
        ),
        openclaw_command=(
            str(payload["openclaw_command"])
            if payload.get("openclaw_command")
            else None
        ),
    )


def supports_transactional_updates(
    installation: ManagedInstallation | None,
) -> bool:
    """Return whether the stable launcher and active command are usable."""

    return bool(
        installation
        and installation.launcher.is_file()
        and installation.dashboard_launcher.is_file()
        and installation.server_command.is_file()
        and installation.dotenv_path.is_file()
    )


def activate_managed_installation(
    *,
    workspace: Path,
    version: str,
    server_command: Path,
    dotenv_path: Path,
    client: ClientIntegration = "standalone",
    server_name: str | None = None,
    openclaw_command: str | Path | None = None,
    dashboard_port: int = DEFAULT_MANAGED_DASHBOARD_PORT,
) -> ManagedInstallation:
    """Atomically point both stable launchers at one validated runtime."""

    resolved_workspace = workspace.expanduser().absolute()
    resolved_server = server_command.expanduser().absolute()
    resolved_dotenv = dotenv_path.expanduser().absolute()
    if not resolved_server.is_file():
        raise SetupError("managed MCP server entry point was not found")
    if not resolved_dotenv.is_file():
        raise SetupError("managed dotenv file was not found")
    if client == "openclaw" and (not server_name or not openclaw_command):
        raise SetupError(
            "OpenClaw managed installations require its server name and command"
        )
    if not 1 <= dashboard_port <= 65_535:
        raise SetupError("managed dashboard port must be between 1 and 65535")
    python_command = resolved_server.parent / (
        "python.exe" if os.name == "nt" else "python"
    )
    if os.name == "nt" and not python_command.is_file():
        python_command = resolved_server.parent.parent / "python.exe"
    if not python_command.is_file():
        raise SetupError("managed Python entry point was not found")
    launcher = stable_launcher_path(resolved_workspace)
    dashboard_launcher = stable_dashboard_launcher_path(resolved_workspace)
    metadata_path = installation_metadata_path(resolved_workspace)
    for directory in (
        launcher.parent,
        dashboard_launcher.parent,
        metadata_path.parent,
    ):
        if directory.is_symlink():
            raise SetupError("managed installation directory cannot be a link")
        directory.mkdir(parents=True, exist_ok=True)
    installation = ManagedInstallation(
        schema_version=INSTALLATION_SCHEMA_VERSION,
        active_version=version,
        client=client,
        launcher=launcher,
        dashboard_launcher=dashboard_launcher,
        server_command=resolved_server,
        dotenv_path=resolved_dotenv,
        dashboard_port=dashboard_port,
        server_name=server_name if client == "openclaw" else None,
        openclaw_command=(
            str(openclaw_command) if client == "openclaw" else None
        ),
    )
    originals = {
        path: path.read_bytes() if path.is_file() else None
        for path in (launcher, dashboard_launcher, metadata_path)
    }
    try:
        _atomic_write(
            dashboard_launcher,
            _render_dashboard_launcher(python_command, resolved_dotenv),
            executable=True,
        )
        _atomic_write(
            launcher,
            _render_launcher(resolved_server, resolved_dotenv),
            executable=True,
        )
        _atomic_write(
            metadata_path,
            json.dumps(installation.to_dict(), indent=2, ensure_ascii=False)
            + "\n",
            executable=False,
        )
    except Exception:
        for path, content in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(
                    path,
                    content.decode("utf-8"),
                    executable=path != metadata_path,
                )
        raise
    return installation


def _render_launcher(server_command: Path, dotenv_path: Path) -> str:
    if os.name == "nt":  # pragma: no cover - rendered on Windows
        server = str(server_command).replace("%", "%%")
        dotenv = str(dotenv_path).replace("%", "%%")
        return f'@echo off\r\n"{server}" --dotenv "{dotenv}" %*\r\n'
    return (
        "#!/bin/sh\n"
        f"exec {shlex.quote(str(server_command))} "
        f'--dotenv {shlex.quote(str(dotenv_path))} "$@"\n'
    )


def _render_dashboard_launcher(
    python_command: Path,
    dotenv_path: Path,
) -> str:
    if os.name == "nt":  # pragma: no cover - rendered on Windows
        python = str(python_command).replace("'", "''")
        dotenv = str(dotenv_path).replace("'", "''")
        return (
            f"$env:HELIX_MCP_DOTENV = '{dotenv}'\r\n"
            f"& '{python}' -m helix_mcp.dashboard_host @args\r\n"
            "exit $LASTEXITCODE\r\n"
        )
    return (
        "#!/bin/sh\n"
        f"export HELIX_MCP_DOTENV={shlex.quote(str(dotenv_path))}\n"
        f"exec {shlex.quote(str(python_command))} "
        '-m helix_mcp.dashboard_host "$@"\n'
    )


def _atomic_write(path: Path, content: str, *, executable: bool) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="",
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        mode = stat.S_IRUSR | stat.S_IWUSR
        if executable:
            mode |= (
                stat.S_IXUSR
                | stat.S_IRGRP
                | stat.S_IXGRP
                | stat.S_IROTH
                | stat.S_IXOTH
            )
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
