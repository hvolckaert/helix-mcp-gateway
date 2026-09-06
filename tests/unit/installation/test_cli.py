"""Tests for installation command-line contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from helix_mcp.installation import InstallPaths, SetupResult
from helix_mcp.installation.cli import setup_main


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
        ],
        "url": "http://127.0.0.1:8766/",
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
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "helix_mcp.installation.cli.setup_installation",
        lambda **kwargs: setup_result,
    )

    class FakeLauncher:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def start(self):
            return SimpleNamespace(
                to_dict=lambda: {
                    "pid": 1_234,
                    "url": "http://127.0.0.1:8766/",
                    "reused": False,
                }
            )

    monkeypatch.setattr(
        "helix_mcp.installation.cli.DashboardProcessLauncher",
        FakeLauncher,
    )

    result = setup_main([])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["dashboard"] == {
        "pid": 1_234,
        "url": "http://127.0.0.1:8766/",
        "reused": False,
    }
    assert captured == {
        "dotenv_path": setup_result.dotenv_path,
        "errors_path": paths.state_dir / "errors",
    }


def test_setup_can_skip_dashboard(tmp_path, monkeypatch, capsys) -> None:
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
    monkeypatch.setattr(
        "helix_mcp.installation.cli.setup_installation",
        lambda **kwargs: setup_result,
    )
    monkeypatch.setattr(
        "helix_mcp.installation.cli.DashboardProcessLauncher",
        lambda **kwargs: pytest.fail("dashboard should not launch"),
    )

    result = setup_main(["--no-dashboard"])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["dashboard"] is None
