"""Tests for automatic OpenClaw integration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from helix_mcp.installation.managed import activate_managed_installation
from helix_mcp.installation.openclaw import (
    EXPOSED_TOOLS,
    OpenClawIntegrationError,
    register_openclaw_server,
    reload_managed_openclaw,
)


class FakeRunner:
    def __init__(self, *, probe_ok: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.probe_ok = probe_ok

    def __call__(self, command: list[str], **kwargs: object):
        self.calls.append(command)
        operation = command[2]
        if operation == "show":
            return subprocess.CompletedProcess(command, 1, "", "missing")
        if operation == "probe":
            launcher = json.loads(self.calls[1][4])["command"]
            output = (
                json.dumps(
                    {"servers": {"helix": {"launch": f"stdio {launcher}"}}}
                )
                if self.probe_ok
                else json.dumps({"diagnostics": ["failed"]})
            )
            return subprocess.CompletedProcess(command, 0, output, "")
        return subprocess.CompletedProcess(command, 0, "", "")


def _commands(tmp_path: Path) -> tuple[Path, Path, Path]:
    openclaw = tmp_path / "bin/openclaw"
    launcher = tmp_path / "workspace/bin/helix-mcp"
    for path in (openclaw, launcher):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("executable", encoding="utf-8")
    return openclaw, launcher, launcher.parent.parent


def test_registration_sets_complete_definition_reloads_and_probes(
    tmp_path: Path,
) -> None:
    openclaw, launcher, workspace = _commands(tmp_path)
    runner = FakeRunner()

    result = register_openclaw_server(
        launcher=launcher,
        workspace=workspace,
        openclaw_command=openclaw,
        runner=runner,
    )

    assert result.reloaded is True
    assert result.probed is True
    assert [call[2] for call in runner.calls] == [
        "show",
        "set",
        "reload",
        "probe",
    ]
    definition = json.loads(runner.calls[1][4])
    assert definition["command"] == str(launcher)
    assert definition["args"] == []
    assert definition["cwd"] == str(workspace)
    assert definition["toolFilter"]["include"] == list(EXPOSED_TOOLS)


def test_failed_probe_removes_new_definition_and_reloads(
    tmp_path: Path,
) -> None:
    openclaw, launcher, workspace = _commands(tmp_path)
    runner = FakeRunner(probe_ok=False)

    with pytest.raises(OpenClawIntegrationError, match="diagnostics"):
        register_openclaw_server(
            launcher=launcher,
            workspace=workspace,
            openclaw_command=openclaw,
            runner=runner,
        )

    assert [call[2] for call in runner.calls][-2:] == ["unset", "reload"]


def test_reload_managed_openclaw_uses_recorded_command(
    tmp_path: Path,
) -> None:
    openclaw, server, workspace = _commands(tmp_path)
    dotenv = tmp_path / "config/.env"
    dotenv.parent.mkdir()
    dotenv.write_text("HELIX_CONFIG_PATH=helix.yaml\n", encoding="utf-8")
    installation = activate_managed_installation(
        workspace=workspace,
        version="0.7.0",
        server_command=server,
        dotenv_path=dotenv,
        client="openclaw",
        server_name="helix",
        openclaw_command=openclaw,
    )
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    reload_managed_openclaw(installation, runner=runner)

    assert calls == [[str(openclaw), "mcp", "reload"]]
