"""Tests for fixed-environment ``.env`` runtime settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from helix_mcp.config import RuntimeSettingsError, load_runtime_settings


def test_config_path_is_resolved_relative_to_dotenv_file(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "HELIX_CONFIG_PATH=config/custom-helix.yaml\n"
        "HELIX_ARAPI_BRIDGE_JAR_PATH=runtime/bridge.jar\n"
        "HELIX_ARAPI_LIB_DIR=runtime/lib\n",
        encoding="utf-8",
    )

    settings = load_runtime_settings(dotenv, environ={})

    assert settings.config_path == tmp_path / "config" / "custom-helix.yaml"
    assert (
        settings.arapi_bridge_jar_path == tmp_path / "runtime" / "bridge.jar"
    )
    assert settings.arapi_lib_dir == tmp_path / "runtime" / "lib"


def test_audit_settings_are_loaded_and_path_is_resolved(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "HELIX_AUDIT_LOG_PATH=state/audit.jsonl\n"
        "HELIX_AUDIT_LOG_MAX_BYTES=2048\n"
        "HELIX_AUDIT_LOG_BACKUP_COUNT=3\n",
        encoding="utf-8",
    )

    settings = load_runtime_settings(dotenv, environ={})

    assert settings.audit_log_path == tmp_path / "state" / "audit.jsonl"
    assert settings.audit_log_max_bytes == 2048
    assert settings.audit_log_backup_count == 3


def test_persistent_write_plan_paths_are_resolved_together(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "HELIX_WRITE_PLAN_DB_PATH=state/write-plans.sqlite3\n"
        "HELIX_WRITE_PLAN_KEY_PATH=state/write-plans.key\n",
        encoding="utf-8",
    )

    settings = load_runtime_settings(dotenv, environ={})

    assert settings.write_plan_db_path == (
        tmp_path / "state" / "write-plans.sqlite3"
    )
    assert settings.write_plan_key_path == (
        tmp_path / "state" / "write-plans.key"
    )


def test_advanced_observability_paths_and_rotation_are_loaded(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "HELIX_METRICS_PATH=state/metrics.json\n"
        "HELIX_OPERATION_LOG_PATH=state/operations.jsonl\n"
        "HELIX_OPERATION_LOG_MAX_BYTES=4096\n"
        "HELIX_OPERATION_LOG_BACKUP_COUNT=4\n",
        encoding="utf-8",
    )

    settings = load_runtime_settings(dotenv, environ={})

    assert settings.metrics_path == tmp_path / "state" / "metrics.json"
    assert settings.operation_log_path == (
        tmp_path / "state" / "operations.jsonl"
    )
    assert settings.operation_log_max_bytes == 4096
    assert settings.operation_log_backup_count == 4


@pytest.mark.parametrize(
    "variable",
    ("HELIX_WRITE_PLAN_DB_PATH", "HELIX_WRITE_PLAN_KEY_PATH"),
)
def test_incomplete_write_plan_persistence_is_rejected(
    tmp_path: Path,
    variable: str,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{variable}=configured-alone\n", encoding="utf-8")

    with pytest.raises(RuntimeSettingsError, match="invalid runtime value"):
        load_runtime_settings(dotenv, environ={})


def test_process_audit_settings_override_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "HELIX_AUDIT_LOG_PATH=file-audit.jsonl\n",
        encoding="utf-8",
    )

    settings = load_runtime_settings(
        dotenv,
        environ={"HELIX_AUDIT_LOG_PATH": "process-audit.jsonl"},
    )

    assert settings.audit_log_path == tmp_path / "process-audit.jsonl"


@pytest.mark.parametrize(
    ("variable", "value"),
    (
        ("HELIX_AUDIT_LOG_MAX_BYTES", "1023"),
        ("HELIX_AUDIT_LOG_BACKUP_COUNT", "0"),
        ("HELIX_OPERATION_LOG_MAX_BYTES", "1023"),
        ("HELIX_OPERATION_LOG_BACKUP_COUNT", "0"),
    ),
)
def test_invalid_audit_limits_are_rejected_without_echoing_values(
    tmp_path: Path,
    variable: str,
    value: str,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{variable}={value}\n", encoding="utf-8")

    with pytest.raises(RuntimeSettingsError) as exc_info:
        load_runtime_settings(dotenv, environ={})

    assert value not in str(exc_info.value)


def test_default_config_path_is_stable(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("", encoding="utf-8")

    settings = load_runtime_settings(dotenv, environ={})

    assert settings.config_path == tmp_path / "config" / "helix.yaml"


def test_process_environment_overrides_dotenv_without_mutation(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "HELIX_CONFIG_PATH=file-config.yaml\n",
        encoding="utf-8",
    )
    environment = {
        "HELIX_CONFIG_PATH": "process-config.yaml",
    }

    settings = load_runtime_settings(dotenv, environ=environment)

    assert settings.config_path == tmp_path / "process-config.yaml"
    assert environment == {"HELIX_CONFIG_PATH": "process-config.yaml"}


def test_dotenv_is_not_interpolated_and_symlinks_are_rejected(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "ROOT=/runtime\nHELIX_CONFIG_PATH=${ROOT}/helix.yaml\n",
        encoding="utf-8",
    )
    settings = load_runtime_settings(dotenv, environ={})
    assert "${ROOT}" in str(settings.config_path)

    link = tmp_path / ".env-link"
    link.symlink_to(dotenv)
    with pytest.raises(RuntimeSettingsError, match="symbolic link"):
        load_runtime_settings(link, environ={})


def test_only_credential_variables_are_returned_and_process_wins(
    tmp_path: Path,
) -> None:
    from helix_mcp.config import load_secret_environment

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        'HELIX_CREDENTIAL_DEV={"username":"file"}\n'
        'UNSUPPORTED_CREDENTIAL_DEV={"username":"ignored"}\n'
        "UNRELATED_SECRET=do-not-load\n",
        encoding="utf-8",
    )

    values = load_secret_environment(
        dotenv,
        environ={
            "HELIX_CREDENTIAL_DEV": '{"username":"process"}',
        },
    )

    assert dict(values) == {
        "HELIX_CREDENTIAL_DEV": '{"username":"process"}',
    }
