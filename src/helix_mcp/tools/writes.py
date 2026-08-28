"""MCP-independent validation adapter for two-phase form writes."""

from __future__ import annotations

from pydantic import ValidationError

from helix_mcp.config import Environment
from helix_mcp.services.writes import (
    ApplyWriteRequest,
    ApplyWriteResult,
    FormWriteService,
    JsonScalar,
    PlanLookupRequest,
    UpdateValuesRequest,
    WritePlanResult,
    WriteValuesRequest,
)
from helix_mcp.tools.errors import ToolInputError


class FormWriteToolAdapter:
    """Validate tool inputs before delegating to the write service."""

    __slots__ = ("_service",)

    def __init__(self, service: FormWriteService) -> None:
        self._service = service

    async def plan_create_entry(
        self,
        *,
        environment: Environment,
        form: str,
        values: dict[str, JsonScalar],
        reason: str,
    ) -> WritePlanResult:
        try:
            request = WriteValuesRequest(values=values, reason=reason)
        except ValidationError:
            raise ToolInputError(
                "plan_create_entry input is invalid"
            ) from None
        return await self._service.plan_create_for_form(
            environment=environment,
            form=form,
            request=request,
        )

    async def plan_update_entry(
        self,
        *,
        environment: Environment,
        form: str,
        entry_id: str,
        values: dict[str, JsonScalar],
        reason: str,
    ) -> WritePlanResult:
        try:
            request = UpdateValuesRequest(
                entry_id=entry_id,
                values=values,
                reason=reason,
            )
        except ValidationError:
            raise ToolInputError(
                "plan_update_entry input is invalid"
            ) from None
        return await self._service.plan_update(
            environment=environment,
            form=form,
            request=request,
        )

    async def apply_create_entry(
        self,
        *,
        environment: Environment,
        plan_id: str,
        plan_digest: str,
    ) -> ApplyWriteResult:
        request = _apply_request(
            plan_id=plan_id,
            plan_digest=plan_digest,
            tool="apply_create_entry",
        )
        return await self._service.apply_create(
            environment=environment,
            request=request,
        )

    async def apply_update_entry(
        self,
        *,
        environment: Environment,
        plan_id: str,
        plan_digest: str,
    ) -> ApplyWriteResult:
        request = _apply_request(
            plan_id=plan_id,
            plan_digest=plan_digest,
            tool="apply_update_entry",
        )
        return await self._service.apply_update(
            environment=environment,
            request=request,
        )

    async def get_write_plan(
        self,
        *,
        environment: Environment,
        plan_id: str,
    ) -> WritePlanResult:
        request = _lookup_request(plan_id, tool="get_write_plan")
        return await self._service.get_plan(
            environment=environment,
            plan_id=request.plan_id,
        )

    async def cancel_write_plan(
        self,
        *,
        environment: Environment,
        plan_id: str,
    ) -> WritePlanResult:
        request = _lookup_request(plan_id, tool="cancel_write_plan")
        return await self._service.cancel_plan(
            environment=environment,
            plan_id=request.plan_id,
        )


def _apply_request(
    *,
    plan_id: str,
    plan_digest: str,
    tool: str,
) -> ApplyWriteRequest:
    try:
        return ApplyWriteRequest(
            plan_id=plan_id,
            plan_digest=plan_digest,
        )
    except ValidationError:
        raise ToolInputError(f"{tool} input is invalid") from None


def _lookup_request(plan_id: str, *, tool: str) -> PlanLookupRequest:
    try:
        return PlanLookupRequest(plan_id=plan_id)
    except ValidationError:
        raise ToolInputError(f"{tool} input is invalid") from None
