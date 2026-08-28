"""Tests for installation command-line contracts."""

from __future__ import annotations

import json

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
