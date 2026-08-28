"""Tests for structured logging and payload-free tool auditing."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import stat
from pathlib import Path

import pytest

from helix_mcp.config import Environment
from helix_mcp.observability import (
    JsonLogFormatter,
    MetricsRegistry,
    ToolAuditor,
    ToolExecutionError,
    configure_logging,
    public_error_code,
)
from helix_mcp.services.database import DatabaseQueryAliasRequiredError


def run(coroutine):
    return asyncio.run(coroutine)


class KnownFailure(RuntimeError):
    code = "KNOWN_FAILURE"


def test_success_audit_contains_only_safe_operation_metadata() -> None:
    stream = io.StringIO()
    logger = _logger(stream)
    times = iter((10.0, 10.025))
    auditor = ToolAuditor(
        logger=logger,
        clock=lambda: next(times),
        operation_id=lambda: "0123456789abcdef",
    )

    async def operation() -> str:
        return "private-result-value"

    result = run(
        auditor.execute(
            tool="query_form",
            environment=Environment.DEV,
            operation=operation,
        )
    )
    event = json.loads(stream.getvalue())

    assert result == "private-result-value"
    assert event == {
        "timestamp": event["timestamp"],
        "level": "INFO",
        "logger": logger.name,
        "message": "tool call completed",
        "event": "tool_call",
        "operation_id": "0123456789abcdef",
        "tool": "query_form",
        "operation_kind": "read",
        "environment": "dev",
        "outcome": "success",
        "duration_ms": 25,
    }
    assert "private-result-value" not in stream.getvalue()


def test_auditor_records_aggregate_metrics_without_payloads(
    tmp_path: Path,
) -> None:
    metrics_path = tmp_path / "metrics.json"
    metrics = MetricsRegistry(metrics_path)
    times = iter((10.0, 10.025))
    auditor = ToolAuditor(
        logger=_logger(io.StringIO()),
        clock=lambda: next(times),
        operation_id=lambda: "0123456789abcdef",
        metrics=metrics,
    )

    async def operation() -> str:
        return "private-result-value"

    assert (
        run(
            auditor.execute(
                tool="query_form",
                environment=Environment.DEV,
                operation=operation,
            )
        )
        == "private-result-value"
    )

    contents = metrics_path.read_text(encoding="utf-8")
    snapshot = json.loads(contents)
    assert snapshot["tools"][0]["count"] == 1
    assert snapshot["tools"][0]["duration_ms_sum"] == 25
    assert "private-result-value" not in contents


def test_nested_logs_receive_operation_correlation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    try:
        auditor = ToolAuditor(operation_id=lambda: "0123456789abcdef")

        async def operation() -> None:
            logging.getLogger("helix_mcp.test").info(
                "stable nested event",
                extra={"event": "application_ready"},
            )

        run(auditor.execute(tool="list_targets", operation=operation))
        events = [
            json.loads(line)
            for line in capsys.readouterr().err.splitlines()
            if line
        ]
        nested = next(
            item for item in events if item["message"] == "stable nested event"
        )
        assert nested["operation_id"] == "0123456789abcdef"
    finally:
        configure_logging("WARNING")


def test_operational_file_uses_a_closed_event_schema(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    configure_logging("INFO", operation_log_path=path)
    try:
        logging.getLogger("helix_mcp.lifecycle").info(
            "application ready",
            extra={"event": "application_ready"},
        )
        logging.getLogger("helix_mcp.test").warning(
            "password=must-not-persist",
            extra={"event": "unapproved_event"},
        )

        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert events == [
            {
                "timestamp": events[0]["timestamp"],
                "level": "INFO",
                "event": "application_ready",
            }
        ]
        assert "password=must-not-persist" not in path.read_text()
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        configure_logging("WARNING")


def test_failure_is_correlated_and_never_logs_exception_text() -> None:
    leaked = "password=private qualification and response"
    stream = io.StringIO()
    logger = _logger(stream)
    times = iter((20.0, 20.001))
    auditor = ToolAuditor(
        logger=logger,
        clock=lambda: next(times),
        operation_id=lambda: "fedcba9876543210",
    )

    async def operation() -> None:
        raise KnownFailure(leaked)

    with pytest.raises(ToolExecutionError) as exc_info:
        run(
            auditor.execute(
                tool="query_form",
                environment=Environment.DEV,
                operation=operation,
            )
        )

    event = json.loads(stream.getvalue())
    assert event["outcome"] == "error"
    assert event["error_code"] == "KNOWN_FAILURE"
    assert event["operation_id"] == exc_info.value.operation_id
    assert "code=KNOWN_FAILURE" in str(exc_info.value)
    assert leaked not in stream.getvalue()
    assert leaked not in str(exc_info.value)


def test_form_not_found_exposes_only_validated_name_suggestions() -> None:
    class MissingForm(RuntimeError):
        code = "FORM_NOT_FOUND"
        suggestions = (
            "Sample:CMDB:INT:FieldMapping",
            "Sample:Other:FieldMapping",
        )

    stream = io.StringIO()
    auditor = ToolAuditor(
        logger=_logger(stream),
        operation_id=lambda: "0123456789abcdef",
    )

    async def operation() -> None:
        raise MissingForm("private bridge response")

    with pytest.raises(ToolExecutionError) as exc_info:
        run(
            auditor.execute(
                tool="list_form_fields",
                environment=Environment.DEV,
                operation=operation,
            )
        )

    assert exc_info.value.suggestions == MissingForm.suggestions
    assert 'suggestions=["Sample:CMDB:INT:FieldMapping"' in str(exc_info.value)
    assert "private bridge response" not in str(exc_info.value)
    assert "Sample:CMDB:INT:FieldMapping" not in stream.getvalue()


def test_sql_alias_error_exposes_only_a_static_actionable_suggestion() -> None:
    stream = io.StringIO()
    auditor = ToolAuditor(
        logger=_logger(stream),
        operation_id=lambda: "0123456789abcdef",
    )

    async def operation() -> None:
        raise DatabaseQueryAliasRequiredError("private SQL validation detail")

    with pytest.raises(ToolExecutionError) as exc_info:
        run(
            auditor.execute(
                tool="plan_sql_query",
                environment=Environment.QA,
                operation=operation,
            )
        )

    assert exc_info.value.error_code == "DATABASE_QUERY_ALIAS_REQUIRED"
    assert exc_info.value.suggestions == (
        "Add an explicit AS alias to every selected expression.",
    )
    assert "private SQL validation detail" not in str(exc_info.value)
    assert "suggestions=" not in stream.getvalue()


def test_declared_database_diagnostics_are_public_and_audited() -> None:
    class QueryTimeout(RuntimeError):
        code = "ARAPI_SQL_QUERY_ERROR"

        def __init__(self, message: str) -> None:
            self.public_details = {
                "category": "query_timeout",
                "sqlstate": "57014",
                "message": (
                    "ARAPI SQL request reached the configured timeout"
                ),
                "timeout_seconds": 30,
            }
            super().__init__(message)

    stream = io.StringIO()
    auditor = ToolAuditor(
        logger=_logger(stream),
        operation_id=lambda: "0123456789abcdef",
    )

    async def operation() -> None:
        raise QueryTimeout("private database message")

    with pytest.raises(ToolExecutionError) as exc_info:
        run(
            auditor.execute(
                tool="execute_sql_query",
                environment=Environment.DEV,
                operation=operation,
            )
        )

    event = json.loads(stream.getvalue())
    assert event["error_category"] == "query_timeout"
    assert event["sqlstate"] == "57014"
    assert exc_info.value.details == {
        "category": "query_timeout",
        "sqlstate": "57014",
        "message": "ARAPI SQL request reached the configured timeout",
        "timeout_seconds": 30,
    }
    assert "private database message" not in str(exc_info.value)


def test_form_not_found_rejects_unsafe_suggestions() -> None:
    class MissingForm(RuntimeError):
        code = "FORM_NOT_FOUND"
        suggestions = ("safe", "private\nvalue")

    auditor = ToolAuditor(operation_id=lambda: "0123456789abcdef")

    async def operation() -> None:
        raise MissingForm("private")

    with pytest.raises(ToolExecutionError) as exc_info:
        run(
            auditor.execute(
                tool="list_form_fields",
                environment=Environment.DEV,
                operation=operation,
            )
        )

    assert exc_info.value.suggestions == ()
    assert "suggestions=" not in str(exc_info.value)


def test_undeclared_or_invalid_error_codes_become_internal_error() -> None:
    class MissingCode(RuntimeError):
        pass

    class InvalidCode(RuntimeError):
        code = "invalid detail"

    assert public_error_code(MissingCode()) == "INTERNAL_ERROR"
    assert public_error_code(InvalidCode()) == "INTERNAL_ERROR"


def test_deprecated_instance_and_operation_values_are_not_logged() -> None:
    leaked = "password-private-operation-value"
    stream = io.StringIO()
    auditor = ToolAuditor(
        logger=_logger(stream),
        operation_id=lambda: "0123456789abcdef",
    )

    async def operation() -> None:
        raise KnownFailure("safe")

    with pytest.raises(ToolExecutionError):
        run(
            auditor.execute(
                tool="health_check",
                environment=Environment.DEV,
                operation=operation,
            )
        )

    assert leaked not in stream.getvalue()
    assert "safe" not in stream.getvalue()
    assert "instance" not in json.loads(stream.getvalue())


@pytest.mark.parametrize(
    ("tool", "expected"),
    (
        ("query_form", "read"),
        ("get_write_plan", "read"),
        ("plan_create_entry", "plan"),
        ("cancel_write_plan", "plan"),
        ("apply_update_entry", "apply"),
    ),
)
def test_operation_kind_is_derived_from_the_closed_tool_name(
    tool: str,
    expected: str,
) -> None:
    stream = io.StringIO()
    auditor = ToolAuditor(
        logger=_logger(stream),
        operation_id=lambda: "0123456789abcdef",
    )

    async def operation() -> None:
        return None

    run(auditor.execute(tool=tool, operation=operation))

    assert json.loads(stream.getvalue())["operation_kind"] == expected


def test_optional_audit_file_contains_only_audit_events(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "private" / "audit.jsonl"
    configure_logging("INFO", audit_log_path=audit_path)
    try:
        auditor = ToolAuditor(operation_id=lambda: "0123456789abcdef")

        async def operation() -> str:
            return "private-result"

        assert (
            run(
                auditor.execute(
                    tool="query_form",
                    environment=Environment.DEV,
                    operation=operation,
                )
            )
            == "private-result"
        )
        logging.getLogger("helix_mcp.lifecycle").info("not an audit event")
        logging.getLogger("helix_mcp.audit").info(
            "password=must-not-be-persisted",
            extra={
                "event": "tool_call",
                "operation_id": "fedcba9876543210",
                "tool": "query_form",
                "operation_kind": "read",
                "environment": "dev",
                "outcome": "success",
                "duration_ms": 1,
            },
        )

        contents = audit_path.read_text(encoding="utf-8")
        events = [json.loads(line) for line in contents.splitlines()]
        event = events[0]
        assert event["event"] == "tool_call"
        assert event["operation_kind"] == "read"
        assert all("message" not in item for item in events)
        assert "private-result" not in contents
        assert "password=must-not-be-persisted" not in contents
        assert "not an audit event" not in contents
        if os.name == "posix":
            assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600
    finally:
        configure_logging("WARNING")


def test_audit_file_rotates_and_keeps_owner_only_permissions(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "audit.jsonl"
    configure_logging(
        "INFO",
        audit_log_path=audit_path,
        audit_log_max_bytes=300,
        audit_log_backup_count=2,
    )
    try:
        logger = logging.getLogger("helix_mcp.audit")
        for index in range(10):
            logger.info(
                "tool call completed",
                extra={
                    "event": "tool_call",
                    "operation_id": f"{index:016x}",
                    "tool": "query_form",
                    "operation_kind": "read",
                    "environment": "dev",
                    "outcome": "success",
                    "duration_ms": index,
                },
            )

        rotated = tuple(tmp_path.glob("audit.jsonl*"))
        assert audit_path.with_suffix(".jsonl.1") in rotated
        assert len(rotated) <= 3
        if os.name == "posix":
            assert all(
                stat.S_IMODE(path.stat().st_mode) == 0o600 for path in rotated
            )
    finally:
        configure_logging("WARNING")


def test_unavailable_audit_file_falls_back_without_path_or_exception(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")
    audit_path = blocker / "audit.jsonl"

    configure_logging("INFO", audit_log_path=audit_path)
    try:
        event = json.loads(capsys.readouterr().err)
        assert event["event"] == "audit_file_unavailable"
        assert event["error_code"] == "AUDIT_FILE_OPEN_ERROR"
        assert str(audit_path) not in json.dumps(event)
    finally:
        configure_logging("WARNING")


def test_symbolic_link_audit_file_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("unchanged", encoding="utf-8")
    link = tmp_path / "audit.jsonl"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    configure_logging("INFO", audit_log_path=link)
    try:
        event = json.loads(capsys.readouterr().err)
        assert event["error_code"] == "AUDIT_FILE_OPEN_ERROR"
        assert target.read_text(encoding="utf-8") == "unchanged"
    finally:
        configure_logging("WARNING")


def test_runtime_audit_write_failure_is_reported_once_and_disabled(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch,
) -> None:
    audit_path = tmp_path / "audit.jsonl"
    configure_logging(
        "INFO",
        audit_log_path=audit_path,
        audit_log_max_bytes=1,
    )
    logger = logging.getLogger("helix_mcp.audit")
    managed = next(
        handler
        for handler in logger.handlers
        if getattr(handler, "_helix_mcp_managed_audit_handler", False)
    )

    def fail_rollover() -> None:
        raise OSError("private filesystem detail")

    monkeypatch.setattr(managed, "doRollover", fail_rollover)
    try:
        safe_fields = {
            "event": "tool_call",
            "operation_id": "0123456789abcdef",
            "tool": "query_form",
            "operation_kind": "read",
            "environment": "dev",
            "outcome": "success",
            "duration_ms": 1,
        }
        logger.info("tool call completed", extra=safe_fields)
        logger.info("tool call completed", extra=safe_fields)

        events = [
            json.loads(line) for line in capsys.readouterr().err.splitlines()
        ]
        failures = [
            event
            for event in events
            if event.get("event") == "audit_file_unavailable"
        ]
        assert len(failures) == 1
        assert failures[0]["error_code"] == "AUDIT_FILE_WRITE_ERROR"
        assert "private filesystem detail" not in json.dumps(events)
    finally:
        configure_logging("WARNING")


def test_runtime_operation_write_failure_is_reported_once_and_disabled(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch,
) -> None:
    operation_path = tmp_path / "operations.jsonl"
    configure_logging(
        "INFO",
        operation_log_path=operation_path,
        operation_log_max_bytes=1,
    )
    root = logging.getLogger()
    managed = next(
        handler
        for handler in root.handlers
        if getattr(handler, "_helix_mcp_managed_operation_handler", False)
    )

    def fail_rollover() -> None:
        raise OSError("private filesystem detail")

    monkeypatch.setattr(managed, "doRollover", fail_rollover)
    try:
        logger = logging.getLogger("helix_mcp.lifecycle")
        logger.info("application ready", extra={"event": "application_ready"})
        logger.info("application ready", extra={"event": "application_ready"})

        events = [
            json.loads(line) for line in capsys.readouterr().err.splitlines()
        ]
        failures = [
            event
            for event in events
            if event.get("event") == "operation_file_unavailable"
        ]
        assert len(failures) == 1
        assert failures[0]["error_code"] == "OPERATION_FILE_WRITE_ERROR"
        assert "private filesystem detail" not in json.dumps(events)
    finally:
        configure_logging("WARNING")


def _logger(stream: io.StringIO) -> logging.Logger:
    logger = logging.getLogger(f"test.audit.{id(stream)}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    return logger
