"""Tests for deterministic application startup and shutdown."""

from __future__ import annotations

import asyncio
import logging

import pytest

from helix_mcp.lifecycle import application_lifespan


def run(coroutine):
    return asyncio.run(coroutine)


class FakeApplication:
    def __init__(self, startup_error: Exception | None = None) -> None:
        self.startup_error = startup_error
        self.started = 0
        self.closed = 0

    async def astart(self) -> None:
        self.started += 1
        if self.startup_error is not None:
            raise self.startup_error

    async def aclose(self) -> None:
        self.closed += 1


async def _normal_lifecycle(application: FakeApplication) -> None:
    lifespan = application_lifespan(application)
    async with lifespan(None):
        assert application.started == 1
        assert application.closed == 0


async def _failed_lifecycle(application: FakeApplication) -> None:
    lifespan = application_lifespan(application)
    async with lifespan(None):
        raise AssertionError("lifespan must not yield")


def test_lifespan_closes_resources_after_normal_operation(caplog) -> None:
    application = FakeApplication()

    with caplog.at_level(logging.INFO, logger="helix_mcp.lifecycle"):
        run(_normal_lifecycle(application))

    assert application.closed == 1
    assert [record.event for record in caplog.records] == [
        "application_starting",
        "application_ready",
        "application_stopping",
        "application_stopped",
    ]


def test_lifespan_closes_resources_after_startup_failure(caplog) -> None:
    application = FakeApplication(RuntimeError("private startup detail"))

    with (
        caplog.at_level(logging.INFO, logger="helix_mcp.lifecycle"),
        pytest.raises(RuntimeError, match="private startup detail"),
    ):
        run(_failed_lifecycle(application))

    assert application.closed == 1
    assert "private startup detail" not in " ".join(
        record.getMessage() for record in caplog.records
    )
