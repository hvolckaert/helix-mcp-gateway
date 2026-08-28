"""MCP-independent adapter for target discovery."""

from __future__ import annotations

from helix_mcp.targeting import TargetRegistry
from helix_mcp.tools.models import ListTargetsOutput


class TargetToolAdapter:
    """Expose only the registry's sanitized descriptors."""

    __slots__ = ("_registry",)

    def __init__(self, registry: TargetRegistry) -> None:
        self._registry = registry

    async def list_targets(
        self,
        *,
        include_disabled: bool = False,
    ) -> ListTargetsOutput:
        return ListTargetsOutput(
            targets=self._registry.list_descriptors(
                include_disabled=include_disabled
            )
        )
