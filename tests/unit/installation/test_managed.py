from __future__ import annotations

import json
import os
from pathlib import Path

from helix_mcp.installation.managed import (
    activate_managed_installation,
    installation_metadata_path,
    load_managed_installation,
    stable_dashboard_launcher_path,
    stable_launcher_path,
    supports_transactional_updates,
)


def test_managed_installation_creates_stable_launcher(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace with spaces"
    executable_dir = (
        workspace
        / "runtime/0.7.0/venv"
        / ("Scripts" if os.name == "nt" else "bin")
    )
    suffix = ".exe" if os.name == "nt" else ""
    server = executable_dir / f"helix-mcp{suffix}"
    dotenv = workspace / "config/.env"
    server.parent.mkdir(parents=True)
    dotenv.parent.mkdir(parents=True)
    server.write_text("server", encoding="utf-8")
    (
        server.parent / ("python.exe" if os.name == "nt" else "python")
    ).write_text("python", encoding="utf-8")
    dotenv.write_text("HELIX_CONFIG_PATH=config.yaml\n", encoding="utf-8")

    installation = activate_managed_installation(
        workspace=workspace,
        version="0.7.0",
        server_command=server,
        dotenv_path=dotenv,
    )

    assert installation.launcher == stable_launcher_path(workspace)
    assert installation.dashboard_launcher == stable_dashboard_launcher_path(
        workspace
    )
    assert installation.launcher.is_file()
    assert installation.dashboard_launcher.is_file()
    content = installation.launcher.read_text(encoding="utf-8")
    assert str(server) in content
    assert str(dotenv) in content
    if os.name != "nt":
        assert content.startswith("#!/bin/sh\n")
        assert os.access(installation.launcher, os.X_OK)
        dashboard_content = installation.dashboard_launcher.read_text(
            encoding="utf-8"
        )
        assert "-m helix_mcp.dashboard_host" in dashboard_content
        assert str(dotenv) in dashboard_content
    assert load_managed_installation(workspace) == installation
    assert supports_transactional_updates(installation)


def test_managed_launcher_switches_without_changing_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    dotenv = workspace / "config/.env"
    dotenv.parent.mkdir(parents=True)
    dotenv.write_text("HELIX_CONFIG_PATH=config.yaml\n", encoding="utf-8")
    suffix = ".exe" if os.name == "nt" else ""
    executable_dir = "Scripts" if os.name == "nt" else "bin"
    old_server = (
        workspace / f"runtime/0.6.8/venv/{executable_dir}/helix-mcp{suffix}"
    )
    new_server = (
        workspace / f"runtime/0.7.0/venv/{executable_dir}/helix-mcp{suffix}"
    )
    old_server.parent.mkdir(parents=True)
    new_server.parent.mkdir(parents=True)
    old_server.write_text("old", encoding="utf-8")
    new_server.write_text("new", encoding="utf-8")
    for server in (old_server, new_server):
        (
            server.parent / ("python.exe" if os.name == "nt" else "python")
        ).write_text("python", encoding="utf-8")

    first = activate_managed_installation(
        workspace=workspace,
        version="0.6.8",
        server_command=old_server,
        dotenv_path=dotenv,
    )
    second = activate_managed_installation(
        workspace=workspace,
        version="0.7.0",
        server_command=new_server,
        dotenv_path=dotenv,
    )

    assert first.launcher == second.launcher
    assert first.dashboard_launcher == second.dashboard_launcher
    content = second.launcher.read_text(encoding="utf-8")
    assert str(new_server) in content
    assert str(old_server) not in content
    loaded = load_managed_installation(workspace)
    assert loaded is not None
    assert loaded.active_version == "0.7.0"
    assert loaded.dashboard_port == 8766


def test_schema_one_metadata_loads_with_dashboard_defaults(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    launcher = stable_launcher_path(workspace)
    server = workspace / "runtime/0.7.0/venv/bin/helix-mcp"
    dotenv = workspace / "config/.env"
    metadata = installation_metadata_path(workspace)
    for path in (launcher, server, dotenv, metadata):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_version": "0.7.0",
                "client": "standalone",
                "launcher": str(launcher),
                "server_command": str(server),
                "dotenv_path": str(dotenv),
            }
        ),
        encoding="utf-8",
    )

    installation = load_managed_installation(workspace)

    assert installation is not None
    assert installation.schema_version == 2
    assert installation.dashboard_launcher == stable_dashboard_launcher_path(
        workspace
    )
    assert installation.dashboard_port == 8766
    assert not supports_transactional_updates(installation)
