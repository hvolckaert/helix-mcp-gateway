"""Safe tool-call auditing without arguments, payloads or exception messages."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeVar

from helix_mcp.config import Environment
from helix_mcp.observability.context import operation_context
from helix_mcp.observability.errors import ToolExecutionError
from helix_mcp.observability.metrics import MetricOutcome, MetricsRegistry

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_STATIC_PUBLIC_SUGGESTIONS = {
    "DATABASE_QUERY_ALIAS_REQUIRED": (
        "Add an explicit AS alias to every selected expression.",
    ),
    "DATABASE_QUERY_ALIAS_INVALID": (
        "Use a unique alias for every selected expression; aliases may "
        "contain only letters, numbers, and underscores.",
    ),
    "DATABASE_QUERY_WILDCARD_NOT_ALLOWED": (
        "Replace SELECT wildcards with explicit columns; COUNT(*) is allowed.",
    ),
}
ResultT = TypeVar("ResultT")


class ToolAuditor:
    """Record sanitized outcomes and normalize public tool failures."""

    __slots__ = ("_clock", "_logger", "_metrics", "_operation_id")

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
        operation_id: Callable[[], str] | None = None,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger("helix_mcp.audit")
        self._clock = clock
        self._operation_id = operation_id or _new_operation_id
        self._metrics = metrics

    async def execute(
        self,
        *,
        tool: str,
        operation: Callable[[], Awaitable[ResultT]],
        environment: Environment | None = None,
    ) -> ResultT:
        operation_id = self._operation_id()
        started_at = self._clock()
        fields = {
            "event": "tool_call",
            "operation_id": operation_id,
            "tool": tool,
            "operation_kind": _operation_kind(tool),
            "environment": (
                environment.value if environment is not None else None
            ),
        }
        with operation_context(operation_id):
            try:
                result = await operation()
            except asyncio.CancelledError:
                duration_ms = _duration_ms(started_at, self._clock())
                self._logger.info(
                    "tool call cancelled",
                    extra={
                        **fields,
                        "outcome": "cancelled",
                        "duration_ms": duration_ms,
                    },
                )
                self._record_metric(
                    tool=tool,
                    environment=environment,
                    outcome="cancelled",
                    duration_ms=duration_ms,
                )
                raise
            except Exception as exc:
                duration_ms = _duration_ms(started_at, self._clock())
                error_code = public_error_code(exc)
                error_details = public_error_details(exc)
                self._logger.warning(
                    "tool call failed",
                    extra={
                        **fields,
                        "outcome": "error",
                        "duration_ms": duration_ms,
                        "error_code": error_code,
                        "error_category": error_details.get("category"),
                        "sqlstate": error_details.get("sqlstate"),
                    },
                )
                self._record_metric(
                    tool=tool,
                    environment=environment,
                    outcome="error",
                    duration_ms=duration_ms,
                    error_code=error_code,
                )
                raise ToolExecutionError(
                    error_code=error_code,
                    operation_id=operation_id,
                    suggestions=public_error_suggestions(exc),
                    details=error_details,
                ) from None

            duration_ms = _duration_ms(started_at, self._clock())
            self._logger.info(
                "tool call completed",
                extra={
                    **fields,
                    "outcome": "success",
                    "duration_ms": duration_ms,
                },
            )
            self._record_metric(
                tool=tool,
                environment=environment,
                outcome="success",
                duration_ms=duration_ms,
            )
            return result

    def _record_metric(
        self,
        *,
        tool: str,
        environment: Environment | None,
        outcome: MetricOutcome,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None:
        if self._metrics is not None:
            self._metrics.record(
                tool=tool,
                environment=environment,
                outcome=outcome,
                duration_ms=duration_ms,
                error_code=error_code,
            )


def public_error_code(error: Exception) -> str:
    """Return only an explicitly declared, stable error code."""

    code = getattr(error, "code", None)
    if isinstance(code, str) and _CODE_PATTERN.fullmatch(code):
        return code
    return "INTERNAL_ERROR"


def public_error_suggestions(error: Exception) -> tuple[str, ...]:
    """Return only allowlisted static hints or bounded form-name hints."""

    code = public_error_code(error)
    static = _STATIC_PUBLIC_SUGGESTIONS.get(code)
    if static is not None:
        return static
    if code != "FORM_NOT_FOUND":
        return ()
    suggestions = getattr(error, "suggestions", ())
    if not isinstance(suggestions, tuple) or len(suggestions) > 3:
        return ()
    if any(
        not isinstance(item, str)
        or not 1 <= len(item) <= 255
        or item != item.strip()
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in item
        )
        for item in suggestions
    ):
        return ()
    return suggestions


def public_error_details(error: Exception) -> dict[str, str | int | bool]:
    """Return only explicitly published, bounded scalar diagnostics."""

    raw = getattr(error, "public_details", None)
    if not isinstance(raw, Mapping) or len(raw) > 8:
        return {}
    allowed_keys = {"category", "sqlstate", "message", "timeout_seconds"}
    details: dict[str, str | int | bool] = {}
    for key, value in raw.items():
        if key not in allowed_keys or not isinstance(value, (str, int, bool)):
            return {}
        if isinstance(value, str) and (
            not 1 <= len(value) <= 255
            or any(ord(character) < 0x20 for character in value)
        ):
            return {}
        details[key] = value
    return details


def _duration_ms(started_at: float, finished_at: float) -> int:
    return max(0, round((finished_at - started_at) * 1_000))


def _new_operation_id() -> str:
    return secrets.token_hex(8)


def _operation_kind(tool: str) -> str:
    if tool.startswith("apply_"):
        return "apply"
    if tool.startswith("plan_") or tool in {
        "cancel_write_plan",
        "cancel_sql_query_plan",
    }:
        return "plan"
    return "read"
