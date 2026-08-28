"""Tests for non-destructive per-user installation setup."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from helix_mcp.installation import (
    BridgeBuildResult,
    SetupError,
    setup_installation,
)

setup_implementation = importlib.import_module("helix_mcp.installation.setup")


def test_dry_run_validates_resources_without_writing(tmp_path) -> None:
    result = setup_installation(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.bridge_built is False
    assert result.arapi_lib_dir is None
    assert list(tmp_path.iterdir()) == []


def test_setup_builds_bridge_and_never_overwrites_configuration(
    tmp_path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    library_dir = tmp_path / "developer-studio" / "lib"
    config_dir.mkdir()
    config_path = config_dir / "helix.yaml"
    config_path.write_text("existing: configuration\n", encoding="utf-8")
    monkeypatch.setattr(
        setup_implementation,
        "discover_arapi_lib_dir",
        lambda explicit: library_dir,
    )

    def fake_build(libraries: Path, output: Path) -> BridgeBuildResult:
        assert libraries == library_dir
        output.parent.mkdir(parents=True)
        output.write_bytes(b"bridge")
        return BridgeBuildResult(
            output_path=output,
            package_version="0.4.0",
            source_sha256="a" * 64,
        )

    monkeypatch.setattr(setup_implementation, "build_bridge", fake_build)

    result = setup_installation(
        arapi_lib_dir=library_dir,
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
    )

    assert result.bridge_built is True
    assert result.config_created is False
    assert result.dotenv_created is True
    assert config_path.read_text(encoding="utf-8") == (
        "existing: configuration\n"
    )
    dotenv = result.dotenv_path.read_text(encoding="utf-8")
    values = dotenv_values(
        result.dotenv_path,
        interpolate=False,
        encoding="utf-8",
    )
    assert values["HELIX_CONFIG_PATH"] == str(config_path)
    assert values["HELIX_ARAPI_LIB_DIR"] == str(library_dir)
    assert values["HELIX_WRITE_PLAN_DB_PATH"] == str(
        state_dir / "write-plans.sqlite3"
    )
    assert values["HELIX_WRITE_PLAN_KEY_PATH"] == str(
        state_dir / "write-plans.key"
    )
    assert values["HELIX_METRICS_PATH"] == str(state_dir / "metrics.json")
    assert values["HELIX_OPERATION_LOG_PATH"] == str(
        state_dir / "operations.jsonl"
    )
    assert values["HELIX_OPERATION_LOG_MAX_BYTES"] == "10485760"
    assert values["HELIX_OPERATION_LOG_BACKUP_COUNT"] == "5"
    assert values["HELIX_CREDENTIAL_DEV"] == ""
    assert "password" not in dotenv.casefold()
    key_path = state_dir / "write-plans.key"
    assert len(key_path.read_bytes()) == 32
    if os.name != "nt":
        assert key_path.stat().st_mode & 0o077 == 0


def test_second_setup_preserves_both_configuration_files(
    tmp_path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    library_dir = tmp_path / "lib"
    config_dir.mkdir()
    (config_dir / "helix.yaml").write_text("yaml-original", encoding="utf-8")
    (config_dir / ".env").write_text("dotenv-original", encoding="utf-8")
    monkeypatch.setattr(
        setup_implementation,
        "discover_arapi_lib_dir",
        lambda explicit: library_dir,
    )
    monkeypatch.setattr(
        setup_implementation,
        "build_bridge",
        lambda libraries, output: BridgeBuildResult(
            output_path=output,
            package_version="0.4.0",
            source_sha256="a" * 64,
        ),
    )

    result = setup_installation(
        arapi_lib_dir=library_dir,
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
    )

    assert result.config_created is False
    assert result.dotenv_created is False
    assert (config_dir / "helix.yaml").read_text() == "yaml-original"
    assert (config_dir / ".env").read_text() == "dotenv-original"


def test_setup_rejects_an_existing_invalid_write_plan_key(
    tmp_path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    library_dir = tmp_path / "lib"
    state_dir.mkdir()
    key_path = state_dir / "write-plans.key"
    key_path.write_bytes(b"too-short")
    key_path.chmod(0o600)
    monkeypatch.setattr(
        setup_implementation,
        "discover_arapi_lib_dir",
        lambda explicit: library_dir,
    )
    monkeypatch.setattr(
        setup_implementation,
        "build_bridge",
        lambda libraries, output: BridgeBuildResult(
            output_path=output,
            package_version="0.4.0",
            source_sha256="a" * 64,
        ),
    )

    with pytest.raises(SetupError, match="invalid length"):
        setup_installation(
            arapi_lib_dir=library_dir,
            config_dir=config_dir,
            data_dir=data_dir,
            state_dir=state_dir,
        )
