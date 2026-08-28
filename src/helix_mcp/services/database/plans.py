"""In-memory one-time plans for approved read-only SQL execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from helix_mcp.config import TargetKey
from helix_mcp.services.database.errors import (
    SqlQueryPlanCapacityError,
    SqlQueryPlanExpiredError,
    SqlQueryPlanMismatchError,
    SqlQueryPlanNotFoundError,
    SqlQueryPlanStateError,
)
from helix_mcp.services.database.models import (
    DatabaseQuery,
    SqlQueryPlanResult,
    SqlQueryPlanStatus,
)


@dataclass(slots=True)
class StoredSqlQueryPlan:
    plan_id: str
    plan_digest: str
    target: TargetKey
    query: DatabaseQuery
    expires_at: float
    status: SqlQueryPlanStatus = SqlQueryPlanStatus.PENDING


class SqlQueryPlanStore:
    """Keep bounded SQL plans until approval, cancellation, or expiry."""

    __slots__ = ("_clock", "_lock", "_max_pending", "_plans", "_ttl")

    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_pending: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._clock = clock
        self._lock = asyncio.Lock()
        self._plans: dict[str, StoredSqlQueryPlan] = {}

    async def create(
        self,
        *,
        target: TargetKey,
        query: DatabaseQuery,
    ) -> SqlQueryPlanResult:
        async with self._lock:
            self._purge_expired()
            pending = sum(
                plan.status
                in {SqlQueryPlanStatus.PENDING, SqlQueryPlanStatus.EXECUTING}
                for plan in self._plans.values()
            )
            if pending >= self._max_pending:
                raise SqlQueryPlanCapacityError(
                    "pending SQL-query-plan capacity was reached"
                )
            plan = StoredSqlQueryPlan(
                plan_id=secrets.token_hex(16),
                plan_digest=_digest(target=target, query=query),
                target=target,
                query=query,
                expires_at=self._clock() + self._ttl,
            )
            self._plans[plan.plan_id] = plan
            return _public(plan, now=self._clock())

    async def acquire(
        self,
        *,
        plan_id: str,
        plan_digest: str,
        target: TargetKey,
    ) -> StoredSqlQueryPlan:
        async with self._lock:
            plan = self._required(plan_id)
            if plan.target != target or not secrets.compare_digest(
                plan.plan_digest,
                plan_digest,
            ):
                raise SqlQueryPlanMismatchError(
                    "SQL query plan does not match the execution request"
                )
            if plan.status is not SqlQueryPlanStatus.PENDING:
                raise SqlQueryPlanStateError(
                    "SQL query plan is not available for execution"
                )
            plan.status = SqlQueryPlanStatus.EXECUTING
            return plan

    async def complete(self, plan_id: str) -> None:
        await self._set_status(plan_id, SqlQueryPlanStatus.EXECUTED)

    async def fail(self, plan_id: str) -> None:
        async with self._lock:
            plan = self._required(plan_id, check_expiry=False)
            if plan.status is not SqlQueryPlanStatus.EXECUTING:
                raise SqlQueryPlanStateError("SQL query plan is not executing")
            plan.status = SqlQueryPlanStatus.PENDING

    async def get(
        self,
        *,
        plan_id: str,
        target: TargetKey,
    ) -> SqlQueryPlanResult:
        async with self._lock:
            plan = self._required(plan_id)
            if plan.target != target:
                raise SqlQueryPlanMismatchError(
                    "SQL query plan belongs to another target"
                )
            return _public(plan, now=self._clock())

    async def cancel(
        self,
        *,
        plan_id: str,
        target: TargetKey,
    ) -> SqlQueryPlanResult:
        async with self._lock:
            plan = self._required(plan_id)
            if plan.target != target:
                raise SqlQueryPlanMismatchError(
                    "SQL query plan belongs to another target"
                )
            if plan.status is not SqlQueryPlanStatus.PENDING:
                raise SqlQueryPlanStateError(
                    "only a pending SQL query plan can be cancelled"
                )
            plan.status = SqlQueryPlanStatus.CANCELLED
            return _public(plan, now=self._clock())

    async def _set_status(
        self,
        plan_id: str,
        status: SqlQueryPlanStatus,
    ) -> None:
        async with self._lock:
            plan = self._required(plan_id, check_expiry=False)
            if plan.status is not SqlQueryPlanStatus.EXECUTING:
                raise SqlQueryPlanStateError("SQL query plan is not executing")
            plan.status = status

    def _required(
        self,
        plan_id: str,
        *,
        check_expiry: bool = True,
    ) -> StoredSqlQueryPlan:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise SqlQueryPlanNotFoundError("SQL query plan was not found")
        if check_expiry and plan.expires_at <= self._clock():
            del self._plans[plan_id]
            raise SqlQueryPlanExpiredError("SQL query plan has expired")
        return plan

    def _purge_expired(self) -> None:
        now = self._clock()
        for plan_id in [
            plan_id
            for plan_id, plan in self._plans.items()
            if plan.expires_at <= now
        ]:
            del self._plans[plan_id]


def _digest(*, target: TargetKey, query: DatabaseQuery) -> str:
    payload = json.dumps(
        {
            "environment": target.environment.value,
            "sql": query.sql,
            "limit": query.limit,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _public(plan: StoredSqlQueryPlan, *, now: float) -> SqlQueryPlanResult:
    return SqlQueryPlanResult(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        environment=plan.target.environment,
        sql=plan.query.sql,
        limit=plan.query.limit,
        status=plan.status,
        server_time=datetime.fromtimestamp(now, tz=UTC),
        expires_at=datetime.fromtimestamp(plan.expires_at, tz=UTC),
        remaining_seconds=max(0, math.ceil(plan.expires_at - now)),
    )
