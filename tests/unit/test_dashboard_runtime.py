from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from helix_mcp.dashboard_host import supervise_dashboard
from helix_mcp.dashboard_runtime import (
    DASHBOARD_SERVICE_NAME,
    DASHBOARD_WINDOWS_RUN_NAME,
    DashboardRuntimeManager,
)
from helix_mcp.installation.managed import activate_managed_installation
from helix_mcp.installation.setup import SetupError


def _installation(tmp_path: Path):
    workspace = tmp_path / "workspace with spaces"
    dotenv = workspace / "config/.env"
    executable_dir = (
        workspace
        / "runtime/0.7.1/venv"
        / ("Scripts" if os.name == "nt" else "bin")
    )
    server = executable_dir / (
        "helix-mcp.exe" if os.name == "nt" else "helix-mcp"
    )
    python = executable_dir / ("python.exe" if os.name == "nt" else "python")
    dotenv.parent.mkdir(parents=True)
    executable_dir.mkdir(parents=True)
    dotenv.write_text("HELIX_CONFIG_PATH=helix.yaml\n", encoding="utf-8")
    server.write_text("server", encoding="utf-8")
    python.write_text("python", encoding="utf-8")
    return activate_managed_installation(
        workspace=workspace,
        version="0.7.1",
        server_command=server,
        dotenv_path=dotenv,
    )


@pytest.mark.skipif(os.name == "nt", reason="systemd is POSIX-only")
def test_installs_no_admin_systemd_user_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    installation = _installation(tmp_path)
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(command)
        output = ""
        if "is-enabled" in command:
            output = "enabled\n"
        elif "is-active" in command:
            output = "active\n"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=output,
            stderr="",
        )

    monkeypatch.setattr(
        "helix_mcp.dashboard_runtime.shutil.which",
        lambda _: "/bin/systemctl",
    )
    monkeypatch.setattr(
        DashboardRuntimeManager,
        "_port_is_open",
        lambda _self: False,
    )
    manager = DashboardRuntimeManager(
        installation,
        gh_command="/opt/github cli/gh",
        runner=runner,
        platform_name="posix",
        user_config_dir=tmp_path / "user-config",
    )

    result = manager.install_and_start(verify=False)

    assert result.status.manager == "systemd_user"
    assert result.status.enabled is True
    unit = (
        tmp_path / "user-config/systemd/user" / DASHBOARD_SERVICE_NAME
    ).read_text(encoding="utf-8")
    assert "Restart=on-failure" in unit
    assert "RestartSec=5s" in unit
    assert "KillMode=process" in unit
    assert "WantedBy=default.target" in unit
    assert "StandardOutput=append:" in unit
    assert "dashboard-supervisor.log" in unit
    assert str(installation.dashboard_launcher) in unit
    assert '"8766"' in unit
    assert '"--gh-command" "/opt/github cli/gh"' in unit
    assert [
        "systemctl",
        "--user",
        "enable",
        "--now",
        DASHBOARD_SERVICE_NAME,
    ] in calls


@pytest.mark.skipif(os.name == "nt", reason="systemd is POSIX-only")
def test_systemd_service_preserves_python_loader_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    installation = _installation(tmp_path)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/custom python/lib")
    manager = DashboardRuntimeManager(
        installation,
        platform_name="posix",
        user_config_dir=tmp_path / "user-config",
    )

    unit = manager._render_unit()

    assert 'Environment="LD_LIBRARY_PATH=/opt/custom python/lib"' in unit


class _FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        return self.values.get(name)

    def write(self, name: str, command: str) -> None:
        self.values[name] = command


def test_windows_uses_per_user_startup_without_task_scheduler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    installation = _installation(tmp_path)
    registry = _FakeRegistry()
    launched: dict[str, object] = {}

    def process_factory(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(
        DashboardRuntimeManager,
        "_supervisor_alive",
        lambda _self: False,
    )
    monkeypatch.setattr(
        DashboardRuntimeManager,
        "_port_is_open",
        lambda _self: False,
    )
    manager = DashboardRuntimeManager(
        installation,
        process_factory=process_factory,
        platform_name="windows",
        windows_registry=registry,
    )

    result = manager.install_and_start(verify=False)

    assert result.process_id == 4321
    assert DASHBOARD_WINDOWS_RUN_NAME in registry.values
    startup = registry.values[DASHBOARD_WINDOWS_RUN_NAME]
    assert "powershell.exe" in startup
    assert "Task Scheduler" not in startup
    assert str(installation.dashboard_launcher) in startup
    assert "supervise" in launched["command"]
    assert int(launched["kwargs"]["creationflags"]) & 0x08000000


def test_falls_back_when_systemd_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    installation = _installation(tmp_path)
    launched: dict[str, object] = {}

    def process_factory(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return SimpleNamespace(pid=7654)

    monkeypatch.setenv("HELIX_MCP_DASHBOARD_MANAGER", "detached")
    monkeypatch.setattr(
        DashboardRuntimeManager,
        "_supervisor_alive",
        lambda _self: False,
    )
    monkeypatch.setattr(
        DashboardRuntimeManager,
        "_port_is_open",
        lambda _self: False,
    )
    manager = DashboardRuntimeManager(
        installation,
        process_factory=process_factory,
        platform_name="posix",
    )

    result = manager.install_and_start(verify=False)

    assert result.process_id == 7654
    assert launched["command"][0] == str(installation.dashboard_launcher)
    assert launched["kwargs"]["start_new_session"] is True
    assert (
        Path(launched["kwargs"]["stdout"].name).name
        == "dashboard-supervisor.log"
    )
    assert launched["kwargs"]["stderr"] is subprocess.STDOUT


def test_supervisor_restarts_after_failure_and_stops_after_clean_exit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    dotenv = workspace / "config/.env"
    dotenv.parent.mkdir(parents=True)
    dotenv.write_text("HELIX_CONFIG_PATH=helix.yaml\n", encoding="utf-8")
    exit_codes = iter([1, 0])
    commands: list[list[str]] = []

    class FakeChild:
        def __init__(self, code: int) -> None:
            self.pid = 5000 + code
            self.returncode = code

        def poll(self):
            return self.returncode

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeChild(next(exit_codes))

    monkeypatch.setattr(
        "helix_mcp.dashboard_host.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "helix_mcp.dashboard_host._port_is_open",
        lambda _port: False,
    )

    result = supervise_dashboard(
        dotenv_path=dotenv,
        workspace=workspace,
        port=8766,
        gh_command="/opt/github-cli/gh",
        restart_seconds=0,
    )

    assert result == 0
    assert len(commands) == 2
    assert all(
        command[-3:-1] == ["--gh-command", "/opt/github-cli/gh"]
        for command in commands
    )
    assert not (workspace / "runtime/dashboard-supervisor.json").exists()


def test_install_rejects_port_owned_by_another_service(
    tmp_path: Path,
) -> None:
    installation = _installation(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        conflicting = activate_managed_installation(
            workspace=installation.launcher.parent.parent,
            version=installation.active_version,
            server_command=installation.server_command,
            dotenv_path=installation.dotenv_path,
            dashboard_port=listener.getsockname()[1],
        )
        manager = DashboardRuntimeManager(conflicting)

        with pytest.raises(SetupError, match="already used"):
            manager.install_and_start(verify=False)
