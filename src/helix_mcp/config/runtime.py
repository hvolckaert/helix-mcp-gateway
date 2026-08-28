"""Runtime settings loaded from process environment and optional ``.env``."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

_CONFIG_PATH_VARIABLE = "HELIX_CONFIG_PATH"
_ARAPI_BRIDGE_JAR_VARIABLE = "HELIX_ARAPI_BRIDGE_JAR_PATH"
_ARAPI_LIB_DIRECTORY_VARIABLE = "HELIX_ARAPI_LIB_DIR"
_AUDIT_LOG_PATH_VARIABLE = "HELIX_AUDIT_LOG_PATH"
_AUDIT_LOG_MAX_BYTES_VARIABLE = "HELIX_AUDIT_LOG_MAX_BYTES"
_AUDIT_LOG_BACKUP_COUNT_VARIABLE = "HELIX_AUDIT_LOG_BACKUP_COUNT"
_METRICS_PATH_VARIABLE = "HELIX_METRICS_PATH"
_OPERATION_LOG_PATH_VARIABLE = "HELIX_OPERATION_LOG_PATH"
_OPERATION_LOG_MAX_BYTES_VARIABLE = "HELIX_OPERATION_LOG_MAX_BYTES"
_OPERATION_LOG_BACKUP_COUNT_VARIABLE = "HELIX_OPERATION_LOG_BACKUP_COUNT"
_WRITE_PLAN_DATABASE_VARIABLE = "HELIX_WRITE_PLAN_DB_PATH"
_WRITE_PLAN_KEY_VARIABLE = "HELIX_WRITE_PLAN_KEY_PATH"
_CREDENTIAL_VARIABLE_PREFIXES = ("HELIX_CREDENTIAL_",)
_KNOWN_VARIABLES = frozenset(
    {
        _CONFIG_PATH_VARIABLE,
        _ARAPI_BRIDGE_JAR_VARIABLE,
        _ARAPI_LIB_DIRECTORY_VARIABLE,
        _AUDIT_LOG_PATH_VARIABLE,
        _AUDIT_LOG_MAX_BYTES_VARIABLE,
        _AUDIT_LOG_BACKUP_COUNT_VARIABLE,
        _METRICS_PATH_VARIABLE,
        _OPERATION_LOG_PATH_VARIABLE,
        _OPERATION_LOG_MAX_BYTES_VARIABLE,
        _OPERATION_LOG_BACKUP_COUNT_VARIABLE,
        _WRITE_PLAN_DATABASE_VARIABLE,
        _WRITE_PLAN_KEY_VARIABLE,
    }
)
_MAX_DOTENV_BYTES = 65_536
_DEFAULT_CONFIG_PATH = Path("config/helix.yaml")


class RuntimeSettings(BaseModel):
    """Non-secret paths for the fixed Helix environments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_path: Path
    arapi_bridge_jar_path: Path | None = None
    arapi_lib_dir: Path | None = None
    audit_log_path: Path | None = None
    audit_log_max_bytes: int = Field(
        default=10_485_760,
        ge=1_024,
        le=1_073_741_824,
    )
    audit_log_backup_count: int = Field(default=5, ge=1, le=100)
    metrics_path: Path | None = None
    operation_log_path: Path | None = None
    operation_log_max_bytes: int = Field(
        default=10_485_760,
        ge=1_024,
        le=1_073_741_824,
    )
    operation_log_backup_count: int = Field(default=5, ge=1, le=100)
    write_plan_db_path: Path | None = None
    write_plan_key_path: Path | None = None

    @model_validator(mode="after")
    def require_complete_write_plan_storage(self) -> RuntimeSettings:
        if (self.write_plan_db_path is None) != (
            self.write_plan_key_path is None
        ):
            raise ValueError(
                "write-plan database and key paths must be configured together"
            )
        return self


class RuntimeSettingsError(ValueError):
    """Sanitized runtime configuration error."""

    code = "RUNTIME_SETTINGS_ERROR"


def load_secret_environment(
    dotenv_path: str | Path = ".env",
    *,
    environ: Mapping[str, str] | None = None,
) -> Mapping[str, str]:
    """Return only credential variables without mutating ``os.environ``."""

    file_values = _read_dotenv(Path(dotenv_path))
    environment_values = os.environ if environ is None else environ
    keys = {
        key
        for key in {*file_values, *environment_values}
        if key.startswith(_CREDENTIAL_VARIABLE_PREFIXES)
    }
    return MappingProxyType(
        {
            key: value
            for key in sorted(keys)
            if (value := environment_values.get(key, file_values.get(key)))
            is not None
        }
    )


def load_runtime_settings(
    dotenv_path: str | Path = ".env",
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeSettings:
    """Load fixed-environment settings without mutating ``os.environ``."""

    source = Path(dotenv_path)
    file_values = _read_dotenv(source)
    environment_values = os.environ if environ is None else environ

    values = {
        variable: environment_values.get(variable, file_values.get(variable))
        for variable in _KNOWN_VARIABLES
    }
    base_directory = source.absolute().parent
    config_value = values[_CONFIG_PATH_VARIABLE]
    try:
        bridge_jar_value = values[_ARAPI_BRIDGE_JAR_VARIABLE]
        arapi_lib_value = values[_ARAPI_LIB_DIRECTORY_VARIABLE]
        audit_log_value = _optional_text(values[_AUDIT_LOG_PATH_VARIABLE])
        metrics_path_value = _optional_text(values[_METRICS_PATH_VARIABLE])
        operation_log_value = _optional_text(
            values[_OPERATION_LOG_PATH_VARIABLE]
        )
        write_plan_db_value = _optional_text(
            values[_WRITE_PLAN_DATABASE_VARIABLE]
        )
        write_plan_key_value = _optional_text(values[_WRITE_PLAN_KEY_VARIABLE])
        return RuntimeSettings.model_validate(
            {
                "config_path": _resolve_runtime_path(
                    config_value or str(_DEFAULT_CONFIG_PATH),
                    base_directory,
                ),
                "arapi_bridge_jar_path": (
                    _resolve_runtime_path(
                        bridge_jar_value,
                        base_directory,
                    )
                    if bridge_jar_value
                    else None
                ),
                "arapi_lib_dir": (
                    _resolve_runtime_path(
                        arapi_lib_value,
                        base_directory,
                    )
                    if arapi_lib_value
                    else None
                ),
                "audit_log_path": (
                    _resolve_runtime_path(audit_log_value, base_directory)
                    if audit_log_value
                    else None
                ),
                "audit_log_max_bytes": (
                    values[_AUDIT_LOG_MAX_BYTES_VARIABLE] or 10_485_760
                ),
                "audit_log_backup_count": (
                    values[_AUDIT_LOG_BACKUP_COUNT_VARIABLE] or 5
                ),
                "metrics_path": (
                    _resolve_runtime_path(metrics_path_value, base_directory)
                    if metrics_path_value
                    else None
                ),
                "operation_log_path": (
                    _resolve_runtime_path(operation_log_value, base_directory)
                    if operation_log_value
                    else None
                ),
                "operation_log_max_bytes": (
                    values[_OPERATION_LOG_MAX_BYTES_VARIABLE] or 10_485_760
                ),
                "operation_log_backup_count": (
                    values[_OPERATION_LOG_BACKUP_COUNT_VARIABLE] or 5
                ),
                "write_plan_db_path": (
                    _resolve_runtime_path(
                        write_plan_db_value,
                        base_directory,
                    )
                    if write_plan_db_value
                    else None
                ),
                "write_plan_key_path": (
                    _resolve_runtime_path(
                        write_plan_key_value,
                        base_directory,
                    )
                    if write_plan_key_value
                    else None
                ),
            }
        )
    except ValidationError:
        raise RuntimeSettingsError(
            "runtime settings contain an invalid runtime value"
        ) from None


def _read_dotenv(path: Path) -> Mapping[str, str | None]:
    if not path.exists():
        return {}
    if path.is_symlink():
        raise RuntimeSettingsError(".env cannot be a symbolic link")
    if not path.is_file():
        raise RuntimeSettingsError(".env source is not a regular file")
    try:
        if path.stat().st_size > _MAX_DOTENV_BYTES:
            raise RuntimeSettingsError(
                ".env exceeds the configured size limit"
            )
        return dotenv_values(path, interpolate=False, encoding="utf-8")
    except (OSError, UnicodeError):
        raise RuntimeSettingsError(".env could not be read as UTF-8") from None


def _resolve_runtime_path(value: str, base_directory: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_directory / path


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
