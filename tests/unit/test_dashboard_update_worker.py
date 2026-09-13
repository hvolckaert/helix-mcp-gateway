from __future__ import annotations

import os
import stat
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import helix_mcp.dashboard_update_worker as worker_module
from helix_mcp.dashboard_update_worker import (
    DashboardUpdateWorkerLauncher,
    _consume_dashboard_token,
    _write_dashboard_token,
    dashboard_update_worker_is_active,
    load_update_status,
    main,
    run_update,
    write_update_status,
)


def _launcher(tmp_path: Path) -> DashboardUpdateWorkerLauncher:
    return DashboardUpdateWorkerLauncher(
        dotenv_path=tmp_path / "config/.env",
        workspace=tmp_path / "workspace with spaces",
        errors_path=tmp_path / "errors",
        repository="owner/repository",
        target_version="1.2.3",
        gh_command="/opt/github cli/gh",
        dashboard_port=8_766,
        dashboard_token="private-dashboard-token",
        python_executable="/runtime/python",
    )


def test_update_status_round_trip_is_atomic(tmp_path: Path) -> None:
    write_update_status(
        tmp_path,
        {"status": "running", "target_version": "0.7.0"},
    )

    assert load_update_status(tmp_path) == {
        "status": "running",
        "target_version": "0.7.0",
    }


def test_detached_launcher_uses_one_time_token_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    finished = threading.Event()

    class FakeProcess:
        pid = 4_321

        def wait(self) -> int:
            finished.wait(timeout=5)
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.delenv("HELIX_MCP_DASHBOARD_MODE", raising=False)
    monkeypatch.setenv(
        "HELIX_MCP_DASHBOARD_UPDATE_TOKEN",
        "legacy-environment-token",
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    process = _launcher(tmp_path).start()

    assert process.pid == 4_321
    command = captured["command"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert isinstance(kwargs, dict)
    assert "private-dashboard-token" not in command
    token_index = command.index("--dashboard-token-file")
    token_path = Path(command[token_index + 1])
    assert token_path.read_text(encoding="utf-8") == (
        "private-dashboard-token"
    )
    if os.name != "nt":
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
        assert kwargs["start_new_session"] is True
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert "HELIX_MCP_DASHBOARD_UPDATE_TOKEN" not in environment
    assert _consume_dashboard_token(token_path) == "private-dashboard-token"
    assert not token_path.exists()
    finished.set()


@pytest.mark.skipif(
    os.name == "nt", reason="systemd user services are POSIX-only"
)
def test_systemd_launcher_uses_independent_transient_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        captured.append((command, kwargs))
        if command[0] == "/usr/bin/systemctl":
            return SimpleNamespace(returncode=0, stdout="4321\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("HELIX_MCP_DASHBOARD_MODE", "systemd_user")
    monkeypatch.setenv(
        "HELIX_MCP_DASHBOARD_UPDATE_TOKEN",
        "legacy-environment-token",
    )
    monkeypatch.setattr(
        worker_module.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("systemd launch used Popen"),
    )

    process = _launcher(tmp_path).start()

    assert process.pid == 4_321
    launch_command, launch_kwargs = captured[0]
    assert launch_command[0] == "/usr/bin/systemd-run"
    assert "--user" in launch_command
    assert "--collect" in launch_command
    assert "--service-type=exec" in launch_command
    assert any(
        item.startswith("--unit=helix-mcp-dashboard-update-")
        for item in launch_command
    )
    assert "--property=StandardInput=null" in launch_command
    assert "--property=StandardOutput=null" in launch_command
    assert "--property=UMask=0077" in launch_command
    assert (
        "--property=UnsetEnvironment=HELIX_MCP_DASHBOARD_UPDATE_TOKEN"
        in launch_command
    )
    assert "private-dashboard-token" not in launch_command
    assert "legacy-environment-token" not in launch_command
    environment = launch_kwargs["env"]
    assert isinstance(environment, dict)
    assert "HELIX_MCP_DASHBOARD_UPDATE_TOKEN" not in environment
    token_index = launch_command.index("--dashboard-token-file")
    token_path = Path(launch_command[token_index + 1])
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert _consume_dashboard_token(token_path) == "private-dashboard-token"


def test_launcher_removes_token_if_process_start_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HELIX_MCP_DASHBOARD_MODE", raising=False)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("failed")),
    )

    with pytest.raises(OSError, match="failed"):
        _launcher(tmp_path).start()

    assert not list((tmp_path / "errors").glob(".dashboard-update-token-*"))


@pytest.mark.skipif(os.name == "nt", reason="simulates the Windows branch")
def test_windows_launcher_uses_detached_popen_without_token_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    finished = threading.Event()

    class FakeProcess:
        pid = 7_654

        def wait(self) -> int:
            finished.wait(timeout=5)
            return 0

    launcher = _launcher(tmp_path)
    monkeypatch.setattr(worker_module.os, "name", "nt")
    monkeypatch.setenv("HELIX_MCP_DASHBOARD_MODE", "systemd_user")

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    process = launcher.start()

    assert process.pid == 7_654
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["creationflags"] == (
        worker_module._WINDOWS_CREATE_NEW_PROCESS_GROUP
        | worker_module._WINDOWS_DETACHED_PROCESS
    )
    assert "start_new_session" not in kwargs
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert "HELIX_MCP_DASHBOARD_UPDATE_TOKEN" not in environment
    command = captured["command"]
    assert isinstance(command, list)
    token_path = type(tmp_path)(
        command[command.index("--dashboard-token-file") + 1]
    )
    assert _consume_dashboard_token(token_path) == "private-dashboard-token"
    finished.set()


@pytest.mark.skipif(
    os.name == "nt", reason="systemd user services are POSIX-only"
)
def test_systemd_launch_failure_stops_unit_and_removes_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="failed")

    monkeypatch.setenv("HELIX_MCP_DASHBOARD_MODE", "systemd_user")
    monkeypatch.setattr(
        worker_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="independent dashboard update"):
        _launcher(tmp_path).start()

    assert calls[0][0] == "/usr/bin/systemd-run"
    assert calls[-1][:3] == ["/usr/bin/systemctl", "--user", "stop"]
    assert not list((tmp_path / "errors").glob(".dashboard-update-token-*"))


def test_token_consumer_rejects_insecure_file_and_removes_it(
    tmp_path: Path,
) -> None:
    token_path = _write_dashboard_token(tmp_path, "secret")
    if os.name != "nt":
        token_path.chmod(0o644)
        with pytest.raises(RuntimeError, match="not private"):
            _consume_dashboard_token(token_path)
        assert not token_path.exists()


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX no-follow file opens",
)
def test_token_consumer_removes_rejected_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("do-not-read", encoding="utf-8")
    token_path = tmp_path / ".dashboard-update-token-symlink"
    token_path.symlink_to(target)

    with pytest.raises(OSError):
        _consume_dashboard_token(token_path)

    assert not token_path.exists()
    assert target.read_text(encoding="utf-8") == "do-not-read"


def test_worker_main_consumes_token_before_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = _write_dashboard_token(tmp_path, "one-use-token")
    captured: dict[str, object] = {}
    monkeypatch.setenv(
        "HELIX_MCP_DASHBOARD_UPDATE_TOKEN",
        "legacy-environment-token",
    )
    monkeypatch.setattr(
        worker_module,
        "run_update",
        lambda **kwargs: captured.update(kwargs) is None,
    )

    result = main(
        [
            "--dotenv",
            str(tmp_path / ".env"),
            "--workspace",
            str(tmp_path),
            "--target-version",
            "1.2.3",
            "--dashboard-port",
            "8766",
            "--dashboard-token-file",
            str(token_path),
        ]
    )

    assert result == 0
    assert captured["dashboard_token"] == "one-use-token"
    assert not token_path.exists()
    assert "HELIX_MCP_DASHBOARD_UPDATE_TOKEN" not in os.environ


def test_worker_pid_must_match_expected_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_module.os, "kill", lambda _pid, _signal: None)
    monkeypatch.setattr(
        worker_module,
        "_process_command_line",
        lambda _pid: "/runtime/python -m unrelated.worker",
    )
    assert dashboard_update_worker_is_active(1234) is False

    monkeypatch.setattr(
        worker_module,
        "_process_command_line",
        lambda _pid: "/runtime/python -m helix_mcp.dashboard_update_worker",
    )
    assert dashboard_update_worker_is_active(1234) is True


@pytest.mark.skipif(os.name == "nt", reason="simulates the Windows branch")
def test_windows_worker_inspection_does_not_signal_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_module.os, "name", "nt")
    monkeypatch.setattr(
        worker_module.os,
        "kill",
        lambda *_args: pytest.fail("Windows process inspection used os.kill"),
    )
    monkeypatch.setattr(
        worker_module,
        "_process_command_line",
        lambda _pid: "/runtime/python -m helix_mcp.dashboard_update_worker",
    )

    assert dashboard_update_worker_is_active(1234) is True


def test_worker_relaunches_dashboard_from_updated_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "data"
    config = tmp_path / "helix.yaml"
    config.write_text("schema_version: 2\n", encoding="utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"HELIX_CONFIG_PATH={config}\n", encoding="utf-8")
    target_runtime = workspace / "runtime/0.7.0"
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        worker_module,
        "_request_dashboard_shutdown",
        lambda port, token: calls.update(shutdown=(port, token)),
    )
    monkeypatch.setattr(
        worker_module,
        "_wait_until_port_is_free",
        lambda port: calls.update(port=port),
    )

    def update_installation(**kwargs):
        kwargs["post_activation_check"](
            SimpleNamespace(active_version="0.7.0")
        )
        return SimpleNamespace(
            target_runtime=target_runtime,
            target_version="0.7.0",
        )

    monkeypatch.setattr(
        worker_module, "update_installation", update_installation
    )
    monkeypatch.setattr(
        worker_module,
        "_restart_openclaw_gateway",
        lambda workspace: False,
    )

    class FakeRuntimeManager:
        def __init__(
            self,
            installation: object,
            *,
            gh_command: str,
        ) -> None:
            calls["installation"] = installation
            calls["gh_command"] = gh_command

        def restart_and_verify(
            self, *, expected_version: str
        ) -> SimpleNamespace:
            calls["expected_version"] = expected_version
            return SimpleNamespace(
                to_dict=lambda: {
                    "manager": "systemd_user",
                    "active": True,
                }
            )

    monkeypatch.setattr(
        worker_module,
        "DashboardRuntimeManager",
        FakeRuntimeManager,
    )

    succeeded = run_update(
        dotenv_path=dotenv,
        workspace=workspace,
        repository="hvolckaert/helix-mcp-gateway",
        target_version="0.7.0",
        gh_command="gh",
        dashboard_port=8_766,
        dashboard_token="worker-token",
    )

    assert succeeded is True
    assert calls["shutdown"] == (8_766, "worker-token")
    assert calls["expected_version"] == "0.7.0"
    assert calls["gh_command"] == "gh"
    status = load_update_status(workspace)
    assert status is not None
    assert status["status"] == "success"
    assert status["current_version"] == "0.7.0"
