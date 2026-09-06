from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import helix_mcp.dashboard as dashboard_module
import helix_mcp.dashboard_update_worker as worker_module
from helix_mcp.dashboard_update_worker import (
    load_update_status,
    run_update,
    write_update_status,
)
from helix_mcp.installation.managed import versioned_runtime_paths


def test_update_status_round_trip_is_atomic(tmp_path: Path) -> None:
    write_update_status(
        tmp_path,
        {"status": "running", "target_version": "0.7.0"},
    )

    assert load_update_status(tmp_path) == {
        "status": "running",
        "target_version": "0.7.0",
    }


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
    monkeypatch.setattr(
        worker_module,
        "update_installation",
        lambda **kwargs: SimpleNamespace(
            target_runtime=target_runtime,
            target_version="0.7.0",
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "_restart_openclaw_gateway",
        lambda workspace: False,
    )

    class FakeDashboardLauncher:
        def __init__(self, **kwargs: object) -> None:
            calls["launcher"] = kwargs

        def start(self, *, open_browser: bool) -> SimpleNamespace:
            calls["open_browser"] = open_browser
            return SimpleNamespace(pid=8_766)

    monkeypatch.setattr(
        dashboard_module,
        "DashboardProcessLauncher",
        FakeDashboardLauncher,
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
    launcher = calls["launcher"]
    assert isinstance(launcher, dict)
    expected_python, _, _, _ = versioned_runtime_paths(workspace, "0.7.0")
    assert launcher["python_executable"] == str(expected_python)
    assert calls["open_browser"] is False
    status = load_update_status(workspace)
    assert status is not None
    assert status["status"] == "success"
    assert status["current_version"] == "0.7.0"
