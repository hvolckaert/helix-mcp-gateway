"""Tests for bounded, payload-free local metrics snapshots."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from helix_mcp.config import Environment
from helix_mcp.observability import MetricsRegistry


def test_metrics_are_aggregated_with_fixed_latency_buckets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "metrics.json"
    registry = MetricsRegistry(
        path,
        clock=lambda: datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )

    registry.record(
        tool="query_form",
        environment=Environment.DEV,
        outcome="success",
        duration_ms=25,
    )
    registry.record(
        tool="query_form",
        environment=Environment.DEV,
        outcome="success",
        duration_ms=75,
    )
    registry.record(
        tool="query_form",
        environment=Environment.DEV,
        outcome="error",
        duration_ms=5,
        error_code="FORM_QUERY_LIMIT_EXCEEDED",
    )

    snapshot = json.loads(path.read_text(encoding="utf-8"))
    success = next(
        item for item in snapshot["tools"] if item["outcome"] == "success"
    )
    assert success["count"] == 2
    assert success["duration_ms_sum"] == 100
    assert success["duration_ms_max"] == 75
    buckets = {
        item["le_ms"]: item["count"] for item in success["latency_buckets"]
    }
    assert buckets[10] == 0
    assert buckets[50] == 1
    assert buckets[100] == 2
    assert buckets[None] == 2
    assert snapshot["error_counts"] == {"FORM_QUERY_LIMIT_EXCEEDED": 1}
    assert "schema_version" in snapshot
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_unknown_names_are_collapsed_and_business_values_never_persist(
    tmp_path: Path,
) -> None:
    business_value = "private-form-and-entry-value"
    path = tmp_path / "metrics.json"
    registry = MetricsRegistry(path)

    registry.record(
        tool=business_value,
        environment=None,
        outcome="error",
        duration_ms=1,
        error_code="invalid private error detail",
    )

    contents = path.read_text(encoding="utf-8")
    snapshot = json.loads(contents)
    assert snapshot["tools"][0]["tool"] == "other"
    assert snapshot["tools"][0]["environment"] == "none"
    assert snapshot["error_counts"] == {"INTERNAL_ERROR": 1}
    assert business_value not in contents


def test_unavailable_metrics_sink_disables_itself_without_raising(
    tmp_path: Path,
    caplog,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    registry = MetricsRegistry(blocker / "metrics.json")

    registry.record(
        tool="list_targets",
        environment=None,
        outcome="success",
        duration_ms=1,
    )
    registry.record(
        tool="list_targets",
        environment=None,
        outcome="success",
        duration_ms=2,
    )

    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "metrics_file_unavailable"
    ]
    assert len(failures) == 1
    assert failures[0].error_code == "METRICS_FILE_WRITE_ERROR"
    assert str(tmp_path) not in failures[0].getMessage()
