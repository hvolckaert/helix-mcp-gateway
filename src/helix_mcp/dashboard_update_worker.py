"""Detached update worker that can replace and relaunch the dashboard runtime."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from helix_mcp.dashboard_runtime import DashboardRuntimeManager
from helix_mcp.installation.managed import (
    ManagedInstallation,
    load_managed_installation,
)
from helix_mcp.installation.updater import (
    DEFAULT_REPOSITORY,
    update_installation,
)
from helix_mcp.observability import public_error_code

UPDATE_STATUS_FILENAME = "dashboard-update.json"
_DASHBOARD_TOKEN_ENV = "HELIX_MCP_DASHBOARD_UPDATE_TOKEN"
_WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
_WINDOWS_DETACHED_PROCESS = 0x00000008


@dataclass(frozen=True, slots=True)
class DashboardUpdateWorkerProcess:
    """Public details for a detached update worker."""

    pid: int


class DashboardUpdateWorkerLauncher:
    """Start a worker that remains alive while the dashboard is replaced."""

    def __init__(
        self,
        *,
        dotenv_path: Path,
        workspace: Path,
        errors_path: Path,
        repository: str,
        target_version: str,
        gh_command: str | Path,
        dashboard_port: int,
        dashboard_token: str,
        python_executable: str | None = None,
    ) -> None:
        self.dotenv_path = dotenv_path.expanduser().absolute()
        self.workspace = workspace.expanduser().absolute()
        self.errors_path = errors_path.expanduser().absolute()
        self.repository = repository
        self.target_version = target_version
        self.gh_command = str(gh_command)
        self.dashboard_port = dashboard_port
        self.dashboard_token = dashboard_token
        self.python_executable = python_executable or sys.executable
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> DashboardUpdateWorkerProcess:
        """Launch the worker without inheriting dashboard standard streams."""

        if self._process is not None:
            raise RuntimeError("dashboard update worker was already launched")
        self.errors_path.mkdir(parents=True, exist_ok=True)
        log_path = self.errors_path / "dashboard-update-worker.log"
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        environment[_DASHBOARD_TOKEN_ENV] = self.dashboard_token
        kwargs: dict[str, Any] = {
            "cwd": self.workspace,
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":  # pragma: no cover - Windows installation path
            kwargs["creationflags"] = (
                _WINDOWS_CREATE_NEW_PROCESS_GROUP | _WINDOWS_DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True
        command = [
            self.python_executable,
            "-m",
            "helix_mcp.dashboard_update_worker",
            "--dotenv",
            str(self.dotenv_path),
            "--workspace",
            str(self.workspace),
            "--repository",
            self.repository,
            "--target-version",
            self.target_version,
            "--gh-command",
            self.gh_command,
            "--dashboard-port",
            str(self.dashboard_port),
        ]
        descriptor = _open_private_append(log_path)
        with os.fdopen(descriptor, "ab", buffering=0) as log:
            self._process = subprocess.Popen(command, stderr=log, **kwargs)
        process = self._process
        threading.Thread(
            target=process.wait,
            name="helix-dashboard-update-worker-reaper",
            daemon=True,
        ).start()
        return DashboardUpdateWorkerProcess(pid=process.pid)


def update_status_path(workspace: str | Path) -> Path:
    """Return the shared dashboard update-state file."""

    return (
        Path(workspace).expanduser().absolute()
        / "runtime"
        / UPDATE_STATUS_FILENAME
    )


def load_update_status(workspace: str | Path) -> dict[str, object] | None:
    """Read a bounded public update status when one exists."""

    path = update_status_path(workspace)
    if not path.is_file() or path.stat().st_size > 65_536:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_update_status(
    workspace: str | Path,
    payload: Mapping[str, object],
) -> None:
    """Atomically persist sanitized progress across dashboard restarts."""

    target = update_status_path(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                payload, stream, ensure_ascii=False, separators=(",", ":")
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def run_update(
    *,
    dotenv_path: str | Path,
    workspace: str | Path,
    repository: str,
    target_version: str,
    gh_command: str | Path,
    dashboard_port: int,
    dashboard_token: str,
) -> bool:
    """Stop the dashboard, update atomically, and relaunch it."""

    resolved_dotenv = Path(dotenv_path).expanduser().absolute()
    resolved_workspace = Path(workspace).expanduser().absolute()
    current_python = Path(sys.executable).resolve()
    started_at = _timestamp()
    write_update_status(
        resolved_workspace,
        {
            "status": "preparing",
            "target_version": target_version,
            "started_at": started_at,
            "process_id": os.getpid(),
        },
    )
    dashboard_runtime: dict[str, object] | None = None

    def activate_dashboard(installation: ManagedInstallation) -> None:
        nonlocal dashboard_runtime
        dashboard_runtime = (
            DashboardRuntimeManager(installation)
            .restart_and_verify(
                expected_version=installation.active_version,
            )
            .to_dict()
        )

    try:
        _request_dashboard_shutdown(dashboard_port, dashboard_token)
        _wait_until_port_is_free(dashboard_port)
        write_update_status(
            resolved_workspace,
            {
                "status": "running",
                "target_version": target_version,
                "started_at": started_at,
                "process_id": os.getpid(),
            },
        )
        result = update_installation(
            dotenv_path=resolved_dotenv,
            workspace=resolved_workspace,
            target_version=target_version,
            repository=repository,
            gh_command=gh_command,
            base_python=current_python,
            post_activation_check=activate_dashboard,
        )
        gateway_restarted = _restart_openclaw_gateway(resolved_workspace)
        success_state: dict[str, object] = {
            "status": "success",
            "current_version": result.target_version,
            "target_version": result.target_version,
            "started_at": started_at,
            "finished_at": _timestamp(),
            "gateway_restarted": gateway_restarted,
        }
        if dashboard_runtime is not None:
            success_state["dashboard_runtime"] = dashboard_runtime
        write_update_status(resolved_workspace, success_state)
        succeeded = True
    except Exception as exc:
        write_update_status(
            resolved_workspace,
            {
                "status": "error",
                "target_version": target_version,
                "started_at": started_at,
                "finished_at": _timestamp(),
                "error_code": public_error_code(exc),
            },
        )
        succeeded = False
    if dashboard_runtime is None:
        try:
            managed = load_managed_installation(resolved_workspace)
            if managed is None:
                raise RuntimeError(
                    "managed installation metadata disappeared during update"
                )
            dashboard_runtime = (
                DashboardRuntimeManager(managed)
                .restart_and_verify(expected_version=managed.active_version)
                .to_dict()
            )
        except Exception:
            return False
    return succeeded


def _request_dashboard_shutdown(port: int, token: str) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/update/prepare",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "2",
                "X-Helix-Dashboard-Token": token,
            },
        )
        response = connection.getresponse()
        response.read()
        if response.status != 202:
            raise RuntimeError("dashboard did not accept update shutdown")
    finally:
        connection.close()


def _wait_until_port_is_free(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(0.2)
            if client.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.05)
    raise RuntimeError("dashboard did not stop before the update")


def _restart_openclaw_gateway(workspace: Path) -> bool:
    managed = load_managed_installation(workspace)
    if managed is None or managed.client != "openclaw":
        return False
    command = managed.openclaw_command
    if not command:
        return False
    completed = subprocess.run(
        [command, "gateway", "restart"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed.returncode == 0


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _open_private_append(path: Path) -> int:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise RuntimeError("update worker log is not a regular file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    if os.name != "nt":
        os.chmod(path, 0o600)
    return descriptor


def main(argv: Sequence[str] | None = None) -> int:
    """Worker command-line entry point."""

    parser = argparse.ArgumentParser(prog="helix-mcp-dashboard-update")
    parser.add_argument("--dotenv", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--gh-command", default="gh")
    parser.add_argument("--dashboard-port", type=int, required=True)
    arguments = parser.parse_args(argv)
    token = os.environ.pop(_DASHBOARD_TOKEN_ENV, "")
    if not token:
        return 1
    return (
        0
        if run_update(
            dotenv_path=arguments.dotenv,
            workspace=arguments.workspace,
            repository=arguments.repository,
            target_version=arguments.target_version,
            gh_command=arguments.gh_command,
            dashboard_port=arguments.dashboard_port,
            dashboard_token=token,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
