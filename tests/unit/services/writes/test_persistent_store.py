"""Tests for encrypted, restart-safe write-plan persistence."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest

from helix_mcp.config import Environment, TargetKey
from helix_mcp.services.writes import (
    ApplyWriteResult,
    PersistentWritePlanStore,
    WriteOperation,
    WriteOutcomeUnknownError,
    WritePlanExpiredError,
    WritePlanPersistenceError,
    WritePlanStatus,
)

TARGET = TargetKey(environment=Environment.DEV)
FORM = "Example:PersistentForm"
BUSINESS_VALUE = "must-be-encrypted-at-rest"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def run(coroutine):
    return asyncio.run(coroutine)


def build_store(
    tmp_path: Path,
    *,
    clock: Clock | None = None,
) -> PersistentWritePlanStore:
    key = tmp_path / "write-plans.key"
    if not key.exists():
        key.write_bytes(b"k" * 32)
        key.chmod(0o600)
    return PersistentWritePlanStore(
        database_path=tmp_path / "write-plans.sqlite3",
        key_path=key,
        ttl_seconds=60,
        max_pending=10,
        clock=clock or Clock(),
    )


async def create_plan(store: PersistentWritePlanStore):
    return await store.create(
        operation=WriteOperation.CREATE,
        target=TARGET,
        form=FORM,
        entry_id=None,
        current_values=None,
        proposed_values={"Name": BUSINESS_VALUE},
        reason="persist one approved test plan",
        precondition=None,
    )


def test_pending_plan_survives_store_restart_and_is_encrypted(
    tmp_path: Path,
) -> None:
    first = build_store(tmp_path)
    plan = run(create_plan(first))

    restarted = build_store(tmp_path)
    recovered = run(restarted.get(plan_id=plan.plan_id, target=TARGET))

    assert recovered == plan
    database = (tmp_path / "write-plans.sqlite3").read_bytes()
    assert BUSINESS_VALUE.encode() not in database
    assert FORM.encode() not in database


def test_applied_result_remains_idempotent_after_restart(
    tmp_path: Path,
) -> None:
    store = build_store(tmp_path)
    plan = run(create_plan(store))
    acquired = run(
        store.acquire(
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            operation=WriteOperation.CREATE,
            target=TARGET,
        )
    )
    assert acquired.plan is not None
    result = ApplyWriteResult(
        plan_id=plan.plan_id,
        operation=WriteOperation.CREATE,
        environment=Environment.DEV,
        form=FORM,
        entry_id="000000000000001",
    )
    run(store.complete(plan.plan_id, result))

    restarted = build_store(tmp_path)
    replay = run(
        restarted.acquire(
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            operation=WriteOperation.CREATE,
            target=TARGET,
        )
    )

    assert replay.plan is None
    assert replay.reused_result is not None
    assert replay.reused_result.entry_id == result.entry_id
    assert replay.reused_result.reused_result is True
    database = (tmp_path / "write-plans.sqlite3").read_bytes()
    assert BUSINESS_VALUE.encode() not in database


def test_applying_plan_becomes_outcome_unknown_after_restart(
    tmp_path: Path,
) -> None:
    store = build_store(tmp_path)
    plan = run(create_plan(store))
    run(
        store.acquire(
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            operation=WriteOperation.CREATE,
            target=TARGET,
        )
    )

    inspection = PersistentWritePlanStore(
        database_path=tmp_path / "write-plans.sqlite3",
        key_path=tmp_path / "write-plans.key",
        ttl_seconds=60,
        max_pending=10,
        clock=Clock(),
        recover_interrupted=False,
    )
    unchanged = run(inspection.get(plan_id=plan.plan_id, target=TARGET))
    assert unchanged.status is WritePlanStatus.APPLYING

    restarted = build_store(tmp_path)
    recovered = run(restarted.get(plan_id=plan.plan_id, target=TARGET))

    assert recovered.status is WritePlanStatus.OUTCOME_UNKNOWN
    assert recovered.proposed_values == {}
    assert recovered.reason == ""
    with pytest.raises(WriteOutcomeUnknownError):
        run(
            restarted.acquire(
                plan_id=plan.plan_id,
                plan_digest=plan.plan_digest,
                operation=WriteOperation.CREATE,
                target=TARGET,
            )
        )


def test_expired_persistent_plan_is_physically_removed(tmp_path: Path) -> None:
    clock = Clock()
    store = build_store(tmp_path, clock=clock)
    plan = run(create_plan(store))
    clock.now += 61

    with pytest.raises(WritePlanExpiredError):
        run(store.get(plan_id=plan.plan_id, target=TARGET))

    with sqlite3.connect(tmp_path / "write-plans.sqlite3") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM write_plans"
        ).fetchone()[0]
    assert count == 0


def test_ciphertext_tampering_is_rejected_without_exposing_payload(
    tmp_path: Path,
) -> None:
    store = build_store(tmp_path)
    plan = run(create_plan(store))
    with sqlite3.connect(tmp_path / "write-plans.sqlite3") as connection:
        connection.execute(
            "UPDATE write_plans SET payload = ? WHERE plan_id = ?",
            (b"tampered", plan.plan_id),
        )

    with pytest.raises(WritePlanPersistenceError) as exc_info:
        run(store.get(plan_id=plan.plan_id, target=TARGET))

    assert BUSINESS_VALUE not in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_key_with_broad_permissions_is_rejected(tmp_path: Path) -> None:
    key = tmp_path / "write-plans.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o644)

    with pytest.raises(WritePlanPersistenceError, match="permissions"):
        PersistentWritePlanStore(
            database_path=tmp_path / "write-plans.sqlite3",
            key_path=key,
            ttl_seconds=60,
            max_pending=10,
        )
