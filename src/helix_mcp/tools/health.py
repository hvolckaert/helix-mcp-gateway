"""MCP-independent adapter for target health checks."""

from __future__ import annotations

from helix_mcp.config import Environment
from helix_mcp.services.health import HealthCheckResult, HealthCheckService


class HealthToolAdapter:
    """Expose sanitized target health without connection details."""

    __slots__ = ("_service",)

    def __init__(self, service: HealthCheckService) -> None:
        self._service = service

    async def health_check(
        self,
        *,
        environment: Environment,
        force_refresh: bool = False,
    ) -> HealthCheckResult:
        return await self._service.check(
            environment=environment,
            force_refresh=force_refresh,
        )
