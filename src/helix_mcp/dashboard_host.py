"""Stable dashboard host with crash recovery for managed installations."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from helix_mcp.dashboard import DEFAULT_DASHBOARD_PORT
from helix_mcp.dashboard_runtime import DASHBOARD_MODE_ENV


def supervisor_state_path(workspace: Path) -> Path:
    """Return the supervisor's process-state file."""

    return workspace.resolve() / "runtime" / "dashboard-supervisor.json"


def supervisor_stop_path(workspace: Path) -> Path:
    """Return the supervisor's cooperative stop marker."""

    return workspace.resolve() / "runtime" / "dashboard-supervisor.stop"


def supervise_dashboard(
    *,
    dotenv_path: Path,
    workspace: Path,
    port: int,
    restart_seconds: float = 5.0,
) -> int:
    """Keep the dashboard alive and restart it after unexpected failures."""

    resolved_workspace = workspace.expanduser().resolve()
    state_path = supervisor_state_path(resolved_workspace)
    stop_path = supervisor_stop_path(resolved_workspace)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.unlink(missing_ok=True)
    stopping = False
    child: subprocess.Popen[bytes] | None = None

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    if os.name != "nt":
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

    while not stopping:
        while _port_is_open(port) and not stopping and not stop_path.exists():
            _write_state(
                state_path,
                port=port,
                child_pid=None,
                status="waiting_for_port",
            )
            time.sleep(0.5)
        if stop_path.exists() or stopping:
            break

        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        command = [
            sys.executable,
            "-m",
            "helix_mcp.dashboard",
            "--dotenv",
            str(dotenv_path),
            "--port",
            str(port),
            "--no-browser",
        ]
        child = subprocess.Popen(
            command,
            cwd=resolved_workspace,
            env=environment,
        )
        _write_state(
            state_path,
            port=port,
            child_pid=child.pid,
            status="running",
        )
        while child.poll() is None and not stopping and not stop_path.exists():
            time.sleep(0.25)
        if stopping or stop_path.exists():
            stopping = True
            _terminate_child(child)
            break
        exit_code = child.returncode or 0
        if exit_code == 0:
            state_path.unlink(missing_ok=True)
            return 0
        _write_state(
            state_path,
            port=port,
            child_pid=None,
            status="restarting",
            exit_code=exit_code,
        )
        deadline = time.monotonic() + restart_seconds
        while (
            time.monotonic() < deadline
            and not stopping
            and not stop_path.exists()
        ):
            time.sleep(min(0.25, deadline - time.monotonic()))

    if child is not None and child.poll() is None:
        _terminate_child(child)
    stop_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    return 0


def _terminate_child(child: subprocess.Popen[bytes]) -> None:
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def _write_state(
    path: Path,
    *,
    port: int,
    child_pid: int | None,
    status: str,
    exit_code: int | None = None,
) -> None:
    temporary = path.with_suffix(".tmp")
    payload: dict[str, object] = {
        "supervisor_pid": os.getpid(),
        "child_pid": child_pid,
        "port": port,
        "status": status,
        "mode": os.environ.get(DASHBOARD_MODE_ENV, "detached"),
    }
    if exit_code is not None:
        payload["last_exit_code"] = exit_code
    temporary.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    """Run the stable dashboard supervisor command."""

    parser = argparse.ArgumentParser(prog="helix-mcp-dashboard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    supervise = subparsers.add_parser(
        "supervise",
        help="keep the managed dashboard running",
    )
    supervise.add_argument(
        "--dotenv",
        default=os.environ.get("HELIX_MCP_DOTENV"),
        required=not bool(os.environ.get("HELIX_MCP_DOTENV")),
    )
    supervise.add_argument("--workspace", required=True)
    supervise.add_argument(
        "--port",
        type=int,
        default=DEFAULT_DASHBOARD_PORT,
    )
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65_535:
        parser.error("dashboard port must be between 1 and 65535")
    return supervise_dashboard(
        dotenv_path=Path(arguments.dotenv).expanduser().resolve(),
        workspace=Path(arguments.workspace).expanduser().resolve(),
        port=arguments.port,
    )


if __name__ == "__main__":
    raise SystemExit(main())
