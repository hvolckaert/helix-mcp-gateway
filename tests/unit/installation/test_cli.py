"""Tests for installation command-line contracts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from helix_mcp.installation import InstallPaths, SetupResult
from helix_mcp.installation.cli import _installed_server_command, setup_main
from helix_mcp.installation.managed import stable_launcher_path


def test_setup_dry_run_returns_machine_readable_codex_configuration(
    tmp_path,
    capsys,
) -> None:
    result = setup_main(
        [
            "--dry-run",
            "--config-dir",
            str(tmp_path / "config"),
            "--data-dir",
            str(tmp_path / "data"),
            "--state-dir",
            str(tmp_path / "state"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["status"] == "ready_for_configuration"
    assert payload["dry_run"] is True
    assert payload["codex_desktop"]["args"] == [
        "--dotenv",
        str(tmp_path / "config" / ".env"),
    ]
    assert payload["dashboard"] == {
        "command": "helix-mcp-dashboard",
        "args": [
            "--dotenv",
            str(tmp_path / "config" / ".env"),
            "--port",
            "8766",
        ],
        "url": "http://127.0.0.1:8766/",
        "browser_requested": True,
    }


def test_setup_failure_exposes_only_a_stable_code(monkeypatch, capsys) -> None:
    class PrivateFailure(RuntimeError):
        code = "SAFE_INSTALL_FAILURE"

    monkeypatch.setattr(
        "helix_mcp.installation.cli.setup_installation",
        lambda **kwargs: (_ for _ in ()).throw(
            PrivateFailure("private path and credential")
        ),
    )

    result = setup_main(["--dry-run"])

    payload = json.loads(capsys.readouterr().out)
    assert result == 1
    assert payload == {
        "status": "failed",
        "error_code": "SAFE_INSTALL_FAILURE",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv Python is a symlink")
def test_installed_server_is_resolved_next_to_the_venv_python(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable_dir = tmp_path / "venv/bin"
    executable_dir.mkdir(parents=True)
    python = executable_dir / "python"
    python.symlink_to(Path(sys.executable).resolve())
    server = executable_dir / "helix-mcp"
    server.write_text("server", encoding="utf-8")
    monkeypatch.setattr(
        "helix_mcp.installation.cli.sys.executable", str(python)
    )

    assert _installed_server_command() == server.absolute()


def test_setup_help_explains_prerequisites_and_destination_paths(
    capsys,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        setup_main(["--help"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "non-destructive per-user" in output
    assert "authorized arapi, arapiext" in output
    assert "arlogger JARs" in output
    assert "HELIX_ARAPI_LIB_DIR" in output
    assert "--config-dir DIR" in output
    assert "--data-dir DIR" in output
    assert "--state-dir DIR" in output
    assert "platform per-user" in output
    assert "never overwritten" in output


def test_setup_starts_detached_dashboard_by_default(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    paths = InstallPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
    )
    setup_result = SetupResult(
        paths=paths,
        dotenv_path=paths.config_dir / ".env",
        config_path=paths.config_dir / "helix.yaml",
        bridge_path=paths.data_dir / "bridge/helix-arapi-bridge.jar",
        arapi_lib_dir=Path("/arapi/lib"),
        dotenv_created=True,
        config_created=True,
        bridge_built=True,
        dry_run=False,
        server_command="helix-mcp",
    )
    setup_result.dotenv_path.parent.mkdir(parents=True)
    setup_result.dotenv_path.write_text("HELIX_CONFIG_PATH=helix.yaml\n")
    installed_server = tmp_path / "venv/bin/helix-mcp"
    installed_server.parent.mkdir(parents=True)
    installed_server.write_text("server", encoding="utf-8")
    (
        installed_server.parent
        / ("python.exe" if os.name == "nt" else "python")
    ).write_text("python", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "helix_mcp.installation.cli.setup_installation",
        lambda **kwargs: setup_result,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli._installed_server_command",
        lambda: installed_server,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli._package_version",
        lambda: "0.7.0",
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.find_openclaw_command",
        lambda candidate=None: None,
    )

    class FakeRuntimeManager:
        def __init__(self, installation) -> None:
            captured["installation"] = installation

        def install_and_start(self):
            return SimpleNamespace(
                to_dict=lambda: {
                    "manager": "systemd_user",
                    "installed": True,
                    "enabled": True,
                    "active": True,
                }
            )

    monkeypatch.setattr(
        "helix_mcp.installation.cli.DashboardRuntimeManager",
        FakeRuntimeManager,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.webbrowser.open", lambda url: True
    )

    result = setup_main([])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["dashboard"] == {
        "manager": "systemd_user",
        "installed": True,
        "enabled": True,
        "active": True,
        "url": "http://127.0.0.1:8766/",
    }
    assert captured["installation"].dotenv_path == setup_result.dotenv_path
    assert payload["codex_desktop"]["command"] == str(
        stable_launcher_path(paths.data_dir)
    )
    assert payload["codex_desktop"]["args"] == []
    assert payload["client_integration"] == "standalone"


def test_setup_can_keep_dashboard_without_opening_browser(
    tmp_path, monkeypatch, capsys
) -> None:
    paths = InstallPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
    )
    setup_result = SetupResult(
        paths=paths,
        dotenv_path=paths.config_dir / ".env",
        config_path=paths.config_dir / "helix.yaml",
        bridge_path=paths.data_dir / "bridge/helix-arapi-bridge.jar",
        arapi_lib_dir=Path("/arapi/lib"),
        dotenv_created=False,
        config_created=False,
        bridge_built=True,
        dry_run=False,
        server_command="helix-mcp",
    )
    setup_result.dotenv_path.parent.mkdir(parents=True)
    setup_result.dotenv_path.write_text("HELIX_CONFIG_PATH=helix.yaml\n")
    installed_server = tmp_path / "venv/bin/helix-mcp"
    installed_server.parent.mkdir(parents=True)
    installed_server.write_text("server", encoding="utf-8")
    (
        installed_server.parent
        / ("python.exe" if os.name == "nt" else "python")
    ).write_text("python", encoding="utf-8")
    monkeypatch.setattr(
        "helix_mcp.installation.cli.setup_installation",
        lambda **kwargs: setup_result,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli._installed_server_command",
        lambda: installed_server,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli._package_version",
        lambda: "0.7.0",
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.find_openclaw_command",
        lambda candidate=None: None,
    )

    class FakeRuntimeManager:
        def __init__(self, installation) -> None:
            self.installation = installation

        def install_and_start(self):
            return SimpleNamespace(
                to_dict=lambda: {"manager": "systemd_user", "active": True}
            )

    monkeypatch.setattr(
        "helix_mcp.installation.cli.DashboardRuntimeManager",
        FakeRuntimeManager,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.webbrowser.open",
        lambda url: pytest.fail("browser should not open"),
    )

    result = setup_main(["--no-dashboard"])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["dashboard"] == {
        "manager": "systemd_user",
        "active": True,
        "url": "http://127.0.0.1:8766/",
    }
    assert payload["codex_desktop"]["command"] == str(
        stable_launcher_path(paths.data_dir)
    )
    assert payload["codex_desktop"]["args"] == []


def test_setup_auto_registers_detected_openclaw(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    paths = InstallPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
    )
    setup_result = SetupResult(
        paths=paths,
        dotenv_path=paths.config_dir / ".env",
        config_path=paths.config_dir / "helix.yaml",
        bridge_path=paths.data_dir / "bridge/helix-arapi-bridge.jar",
        arapi_lib_dir=tmp_path / "arapi/lib",
        dotenv_created=True,
        config_created=True,
        bridge_built=True,
        dry_run=False,
        server_command="helix-mcp",
    )
    setup_result.dotenv_path.parent.mkdir(parents=True)
    setup_result.dotenv_path.write_text(
        "HELIX_CONFIG_PATH=helix.yaml\n", encoding="utf-8"
    )
    installed_server = tmp_path / "venv/bin/helix-mcp"
    installed_server.parent.mkdir(parents=True)
    installed_server.write_text("server", encoding="utf-8")
    (
        installed_server.parent
        / ("python.exe" if os.name == "nt" else "python")
    ).write_text("python", encoding="utf-8")
    openclaw = tmp_path / "bin/openclaw"
    openclaw.parent.mkdir()
    openclaw.write_text("openclaw", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "helix_mcp.installation.cli.setup_installation",
        lambda **kwargs: setup_result,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli._installed_server_command",
        lambda: installed_server,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli._package_version",
        lambda: "0.7.0",
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.find_openclaw_command",
        lambda candidate=None: openclaw,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.register_openclaw_server",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.DashboardRuntimeManager",
        lambda installation: SimpleNamespace(
            install_and_start=lambda: SimpleNamespace(
                to_dict=lambda: {"manager": "systemd_user", "active": True}
            )
        ),
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.webbrowser.open", lambda url: True
    )

    result = setup_main([])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["client_integration"] == "openclaw"
    assert captured["openclaw_command"] == openclaw
    assert captured["server_name"] == "helix"
    assert captured["launcher"] == stable_launcher_path(paths.data_dir)
