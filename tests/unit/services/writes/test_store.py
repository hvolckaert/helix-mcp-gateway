"""Tests for bounded, expiring and one-time write-plan storage."""

from __future__ import annotations

import asyncio

import pytest

from helix_mcp.config import Environment, TargetKey
from helix_mcp.services.writes import (
    WriteOperation,
    WritePlanCapacityError,
    WritePlanExpiredError,
    WritePlanStateError,
    WritePlanStatus,
    WritePlanStore,
)


def run(coroutine):
    return asyncio.run(coroutine)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


async def create_plan(store: WritePlanStore):
    return await store.create(
        operation=WriteOperation.CREATE,
        target=TargetKey(instance="helix", environment=Environment.DEV),
        form="Example:ComputerSystem",
        entry_id=None,
        current_values=None,
        proposed_values={"DatasetId": "TEST.SAMPLE"},
        reason="test bounded write plan storage",
        precondition=None,
    )


def test_expired_plan_is_removed_and_cannot_be_applied() -> None:
    clock = Clock()
    store = WritePlanStore(
        ttl_seconds=60,
        max_pending=1,
        clock=clock,
    )
    plan = run(create_plan(store))
    clock.now += 61

    with pytest.raises(WritePlanExpiredError):
        run(
            store.acquire(
                plan_id=plan.plan_id,
                plan_digest=plan.plan_digest,
                operation=WriteOperation.CREATE,
                target=TargetKey(
                    environment=Environment.DEV,
                ),
            )
        )


def test_capacity_is_released_when_a_pending_plan_is_cancelled() -> None:
    store = WritePlanStore(ttl_seconds=60, max_pending=1)
    first = run(create_plan(store))

    with pytest.raises(WritePlanCapacityError):
        run(create_plan(store))

    cancelled = run(
        store.cancel(
            plan_id=first.plan_id,
            target=TargetKey(
                environment=Environment.DEV,
            ),
        )
    )
    second = run(create_plan(store))

    assert cancelled.status is WritePlanStatus.CANCELLED
    assert cancelled.proposed_values == {}
    assert cancelled.reason == ""
    assert second.status is WritePlanStatus.PENDING


def test_acquired_plan_cannot_be_acquired_concurrently() -> None:
    store = WritePlanStore(ttl_seconds=60, max_pending=1)
    plan = run(create_plan(store))
    request = {
        "plan_id": plan.plan_id,
        "plan_digest": plan.plan_digest,
        "operation": WriteOperation.CREATE,
        "target": TargetKey(
            environment=Environment.DEV,
        ),
    }

    acquired = run(store.acquire(**request))

    assert acquired.plan is not None
    with pytest.raises(WritePlanStateError):
        run(store.acquire(**request))
