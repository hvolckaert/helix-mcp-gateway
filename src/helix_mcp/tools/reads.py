"""Attach policy and acquisition provenance at the audited MCP boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel

from helix_mcp.config import Environment, TargetKey
from helix_mcp.observability import ToolAuditor
from helix_mcp.provenance import (
    ReadProvenance,
    ReadSource,
    capture_cache_reads,
    policy_version,
    utc_now,
)
from helix_mcp.targeting import TargetRegistry
from helix_mcp.tools.read_models import ProvenancedOutput

OutputT = TypeVar("OutputT", bound=ProvenancedOutput)
_PRIMARY_CACHES = {
    "list_forms": "form_catalog",
    "list_form_fields": "field_catalog",
    "health_check": "health",
}
_LOCAL_READS = {"list_targets", "get_sql_query_plan", "get_write_plan"}


class ReadToolRunner:
    """Use the same registry as policy enforcement, without another fetch."""

    def __init__(
        self,
        registry: TargetRegistry,
        audit: ToolAuditor,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._clock = clock

    async def execute(
        self,
        *,
        tool: str,
        output_type: type[OutputT],
        operation: Callable[[], Awaitable[BaseModel]],
        environment: Environment | None = None,
    ) -> OutputT:
        async def read() -> OutputT:
            with capture_cache_reads() as observations:
                result = await operation()
                payload = result.model_dump()
                if (
                    environment is not None
                    and payload.get("environment") != environment
                ):
                    raise ValueError(
                        "read result environment does not match request"
                    )
                # Result identities, rather than unvalidated request arguments,
                # also avoid duplicating SQL text, qualifications and values.
                source = _source(tool, payload)
                environments = (
                    [environment]
                    if environment is not None
                    else [
                        Environment(t["environment"])
                        for t in payload["targets"]
                    ]
                )
                versions = {
                    env: policy_version(
                        self._registry.policy_for(
                            self._registry.get(TargetKey(environment=env))
                        )
                    )
                    for env in environments
                }
                completed_at = self._clock()
                primary = next(
                    (
                        o
                        for o in observations
                        if o.kind == _PRIMARY_CACHES.get(tool)
                    ),
                    None,
                )
                provenance = ReadProvenance(
                    environment=environment,
                    source=source,
                    read_at=completed_at,
                    data_read_at=primary.read_at if primary else completed_at,
                    cached=primary.cached if primary else False,
                    cache_reads=tuple(observations),
                    policy_versions=versions,
                )
                return output_type.model_validate(
                    {**payload, "provenance": provenance}
                )

        return await self._audit.execute(
            tool=tool, environment=environment, operation=read
        )


def _source(tool: str, payload: dict[str, object]) -> ReadSource:
    source: dict[str, object] = {
        "operation": tool,
        "backend": (
            "gateway"
            if tool in _LOCAL_READS
            else "health"
            if tool == "health_check"
            else "arapi"
        ),
    }
    for key in (
        "form",
        "entry_id",
        "schema_name",
        "object_name",
        "plan_id",
        "plan_digest",
    ):
        if key in payload:
            source[key] = payload[key]
    field = payload.get("field")
    if isinstance(field, dict):
        source["field_id"] = field["id"]
    view = payload.get("view")
    if isinstance(view, dict):
        source["view_id"] = view["id"]
    obj = payload.get("object")
    if isinstance(obj, dict):
        source["schema_name"] = obj["schema_name"]
        source["object_name"] = obj["name"]
    return ReadSource.model_validate(source)
