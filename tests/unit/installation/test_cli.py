"""Tests for installation command-line contracts."""

from __future__ import annotations

import json

import pytest

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
