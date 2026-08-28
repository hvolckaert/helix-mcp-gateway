"""Tests for encrypted and restart-safe SQL query plans."""

from __future__ import annotations

import asyncio
from pathlib import Path

from helix_mcp.config import Environment, TargetKey
from helix_mcp.services.database import (
    DatabaseQuery,
    PersistentSqlQueryPlanStore,
    SqlQueryPlanStatus,
)

TARGET = TargetKey(environment=Environment.DEV)
MARKER = "sql-plan-business-value-must-be-encrypted"
SQL = (
    "SELECT marker AS marker FROM public.allowed_table "
    f"WHERE marker = '{MARKER}'"
)


def run(coroutine):
    return asyncio.run(coroutine)


def build_store(tmp_path: Path) -> PersistentSqlQueryPlanStore:
    key = tmp_path / "write-plans.key"
    if not key.exists():
        key.write_bytes(b"k" * 32)
        key.chmod(0o600)
    return PersistentSqlQueryPlanStore(
        database_path=tmp_path / "write-plans.sqlite3",
        key_path=key,
        ttl_seconds=60,
        max_pending=10,
    )


def test_pending_sql_plan_survives_restart_and_is_encrypted(
    tmp_path: Path,
) -> None:
    first = build_store(tmp_path)
    plan = run(
        first.create(
            target=TARGET,
            query=DatabaseQuery(sql=SQL, limit=5),
        )
    )

    restarted = build_store(tmp_path)
    recovered = run(restarted.get(plan_id=plan.plan_id, target=TARGET))

    volatile = {"server_time", "remaining_seconds"}
    assert recovered.model_dump(exclude=volatile) == plan.model_dump(
        exclude=volatile
    )
    assert recovered.server_time >= plan.server_time
    assert recovered.remaining_seconds <= plan.remaining_seconds
    database = (tmp_path / "write-plans.sqlite3").read_bytes()
    assert SQL.encode() not in database
    assert MARKER.encode() not in database


def test_interrupted_read_execution_is_safely_recoverable(
    tmp_path: Path,
) -> None:
    store = build_store(tmp_path)
    plan = run(
        store.create(
            target=TARGET,
            query=DatabaseQuery(sql=SQL, limit=5),
        )
    )
    run(
        store.acquire(
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            target=TARGET,
        )
    )

    restarted = build_store(tmp_path)
    recovered = run(restarted.get(plan_id=plan.plan_id, target=TARGET))

    assert recovered.status is SqlQueryPlanStatus.PENDING
