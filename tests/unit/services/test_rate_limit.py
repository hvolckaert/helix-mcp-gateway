"""Tests for process-safe shared sliding-window rate limits."""

from __future__ import annotations

import asyncio
from pathlib import Path

from helix_mcp.config import Environment, TargetKey
from helix_mcp.services.rate_limit import SlidingWindowCounter

TARGET = TargetKey(environment=Environment.DEV)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def run(coroutine):
    return asyncio.run(coroutine)


def test_persistent_limit_is_shared_between_instances(tmp_path: Path) -> None:
    clock = Clock()
    database = tmp_path / "write-plans.sqlite3"
    first = SlidingWindowCounter(
        "form_read",
        database_path=database,
        time_source=clock,
    )
    second = SlidingWindowCounter(
        "form_read",
        database_path=database,
        time_source=clock,
    )

    assert run(first.acquire(TARGET, 2)) is True
    assert run(second.acquire(TARGET, 2)) is True
    assert run(first.acquire(TARGET, 2)) is False

    clock.now += 60.1
    assert run(second.acquire(TARGET, 2)) is True


def test_persistent_scopes_have_independent_budgets(tmp_path: Path) -> None:
    database = tmp_path / "write-plans.sqlite3"
    form_reads = SlidingWindowCounter("form_read", database_path=database)
    form_writes = SlidingWindowCounter("form_write", database_path=database)

    assert run(form_reads.acquire(TARGET, 1)) is True
    assert run(form_reads.acquire(TARGET, 1)) is False
    assert run(form_writes.acquire(TARGET, 1)) is True
