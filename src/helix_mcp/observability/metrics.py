"""Bounded local metrics snapshots without arguments or business values."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from helix_mcp.config import Environment

MetricOutcome = Literal["success", "error", "cancelled"]
_KNOWN_TOOLS: Final = frozenset(
    {
        "apply_create_entry",
        "apply_update_entry",
        "cancel_write_plan",
        "cancel_sql_query_plan",
        "describe_database_object",
        "get_entry",
        "get_write_plan",
        "get_sql_query_plan",
        "health_check",
        "list_database_columns",
        "list_database_objects",
        "list_form_fields",
        "list_forms",
        "list_targets",
        "plan_create_entry",
        "plan_sql_query",
        "plan_update_entry",
        "query_form",
        "execute_sql_query",
    }
)
_LATENCY_BUCKETS_MS: Final = (
    10,
    50,
    100,
    250,
    500,
    1_000,
    2_500,
    5_000,
    10_000,
    30_000,
)
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_ERROR_CODES = 64
_SCHEMA_VERSION = 1


class _Metric:
    __slots__ = ("buckets", "count", "duration_ms_max", "duration_ms_sum")

    def __init__(self) -> None:
        self.count = 0
        self.duration_ms_sum = 0
        self.duration_ms_max = 0
        self.buckets = [0] * (len(_LATENCY_BUCKETS_MS) + 1)

    def record(self, duration_ms: int) -> None:
        self.count += 1
        self.duration_ms_sum += duration_ms
        self.duration_ms_max = max(self.duration_ms_max, duration_ms)
        for index, boundary in enumerate(_LATENCY_BUCKETS_MS):
            if duration_ms <= boundary:
                self.buckets[index] += 1
        self.buckets[-1] += 1


class MetricsRegistry:
    """Aggregate fixed-cardinality tool metrics and persist one safe snapshot."""

    __slots__ = (
        "_clock",
        "_disabled",
        "_errors",
        "_lock",
        "_metrics",
        "_path",
        "_started_at",
    )

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = None if path is None else path.absolute()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._started_at = self._clock()
        self._metrics: dict[tuple[str, str, MetricOutcome], _Metric] = {}
        self._errors: dict[str, int] = {}
        self._lock = threading.Lock()
        self._disabled = False

    def record(
        self,
        *,
        tool: str,
        environment: Environment | None,
        outcome: MetricOutcome,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None:
        """Record one closed-schema observation; failures never affect tools."""

        safe_tool = tool if tool in _KNOWN_TOOLS else "other"
        safe_environment = "none" if environment is None else environment.value
        safe_duration = max(0, min(duration_ms, 86_400_000))
        with self._lock:
            metric = self._metrics.setdefault(
                (safe_tool, safe_environment, outcome),
                _Metric(),
            )
            metric.record(safe_duration)
            if outcome == "error" and error_code is not None:
                self._record_error(error_code)
            if self._path is not None and not self._disabled:
                try:
                    self._write_snapshot(self._snapshot_locked())
                except OSError:
                    self._disabled = True
                    logging.getLogger("helix_mcp.metrics_sink").warning(
                        "metrics file unavailable",
                        extra={
                            "event": "metrics_file_unavailable",
                            "error_code": "METRICS_FILE_WRITE_ERROR",
                        },
                    )

    def snapshot(self) -> dict[str, object]:
        """Return a copy of the aggregate schema for tests and diagnostics."""

        with self._lock:
            return self._snapshot_locked()

    def _record_error(self, error_code: str) -> None:
        safe_code = (
            error_code
            if _ERROR_CODE_PATTERN.fullmatch(error_code)
            else "INTERNAL_ERROR"
        )
        if (
            safe_code not in self._errors
            and len(self._errors) >= _MAX_ERROR_CODES
        ):
            safe_code = "OTHER_ERROR"
        self._errors[safe_code] = self._errors.get(safe_code, 0) + 1

    def _snapshot_locked(self) -> dict[str, object]:
        updated_at = self._clock()
        tools: list[dict[str, object]] = []
        for (tool, environment, outcome), metric in sorted(
            self._metrics.items()
        ):
            buckets: list[dict[str, int | None]] = [
                {"le_ms": boundary, "count": metric.buckets[index]}
                for index, boundary in enumerate(_LATENCY_BUCKETS_MS)
            ]
            buckets.append({"le_ms": None, "count": metric.buckets[-1]})
            tools.append(
                {
                    "tool": tool,
                    "environment": environment,
                    "outcome": outcome,
                    "count": metric.count,
                    "duration_ms_sum": metric.duration_ms_sum,
                    "duration_ms_max": metric.duration_ms_max,
                    "latency_buckets": buckets,
                }
            )
        return {
            "schema_version": _SCHEMA_VERSION,
            "process_started_at": _timestamp(self._started_at),
            "updated_at": _timestamp(updated_at),
            "tools": tools,
            "error_counts": dict(sorted(self._errors.items())),
        }

    def _write_snapshot(self, snapshot: dict[str, object]) -> None:
        assert self._path is not None
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise OSError("metrics path cannot be a symbolic link")
        temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(
                descriptor, "w", encoding="utf-8", newline="\n"
            ) as stream:
                descriptor = None
                json.dump(
                    snapshot, stream, ensure_ascii=False, separators=(",", ":")
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name == "posix":
                os.chmod(path, 0o600)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _timestamp(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
