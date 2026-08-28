"""Composition root for Helix MCP Gateway dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from helix_mcp.clients.arapi import (
    ArapiBridgeClientPool,
    ArapiBridgeProcess,
)
from helix_mcp.config import ServerSettings
from helix_mcp.observability import MetricsRegistry, ToolAuditor
from helix_mcp.services.database import (
    DatabaseMetadataService,
    DatabaseQueryService,
    DatabaseRateLimiter,
    PersistentSqlQueryPlanStore,
    SqlQueryPlanStore,
)
from helix_mcp.services.forms import FormCatalogService, FormQueryService
from helix_mcp.services.health import HealthCheckService
from helix_mcp.services.writes import (
    FormWriteService,
    PersistentWritePlanStore,
    WritePlanStore,
)
from helix_mcp.targeting import (
    RuntimeTargetContext,
    TargetResolver,
    load_runtime_target_context,
)
from helix_mcp.tools import (
    DatabaseToolAdapter,
    FormToolAdapter,
    FormWriteToolAdapter,
    HealthToolAdapter,
    TargetToolAdapter,
)


class ApplicationContext:
    """Shared process dependencies with an explicit asynchronous teardown."""

    __slots__ = (
        "_closed",
        "arapi_bridge",
        "arapi_clients",
        "database_metadata",
        "database_queries",
        "database_tools",
        "form_catalog",
        "form_queries",
        "form_tools",
        "form_writes",
        "health_checks",
        "health_tools",
        "metrics",
        "runtime",
        "sql_query_plans",
        "target_resolver",
        "target_tools",
        "tool_auditor",
        "write_plans",
        "write_tools",
    )

    def __init__(
        self,
        runtime: RuntimeTargetContext,
        *,
        recover_write_plans: bool = True,
    ) -> None:
        self.runtime = runtime
        self.target_resolver = TargetResolver(runtime.registry)
        self.arapi_clients = ArapiBridgeClientPool(runtime.secrets)
        self.arapi_bridge = ArapiBridgeProcess(
            runtime.settings,
            tuple(
                str(target.arapi.bridge_base_url)
                for target in runtime.config.targets
            ),
        )
        self.form_queries = FormQueryService(
            self.target_resolver,
            self.arapi_clients,
            metadata_cache_ttl_seconds=(
                self.settings.metadata_cache_ttl_seconds
            ),
        )
        self.form_catalog = FormCatalogService(
            self.target_resolver,
            self.arapi_clients,
            metadata_cache_ttl_seconds=(
                self.settings.metadata_cache_ttl_seconds
            ),
        )
        database_limiter = DatabaseRateLimiter()
        self.database_metadata = DatabaseMetadataService(
            self.target_resolver,
            self.arapi_clients,
            limiter=database_limiter,
        )
        self.health_checks = HealthCheckService(
            self.target_resolver,
            self.arapi_clients,
            cache_ttl_seconds=self.settings.health_cache_ttl_seconds,
        )
        write_plan_db = runtime.settings.write_plan_db_path
        write_plan_key = runtime.settings.write_plan_key_path
        self.write_plans = (
            PersistentWritePlanStore(
                database_path=write_plan_db,
                key_path=write_plan_key,
                ttl_seconds=self.settings.write_plan_ttl_seconds,
                max_pending=self.settings.max_pending_write_plans,
                recover_interrupted=recover_write_plans,
            )
            if write_plan_db is not None and write_plan_key is not None
            else WritePlanStore(
                ttl_seconds=self.settings.write_plan_ttl_seconds,
                max_pending=self.settings.max_pending_write_plans,
            )
        )
        self.sql_query_plans = (
            PersistentSqlQueryPlanStore(
                database_path=write_plan_db,
                key_path=write_plan_key,
                ttl_seconds=self.settings.write_plan_ttl_seconds,
                max_pending=self.settings.max_pending_write_plans,
                recover_interrupted=recover_write_plans,
            )
            if write_plan_db is not None and write_plan_key is not None
            else SqlQueryPlanStore(
                ttl_seconds=self.settings.write_plan_ttl_seconds,
                max_pending=self.settings.max_pending_write_plans,
            )
        )
        self.database_queries = DatabaseQueryService(
            self.target_resolver,
            self.arapi_clients,
            limiter=database_limiter,
            plans=self.sql_query_plans,
        )
        self.form_writes = FormWriteService(
            self.target_resolver,
            self.arapi_clients,
            self.write_plans,
        )
        self.metrics = MetricsRegistry(runtime.settings.metrics_path)
        self.tool_auditor = ToolAuditor(metrics=self.metrics)
        self.target_tools = TargetToolAdapter(runtime.registry)
        self.form_tools = FormToolAdapter(
            self.form_queries,
            self.form_catalog,
        )
        self.database_tools = DatabaseToolAdapter(
            self.database_queries,
            self.database_metadata,
        )
        self.health_tools = HealthToolAdapter(self.health_checks)
        self.write_tools = FormWriteToolAdapter(self.form_writes)
        self._closed = False

    @property
    def settings(self) -> ServerSettings:
        return self.runtime.config.server

    @property
    def closed(self) -> bool:
        return self._closed

    async def astart(self) -> None:
        if self._closed:
            raise RuntimeError("application context is closed")
        await self.arapi_bridge.start()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        try:
            await self.arapi_clients.aclose()
        except Exception as exc:
            if first_error is None:
                first_error = exc
        try:
            await self.arapi_bridge.aclose()
        except Exception as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            raise first_error

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return (
            f"<ApplicationContext targets={len(self.runtime.registry)} "
            f"state={state}>"
        )


def load_application(
    dotenv_path: str | Path = ".env",
    *,
    environ: Mapping[str, str] | None = None,
    recover_write_plans: bool = True,
) -> ApplicationContext:
    """Load and validate the complete local application graph."""

    return ApplicationContext(
        load_runtime_target_context(dotenv_path, environ=environ),
        recover_write_plans=recover_write_plans,
    )
