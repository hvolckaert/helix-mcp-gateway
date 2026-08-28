"""MCP-independent adapter for read-only SQL through ARAPI."""

from __future__ import annotations

from pydantic import ValidationError

from helix_mcp.config import Environment
from helix_mcp.services.database import (
    DatabaseColumnCatalogQuery,
    DatabaseColumnCatalogResult,
    DatabaseMetadataService,
    DatabaseObjectCatalogQuery,
    DatabaseObjectCatalogResult,
    DatabaseObjectDescription,
    DatabaseObjectKind,
    DatabaseObjectReference,
    DatabaseQuery,
    DatabaseQueryService,
    ExecuteSqlQueryRequest,
    SqlQueryExecutionResult,
    SqlQueryPlanLookupRequest,
    SqlQueryPlanResult,
)
from helix_mcp.tools.errors import ToolInputError


class DatabaseToolAdapter:
    __slots__ = ("_metadata", "_queries")

    def __init__(
        self,
        queries: DatabaseQueryService,
        metadata: DatabaseMetadataService,
    ) -> None:
        self._queries = queries
        self._metadata = metadata

    async def plan_sql_query(
        self,
        *,
        environment: Environment,
        sql: str,
        limit: int = 100,
    ) -> SqlQueryPlanResult:
        try:
            query = DatabaseQuery(
                sql=sql,
                limit=limit,
            )
        except ValidationError:
            raise ToolInputError("plan_sql_query input is invalid") from None
        return await self._queries.plan(
            environment=environment,
            query=query,
        )

    async def execute_sql_query(
        self,
        *,
        environment: Environment,
        plan_id: str,
        plan_digest: str,
    ) -> SqlQueryExecutionResult:
        try:
            request = ExecuteSqlQueryRequest(
                plan_id=plan_id,
                plan_digest=plan_digest,
            )
        except ValidationError:
            raise ToolInputError(
                "execute_sql_query input is invalid"
            ) from None
        return await self._queries.execute_plan(
            environment=environment,
            plan_id=request.plan_id,
            plan_digest=request.plan_digest,
        )

    async def get_sql_query_plan(
        self,
        *,
        environment: Environment,
        plan_id: str,
    ) -> SqlQueryPlanResult:
        validated = _plan_id(plan_id, tool="get_sql_query_plan")
        return await self._queries.get_plan(
            environment=environment,
            plan_id=validated,
        )

    async def cancel_sql_query_plan(
        self,
        *,
        environment: Environment,
        plan_id: str,
    ) -> SqlQueryPlanResult:
        validated = _plan_id(plan_id, tool="cancel_sql_query_plan")
        return await self._queries.cancel_plan(
            environment=environment,
            plan_id=validated,
        )

    async def list_database_objects(
        self,
        *,
        environment: Environment,
        schema: str | None = None,
        name_contains: str | None = None,
        kind: DatabaseObjectKind | None = None,
        include_system: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> DatabaseObjectCatalogResult:
        try:
            query = DatabaseObjectCatalogQuery(
                schema_name=schema,
                name_contains=name_contains,
                kind=kind,
                include_system=include_system,
                offset=offset,
                limit=limit,
            )
        except ValidationError:
            raise ToolInputError(
                "list_database_objects input is invalid"
            ) from None
        return await self._metadata.list_objects(
            environment=environment,
            query=query,
        )

    async def list_database_columns(
        self,
        *,
        environment: Environment,
        schema: str,
        object_name: str,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> DatabaseColumnCatalogResult:
        try:
            query = DatabaseColumnCatalogQuery(
                schema_name=schema,
                object_name=object_name,
                name_contains=name_contains,
                offset=offset,
                limit=limit,
            )
        except ValidationError:
            raise ToolInputError(
                "list_database_columns input is invalid"
            ) from None
        return await self._metadata.list_columns(
            environment=environment,
            query=query,
        )

    async def describe_database_object(
        self,
        *,
        environment: Environment,
        schema: str,
        object_name: str,
    ) -> DatabaseObjectDescription:
        try:
            reference = DatabaseObjectReference(
                schema_name=schema,
                object_name=object_name,
            )
        except ValidationError:
            raise ToolInputError(
                "describe_database_object input is invalid"
            ) from None
        return await self._metadata.describe_object(
            environment=environment,
            reference=reference,
        )


def _plan_id(plan_id: str, *, tool: str) -> str:
    try:
        request = SqlQueryPlanLookupRequest(plan_id=plan_id)
    except ValidationError:
        raise ToolInputError(f"{tool} input is invalid") from None
    return request.plan_id
