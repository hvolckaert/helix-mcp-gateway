"""Policy-enforced two-phase creation and update of Helix entries."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from time import monotonic
from typing import Protocol

from helix_mcp.clients.arapi import (
    ArapiBridgeClient,
    ArapiBridgeClientPool,
    ArapiBridgeConflictError,
    ArapiBridgeError,
    ArapiBridgeProtocolError,
    ArapiBridgeTransportError,
)
from helix_mcp.config import AccessMode, BackendKind, Environment, TargetKey
from helix_mcp.services.writes.errors import (
    FormWriteConflictError,
    FormWriteDisabledError,
    FormWriteFieldNotAllowedError,
    FormWriteFormNotAllowedError,
    FormWriteRateLimitError,
    FormWriteReasonRequiredError,
    WriteOutcomeUnknownError,
)
from helix_mcp.services.writes.models import (
    ApplyWriteRequest,
    ApplyWriteResult,
    JsonScalar,
    UpdateValuesRequest,
    WriteOperation,
    WritePlanResult,
    WritePlanStatus,
    WriteValuesRequest,
)
from helix_mcp.services.writes.persistent_store import PersistentWritePlanStore
from helix_mcp.services.writes.store import WritePlanStore
from helix_mcp.targeting import ResolvedTarget, TargetResolver


class ArapiClientProvider(Protocol):
    def get(self, target: ResolvedTarget) -> ArapiBridgeClient: ...


class FormWriteService:
    """Create reviewable plans and apply each approved plan at most once."""

    __slots__ = ("_clients", "_limiter", "_plans", "_targets")

    def __init__(
        self,
        targets: TargetResolver,
        clients: ArapiClientProvider | ArapiBridgeClientPool,
        plans: WritePlanStore | PersistentWritePlanStore,
        *,
        time_source: Callable[[], float] = monotonic,
    ) -> None:
        self._targets = targets
        self._clients = clients
        self._plans = plans
        self._limiter = _WriteRateLimiter(time_source=time_source)

    async def plan_create_for_form(
        self,
        *,
        environment: str | Environment,
        form: str,
        request: WriteValuesRequest,
    ) -> WritePlanResult:
        target = self._resolve(environment)
        values = _enforce_write(
            target,
            operation=WriteOperation.CREATE,
            form=form,
            values=request.values,
            reason=request.reason,
        )
        return await self._plans.create(
            operation=WriteOperation.CREATE,
            target=target.key,
            form=form,
            entry_id=None,
            current_values=None,
            proposed_values=values,
            reason=request.reason,
            precondition=None,
        )

    async def plan_update(
        self,
        *,
        environment: str | Environment,
        form: str,
        request: UpdateValuesRequest,
    ) -> WritePlanResult:
        target = self._resolve(environment)
        values = _enforce_write(
            target,
            operation=WriteOperation.UPDATE,
            form=form,
            values=request.values,
            reason=request.reason,
        )
        prepared = await self._clients.get(target).prepare_update(
            form=form,
            entry_id=request.entry_id,
            fields=tuple(values),
        )
        current = _parse_current_values(prepared.entry.values, values)
        return await self._plans.create(
            operation=WriteOperation.UPDATE,
            target=target.key,
            form=form,
            entry_id=request.entry_id,
            current_values=current,
            proposed_values=values,
            reason=request.reason,
            precondition=prepared.precondition,
        )

    async def apply_create(
        self,
        *,
        environment: str | Environment,
        request: ApplyWriteRequest,
    ) -> ApplyWriteResult:
        return await self._apply(
            environment=environment,
            operation=WriteOperation.CREATE,
            request=request,
        )

    async def apply_update(
        self,
        *,
        environment: str | Environment,
        request: ApplyWriteRequest,
    ) -> ApplyWriteResult:
        return await self._apply(
            environment=environment,
            operation=WriteOperation.UPDATE,
            request=request,
        )

    async def get_plan(
        self,
        *,
        environment: str | Environment,
        plan_id: str,
    ) -> WritePlanResult:
        target = self._resolve(environment)
        return await self._plans.get(plan_id=plan_id, target=target.key)

    async def cancel_plan(
        self,
        *,
        environment: str | Environment,
        plan_id: str,
    ) -> WritePlanResult:
        target = self._resolve(environment)
        return await self._plans.cancel(plan_id=plan_id, target=target.key)

    async def _apply(
        self,
        *,
        environment: str | Environment,
        operation: WriteOperation,
        request: ApplyWriteRequest,
    ) -> ApplyWriteResult:
        target = self._resolve(environment)
        acquired = await self._plans.acquire(
            plan_id=request.plan_id,
            plan_digest=request.plan_digest,
            operation=operation,
            target=target.key,
        )
        if acquired.reused_result is not None:
            return acquired.reused_result
        assert acquired.plan is not None
        plan = acquired.plan
        try:
            values = _enforce_write(
                target,
                operation=operation,
                form=plan.form,
                values=plan.proposed_values,
                reason=plan.reason,
            )
            await self._limiter.check(
                target.key,
                target.policy.write_rate_limit_per_minute,
            )
            client = self._clients.get(target)
            if operation is WriteOperation.CREATE:
                entry_id = await client.create_entry(
                    form=plan.form,
                    values=values,
                )
            else:
                assert plan.entry_id is not None
                assert plan.precondition is not None
                await client.update_entry(
                    form=plan.form,
                    entry_id=plan.entry_id,
                    values=values,
                    precondition=plan.precondition,
                )
                entry_id = plan.entry_id
        except ArapiBridgeConflictError:
            await self._plans.fail(request.plan_id, outcome_unknown=False)
            raise FormWriteConflictError(
                "entry changed after the write plan was created"
            ) from None
        except ArapiBridgeError as error:
            outcome_unknown = _arapi_outcome_unknown(error)
            await self._plans.fail(
                request.plan_id,
                outcome_unknown=outcome_unknown,
            )
            if outcome_unknown:
                raise WriteOutcomeUnknownError(
                    "write outcome is unknown and cannot be retried"
                ) from None
            raise
        except Exception:
            await self._plans.fail(request.plan_id, outcome_unknown=False)
            raise

        result = ApplyWriteResult(
            plan_id=plan.plan_id,
            operation=operation,
            environment=target.key.environment,
            form=plan.form,
            status=WritePlanStatus.APPLIED,
            entry_id=entry_id,
        )
        return await self._plans.complete(request.plan_id, result)

    def _resolve(
        self,
        environment: str | Environment,
    ) -> ResolvedTarget:
        return self._targets.resolve(
            environment=environment,
            backend=BackendKind.ARAPI,
        )


class _WriteRateLimiter:
    __slots__ = ("_events", "_lock", "_time")

    def __init__(self, *, time_source: Callable[[], float]) -> None:
        self._events: dict[TargetKey, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._time = time_source

    async def check(self, target: TargetKey, limit: int) -> None:
        async with self._lock:
            now = self._time()
            oldest_allowed = now - 60.0
            events = self._events.setdefault(target, deque())
            while events and events[0] <= oldest_allowed:
                events.popleft()
            if len(events) >= limit:
                raise FormWriteRateLimitError(
                    "form write rate limit was reached"
                )
            events.append(now)


def _enforce_write(
    target: ResolvedTarget,
    *,
    operation: WriteOperation,
    form: str,
    values: Mapping[str, JsonScalar],
    reason: str,
) -> dict[str, JsonScalar]:
    policy = target.policy
    if policy.access_mode is not AccessMode.READ_WRITE:
        raise FormWriteDisabledError(
            "requested form write is disabled by target policy"
        )
    if form not in policy.writable_forms:
        raise FormWriteFormNotAllowedError(
            "form is not included in the write allowlist"
        )
    if policy.require_write_reason and not reason.strip():
        raise FormWriteReasonRequiredError("write reason is required")
    fields_by_form = (
        policy.creatable_fields_by_form
        if operation is WriteOperation.CREATE
        else policy.updatable_fields_by_form
    )
    allowed = {
        field.casefold(): field for field in fields_by_form.get(form, ())
    }
    requested = {field.casefold() for field in values}
    if not requested.issubset(allowed):
        raise FormWriteFieldNotAllowedError(
            "write requests a field outside the write allowlist"
        )
    return {
        allowed[field.casefold()]: value for field, value in values.items()
    }


def _parse_current_values(
    raw_values: Mapping[str, object],
    requested: Mapping[str, JsonScalar],
) -> dict[str, JsonScalar]:
    by_name = {
        str(field).casefold(): value
        for field, value in raw_values.items()
        if isinstance(field, str)
    }
    current: dict[str, JsonScalar] = {}
    for field in requested:
        value = by_name.get(field.casefold())
        if isinstance(value, (str, int, float, bool)):
            current[field] = value
    return current


def _arapi_outcome_unknown(error: ArapiBridgeError) -> bool:
    return isinstance(
        error,
        (ArapiBridgeTransportError, ArapiBridgeProtocolError),
    )
