"""Structured process logging configured for MCP stdio safety."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from helix_mcp.observability.context import OperationContextFilter

_EVENT_FIELDS = (
    "event",
    "operation_id",
    "tool",
    "operation_kind",
    "environment",
    "outcome",
    "duration_ms",
    "error_code",
    "error_category",
    "sqlstate",
)
_NOISY_LOGGERS = ("httpx", "httpcore")
_AUDIT_LOGGER = "helix_mcp.audit"
_AUDIT_SINK_LOGGER = "helix_mcp.audit_sink"
_MANAGED_HANDLER_MARKER = "_helix_mcp_managed_audit_handler"
_MANAGED_OPERATION_HANDLER_MARKER = "_helix_mcp_managed_operation_handler"
_OPERATION_SINK_LOGGER = "helix_mcp.operation_sink"
_OPERATION_EVENTS = frozenset(
    {
        "application_ready",
        "application_shutdown_failed",
        "application_starting",
        "application_startup_failed",
        "application_stopped",
        "application_stopping",
        "audit_file_unavailable",
        "metrics_file_unavailable",
        "operation_file_unavailable",
        "server_startup_failed",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Emit one bounded JSON object without exception text or tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=UTC,
            )
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _EVENT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )


class AuditJsonLogFormatter(logging.Formatter):
    """Persist only the closed audit schema, without free-form messages."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=UTC,
            )
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
        }
        for field in _EVENT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )


class ToolAuditFilter(logging.Filter):
    """Accept only final tool-call events from the dedicated logger."""

    def filter(self, record: logging.LogRecord) -> bool:
        return (
            record.name == _AUDIT_LOGGER
            and getattr(record, "event", None) == "tool_call"
        )


class OperationalEventFilter(logging.Filter):
    """Persist only explicitly approved process-level event names."""

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event", None) in _OPERATION_EVENTS


class SafeRotatingAuditHandler(RotatingFileHandler):
    """Rotate an owner-only audit file and fail closed without tracebacks."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int,
        backup_count: int,
    ) -> None:
        self._audit_failed = False
        super().__init__(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
        try:
            self._restrict_permissions()
        except OSError:
            self.close()
            raise

    def emit(self, record: logging.LogRecord) -> None:
        if self._audit_failed:
            return
        super().emit(record)

    def doRollover(self) -> None:
        super().doRollover()
        self._restrict_permissions()

    def handleError(self, record: logging.LogRecord) -> None:
        if self._audit_failed:
            return
        self._audit_failed = True
        logging.getLogger(_AUDIT_SINK_LOGGER).warning(
            "audit file write disabled",
            extra={
                "event": "audit_file_unavailable",
                "error_code": "AUDIT_FILE_WRITE_ERROR",
            },
        )

    def _restrict_permissions(self) -> None:
        if os.name == "posix":
            os.chmod(self.baseFilename, 0o600)


class SafeRotatingOperationalHandler(RotatingFileHandler):
    """Owner-only operational sink that disables itself after one failure."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int,
        backup_count: int,
    ) -> None:
        self._operation_failed = False
        super().__init__(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
        try:
            self._restrict_permissions()
        except OSError:
            self.close()
            raise

    def emit(self, record: logging.LogRecord) -> None:
        if not self._operation_failed:
            super().emit(record)

    def doRollover(self) -> None:
        super().doRollover()
        self._restrict_permissions()

    def handleError(self, record: logging.LogRecord) -> None:
        if self._operation_failed:
            return
        self._operation_failed = True
        logging.getLogger(_OPERATION_SINK_LOGGER).warning(
            "operation file write disabled",
            extra={
                "event": "operation_file_unavailable",
                "error_code": "OPERATION_FILE_WRITE_ERROR",
            },
        )

    def _restrict_permissions(self) -> None:
        if os.name == "posix":
            os.chmod(self.baseFilename, 0o600)


def configure_logging(
    level: str,
    *,
    audit_log_path: Path | None = None,
    audit_log_max_bytes: int = 10_485_760,
    audit_log_backup_count: int = 5,
    operation_log_path: Path | None = None,
    operation_log_max_bytes: int = 10_485_760,
    operation_log_backup_count: int = 5,
) -> None:
    """Configure stderr JSON logging and an optional rotated audit sink."""

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(OperationContextFilter())
    root = logging.getLogger()
    _remove_managed_operation_handlers()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.captureWarnings(True)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    _remove_managed_audit_handlers()
    if audit_log_path is not None:
        _configure_audit_file(
            audit_log_path,
            max_bytes=audit_log_max_bytes,
            backup_count=audit_log_backup_count,
        )
    if operation_log_path is not None:
        _configure_operation_file(
            operation_log_path,
            max_bytes=operation_log_max_bytes,
            backup_count=operation_log_backup_count,
        )


def _configure_audit_file(
    path: Path,
    *,
    max_bytes: int,
    backup_count: int,
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise OSError("audit log path cannot be a symbolic link")
        handler = SafeRotatingAuditHandler(
            path,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
    except OSError:
        logging.getLogger(_AUDIT_SINK_LOGGER).warning(
            "audit file unavailable; stderr audit remains active",
            extra={
                "event": "audit_file_unavailable",
                "error_code": "AUDIT_FILE_OPEN_ERROR",
            },
        )
        return
    handler.addFilter(ToolAuditFilter())
    handler.setFormatter(AuditJsonLogFormatter())
    setattr(handler, _MANAGED_HANDLER_MARKER, True)
    logging.getLogger(_AUDIT_LOGGER).addHandler(handler)


def _remove_managed_audit_handlers() -> None:
    logger = logging.getLogger(_AUDIT_LOGGER)
    for handler in tuple(logger.handlers):
        if getattr(handler, _MANAGED_HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()


def _configure_operation_file(
    path: Path,
    *,
    max_bytes: int,
    backup_count: int,
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise OSError("operation log path cannot be a symbolic link")
        handler = SafeRotatingOperationalHandler(
            path,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
    except OSError:
        logging.getLogger(_OPERATION_SINK_LOGGER).warning(
            "operation file unavailable",
            extra={
                "event": "operation_file_unavailable",
                "error_code": "OPERATION_FILE_OPEN_ERROR",
            },
        )
        return
    handler.addFilter(OperationalEventFilter())
    handler.addFilter(OperationContextFilter())
    handler.setFormatter(AuditJsonLogFormatter())
    setattr(handler, _MANAGED_OPERATION_HANDLER_MARKER, True)
    logging.getLogger().addHandler(handler)


def _remove_managed_operation_handlers() -> None:
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        if getattr(handler, _MANAGED_OPERATION_HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()
