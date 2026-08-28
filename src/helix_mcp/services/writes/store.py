"""Bounded in-memory write plans with one-time apply semantics."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from helix_mcp.config import TargetKey
from helix_mcp.services.writes.errors import (
    WriteOutcomeUnknownError,
    WritePlanCapacityError,
    WritePlanExpiredError,
    WritePlanMismatchError,
    WritePlanNotFoundError,
    WritePlanStateError,
)
from helix_mcp.services.writes.models import (
    ApplyWriteResult,
    JsonScalar,
    WriteOperation,
    WritePlanResult,
    WritePlanStatus,
)


@dataclass(slots=True, repr=False)
class StoredWritePlan:
    plan_id: str
    plan_digest: str
    operation: WriteOperation
    target: TargetKey
    form: str
    entry_id: str | None
    current_values: dict[str, JsonScalar] | None = field(repr=False)
    proposed_values: dict[str, JsonScalar] = field(repr=False)
    reason: str = field(repr=False)
    precondition: str | None = field(repr=False)
    expires_at: float
    status: WritePlanStatus = WritePlanStatus.PENDING
    result: ApplyWriteResult | None = None


@dataclass(frozen=True, slots=True)
class AcquiredWritePlan:
    plan: StoredWritePlan | None
    reused_result: ApplyWriteResult | None = None


class WritePlanStore:
    """Keep sensitive plan values only in process memory for a short TTL."""

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
        self._plans: dict[str, StoredWritePlan] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        operation: WriteOperation,
        target: TargetKey,
        form: str,
        entry_id: str | None,
        current_values: dict[str, JsonScalar] | None,
        proposed_values: dict[str, JsonScalar],
        reason: str,
        precondition: str | None,
    ) -> WritePlanResult:
        async with self._lock:
            self._purge_expired()
            pending = sum(
                plan.status
                in {WritePlanStatus.PENDING, WritePlanStatus.APPLYING}
                for plan in self._plans.values()
            )
            if pending >= self._max_pending:
                raise WritePlanCapacityError(
                    "pending write-plan capacity was reached"
                )
            plan_id = secrets.token_hex(16)
            digest = _digest(
                operation=operation,
                target=target,
                form=form,
                entry_id=entry_id,
                current_values=current_values,
                proposed_values=proposed_values,
                reason=reason,
                precondition=precondition,
            )
            plan = StoredWritePlan(
                plan_id=plan_id,
                plan_digest=digest,
                operation=operation,
                target=target,
                form=form,
                entry_id=entry_id,
                current_values=(
                    None if current_values is None else dict(current_values)
                ),
                proposed_values=dict(proposed_values),
                reason=reason,
                precondition=precondition,
                expires_at=self._clock() + self._ttl,
            )
            self._plans[plan_id] = plan
            return _public(plan)

    async def acquire(
        self,
        *,
        plan_id: str,
        plan_digest: str,
        operation: WriteOperation,
        target: TargetKey,
    ) -> AcquiredWritePlan:
        async with self._lock:
            plan = self._required(plan_id)
            self._ensure_not_expired(plan)
            if (
                plan.operation is not operation
                or plan.target != target
                or not secrets.compare_digest(
                    plan.plan_digest,
                    plan_digest,
                )
            ):
                raise WritePlanMismatchError(
                    "write plan does not match the apply request"
                )
            if plan.status is WritePlanStatus.APPLIED:
                assert plan.result is not None
                return AcquiredWritePlan(
                    plan=None,
                    reused_result=plan.result.model_copy(
                        update={"reused_result": True}
                    ),
                )
            if plan.status is WritePlanStatus.OUTCOME_UNKNOWN:
                raise WriteOutcomeUnknownError(
                    "write outcome is unknown and cannot be retried"
                )
            if plan.status is not WritePlanStatus.PENDING:
                raise WritePlanStateError(
                    "write plan is not available for apply"
                )
            plan.status = WritePlanStatus.APPLYING
            return AcquiredWritePlan(plan=plan)

    async def complete(
        self,
        plan_id: str,
        result: ApplyWriteResult,
    ) -> ApplyWriteResult:
        async with self._lock:
            plan = self._required(plan_id)
            plan.status = WritePlanStatus.APPLIED
            plan.result = result
            _clear_payload(plan)
            return result

    async def fail(self, plan_id: str, *, outcome_unknown: bool) -> None:
        async with self._lock:
            plan = self._required(plan_id)
            plan.status = (
                WritePlanStatus.OUTCOME_UNKNOWN
                if outcome_unknown
                else WritePlanStatus.FAILED
            )
            _clear_payload(plan)

    async def get(
        self,
        *,
        plan_id: str,
        target: TargetKey,
    ) -> WritePlanResult:
        async with self._lock:
            plan = self._required(plan_id)
            self._ensure_not_expired(plan)
            if plan.target != target:
                raise WritePlanMismatchError(
                    "write plan does not match the selected target"
                )
            return _public(plan)

    async def cancel(
        self,
        *,
        plan_id: str,
        target: TargetKey,
    ) -> WritePlanResult:
        async with self._lock:
            plan = self._required(plan_id)
            self._ensure_not_expired(plan)
            if plan.target != target:
                raise WritePlanMismatchError(
                    "write plan does not match the selected target"
                )
            if plan.status is not WritePlanStatus.PENDING:
                raise WritePlanStateError(
                    "only pending write plans can be cancelled"
                )
            plan.status = WritePlanStatus.CANCELLED
            _clear_payload(plan)
            return _public(plan)

    def _required(self, plan_id: str) -> StoredWritePlan:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise WritePlanNotFoundError("write plan was not found")
        return plan

    def _ensure_not_expired(self, plan: StoredWritePlan) -> None:
        if plan.expires_at <= self._clock():
            self._plans.pop(plan.plan_id, None)
            _clear_payload(plan)
            raise WritePlanExpiredError("write plan has expired")

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [
            plan_id
            for plan_id, plan in self._plans.items()
            if plan.expires_at <= now
        ]
        for plan_id in expired:
            plan = self._plans.pop(plan_id)
            _clear_payload(plan)


def _public(plan: StoredWritePlan) -> WritePlanResult:
    return WritePlanResult(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        operation=plan.operation,
        environment=plan.target.environment,
        form=plan.form,
        entry_id=plan.entry_id,
        current_values=(
            None if plan.current_values is None else dict(plan.current_values)
        ),
        proposed_values=dict(plan.proposed_values),
        reason=plan.reason,
        expires_at=datetime.fromtimestamp(plan.expires_at, tz=UTC),
        status=plan.status,
    )


def _clear_payload(plan: StoredWritePlan) -> None:
    if plan.current_values is not None:
        plan.current_values.clear()
    plan.proposed_values.clear()
    plan.reason = ""
    plan.precondition = None


def _digest(
    *,
    operation: WriteOperation,
    target: TargetKey,
    form: str,
    entry_id: str | None,
    current_values: dict[str, JsonScalar] | None,
    proposed_values: dict[str, JsonScalar],
    reason: str,
    precondition: str | None,
) -> str:
    canonical = json.dumps(
        {
            "operation": operation.value,
            "environment": target.environment.value,
            "form": form,
            "entry_id": entry_id,
            "current_values": current_values,
            "proposed_values": proposed_values,
            "reason": reason,
            "precondition": precondition,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
