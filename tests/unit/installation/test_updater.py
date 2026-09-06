from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from helix_mcp.installation.managed import (
    activate_managed_installation,
    load_managed_installation,
    stable_launcher_path,
    versioned_runtime_paths,
)
from helix_mcp.installation.openclaw import EXPOSED_TOOLS
from helix_mcp.installation.updater import (
    UpdateError,
    check_for_update,
    update_installation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class FakeUpdateRunner:
    def __init__(
        self,
        *,
        wheel_content: bytes,
        digest_content: bytes | None = None,
        fail_smoke_test: bool = False,
        bridge_path: Path | None = None,
    ) -> None:
        self.wheel_content = wheel_content
        self.digest_content = digest_content or wheel_content
        self.fail_smoke_test = fail_smoke_test
        self.bridge_path = bridge_path
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs: object):
        self.commands.append(command)
        if command[1:3] == ["release", "view"]:
            digest = hashlib.sha256(self.digest_content).hexdigest()
            payload = {
                "tagName": "v0.7.0",
                "isDraft": False,
                "isPrerelease": False,
                "url": "https://example.test/releases/v0.7.0",
                "assets": [
                    {
                        "name": "helix_mcp_gateway-0.7.0-py3-none-any.whl",
                        "digest": f"sha256:{digest}",
                    }
                ],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(payload),
                stderr="",
            )
        if command[1:3] == ["release", "download"]:
            destination = Path(command[command.index("--dir") + 1])
            (
                destination / command[command.index("--pattern") + 1]
            ).write_bytes(self.wheel_content)
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if command[1:3] == ["-m", "venv"]:
            executable_dir = Path(command[-1]) / (
                "Scripts" if os.name == "nt" else "bin"
            )
            executable_dir.mkdir(parents=True)
            suffix = ".exe" if os.name == "nt" else ""
            for name in (
                f"python{suffix}",
                f"helix-mcp{suffix}",
                f"helix-mcp-setup{suffix}",
                f"helix-mcp-check{suffix}",
            ):
                (executable_dir / name).write_text(name, encoding="utf-8")
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if any(
            "from importlib.metadata import version" in part
            for part in command
        ):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="0.7.0\n",
                stderr="",
            )
        if Path(command[0]).name.startswith("helix-mcp-setup"):
            assert "--no-managed" in command
            if self.bridge_path is not None:
                self.bridge_path.write_bytes(b"updated bridge")
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if self.fail_smoke_test and Path(command[0]).name.startswith(
            "helix-mcp-check"
        ):
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class OpenClawUpdateRunner(FakeUpdateRunner):
    def __init__(self, *, workspace: Path, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.workspace = workspace
        self.definition: dict[str, object] | None = None

    def __call__(self, command: list[str], **kwargs: object):
        if command[1:3] == ["mcp", "show"]:
            self.commands.append(command)
            payload = {
                "command": str(stable_launcher_path(self.workspace)),
                "args": [],
                "cwd": str(self.workspace),
                "toolFilter": {"include": ["list_targets"]},
            }
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(payload), stderr=""
            )
        if command[1:3] == ["mcp", "set"]:
            self.commands.append(command)
            self.definition = json.loads(command[4])
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["mcp", "probe"]:
            self.commands.append(command)
            payload = {
                "servers": {
                    "helix": {
                        "launch": (
                            f"stdio {stable_launcher_path(self.workspace)}"
                        )
                    }
                }
            }
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(payload), stderr=""
            )
        if command[1:3] == ["mcp", "reload"]:
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        return super().__call__(command, **kwargs)


def _managed_installation(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    workspace = tmp_path / "data"
    config_dir = tmp_path / "config"
    state_dir = tmp_path / "state"
    config_dir.mkdir()
    state_dir.mkdir()
    config_path = config_dir / "helix.yaml"
    config_path.write_bytes((PROJECT_ROOT / "config/helix.yaml").read_bytes())
    bridge_path = workspace / "bridge/helix-arapi-bridge.jar"
    bridge_path.parent.mkdir(parents=True)
    bridge_path.write_bytes(b"original bridge")
    arapi_lib_dir = tmp_path / "arapi"
    arapi_lib_dir.mkdir()
    database_path = state_dir / "write-plans.sqlite3"
    with sqlite3.connect(database_path):
        pass
    key_path = state_dir / "write-plans.key"
    key_path.write_bytes(b"k" * 32)
    dotenv_path = config_dir / ".env"
    dotenv_path.write_text(
        "\n".join(
            (
                f"HELIX_CONFIG_PATH={config_path}",
                f"HELIX_ARAPI_BRIDGE_JAR_PATH={bridge_path}",
                f"HELIX_ARAPI_LIB_DIR={arapi_lib_dir}",
                f"HELIX_WRITE_PLAN_DB_PATH={database_path}",
                f"HELIX_WRITE_PLAN_KEY_PATH={key_path}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _, old_server, _, _ = versioned_runtime_paths(workspace, "0.6.8")
    old_server.parent.mkdir(parents=True)
    old_server.write_text("old server", encoding="utf-8")
    activate_managed_installation(
        workspace=workspace,
        version="0.6.8",
        server_command=old_server,
        dotenv_path=dotenv_path,
    )
    base_python = tmp_path / "python"
    base_python.write_text("python", encoding="utf-8")
    return workspace, dotenv_path, bridge_path, base_python


def test_release_check_requires_a_verified_gateway_wheel(
    tmp_path: Path,
) -> None:
    gh = tmp_path / "gh"
    gh.write_text("gh", encoding="utf-8")
    runner = FakeUpdateRunner(wheel_content=b"signed wheel")

    status = check_for_update(
        current_version="0.6.8",
        gh_command=gh,
        runner=runner,
    )

    assert status.status == "available"
    assert status.latest_version == "0.7.0"
    assert status.update_available is True


def test_update_activates_only_after_setup_and_smoke_test(
    tmp_path: Path,
) -> None:
    workspace, dotenv_path, bridge_path, base_python = _managed_installation(
        tmp_path
    )
    gh = tmp_path / "gh"
    gh.write_text("gh", encoding="utf-8")
    wheel_content = b"published gateway wheel"
    runner = FakeUpdateRunner(
        wheel_content=wheel_content,
        bridge_path=bridge_path,
    )

    result = update_installation(
        dotenv_path=dotenv_path,
        workspace=workspace,
        target_version="0.7.0",
        gh_command=gh,
        base_python=base_python,
        runner=runner,
        clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
    )

    managed = load_managed_installation(workspace)
    assert result.status == "updated"
    assert result.sha256 == hashlib.sha256(wheel_content).hexdigest()
    assert managed is not None
    assert managed.active_version == "0.7.0"
    assert managed.server_command.is_file()
    assert str(managed.server_command) in managed.launcher.read_text()
    assert bridge_path.read_bytes() == b"updated bridge"
    assert (result.backup / "bridge").read_bytes() == b"original bridge"


def test_update_refreshes_reloads_and_probes_openclaw_definition(
    tmp_path: Path,
) -> None:
    workspace, dotenv_path, bridge_path, base_python = _managed_installation(
        tmp_path
    )
    current = load_managed_installation(workspace)
    assert current is not None
    openclaw = tmp_path / "bin/openclaw"
    openclaw.parent.mkdir()
    openclaw.write_text("openclaw", encoding="utf-8")
    activate_managed_installation(
        workspace=workspace,
        version=current.active_version,
        server_command=current.server_command,
        dotenv_path=dotenv_path,
        client="openclaw",
        server_name="helix",
        openclaw_command=openclaw,
    )
    gh = tmp_path / "gh"
    gh.write_text("gh", encoding="utf-8")
    runner = OpenClawUpdateRunner(
        workspace=workspace,
        wheel_content=b"published gateway wheel",
        bridge_path=bridge_path,
    )

    result = update_installation(
        dotenv_path=dotenv_path,
        workspace=workspace,
        target_version="0.7.0",
        gh_command=gh,
        base_python=base_python,
        runner=runner,
        clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
    )

    assert result.openclaw_reloaded is True
    assert result.openclaw_probed is True
    assert runner.definition is not None
    launcher = stable_launcher_path(workspace)
    if os.name == "nt":
        assert Path(str(runner.definition["command"])).name.casefold() == (
            "cmd.exe"
        )
        assert runner.definition["args"] == [
            "/d",
            "/s",
            "/c",
            str(launcher),
        ]
    else:
        assert runner.definition["command"] == str(launcher)
        assert runner.definition["args"] == []
    assert runner.definition["toolFilter"] == {"include": list(EXPOSED_TOOLS)}


def test_update_rejects_a_wheel_that_does_not_match_release_digest(
    tmp_path: Path,
) -> None:
    workspace, dotenv_path, _bridge_path, base_python = _managed_installation(
        tmp_path
    )
    gh = tmp_path / "gh"
    gh.write_text("gh", encoding="utf-8")
    runner = FakeUpdateRunner(
        wheel_content=b"tampered wheel",
        digest_content=b"published wheel",
    )

    with pytest.raises(UpdateError, match="digest mismatch"):
        update_installation(
            dotenv_path=dotenv_path,
            workspace=workspace,
            target_version="0.7.0",
            gh_command=gh,
            base_python=base_python,
            runner=runner,
        )

    managed = load_managed_installation(workspace)
    assert managed is not None
    assert managed.active_version == "0.6.8"


def test_failed_smoke_test_restores_data_and_keeps_previous_launcher(
    tmp_path: Path,
) -> None:
    workspace, dotenv_path, bridge_path, base_python = _managed_installation(
        tmp_path
    )
    gh = tmp_path / "gh"
    gh.write_text("gh", encoding="utf-8")
    before = load_managed_installation(workspace)
    assert before is not None
    runner = FakeUpdateRunner(
        wheel_content=b"published gateway wheel",
        fail_smoke_test=True,
        bridge_path=bridge_path,
    )

    with pytest.raises(UpdateError, match="smoke test failed"):
        update_installation(
            dotenv_path=dotenv_path,
            workspace=workspace,
            target_version="0.7.0",
            gh_command=gh,
            base_python=base_python,
            runner=runner,
            clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
        )

    after = load_managed_installation(workspace)
    assert after is not None
    assert after.active_version == "0.6.8"
    assert after.server_command == before.server_command
    assert str(before.server_command) in after.launcher.read_text()
    assert bridge_path.read_bytes() == b"original bridge"
