"""Public health models that exclude connection and credential details."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from helix_mcp.config import Environment
from helix_mcp.config.models import FrozenModel


class HealthStatus(StrEnum):
    """Coarse state safe to expose through MCP."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class HealthComponent(StrEnum):
    """Target components that can be checked independently."""

    ARAPI_BRIDGE = "arapi_bridge"
    KAAZING = "kaazing"


class HealthComponentResult(FrozenModel):
    """Sanitized outcome for one target component."""

    component: HealthComponent
    status: HealthStatus
    latency_ms: int = Field(ge=0)
    error_code: str | None = None


class HealthCheckResult(FrozenModel):
    """Aggregate health for one explicitly selected environment."""

    environment: Environment
    status: HealthStatus
    cached: bool
    checks: tuple[HealthComponentResult, ...]
