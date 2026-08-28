"""Policy-aware database metadata discovery through BMC ARAPI SQL."""

from __future__ import annotations

from typing import Protocol

from helix_mcp.clients.arapi import ArapiSqlResult, ArapiSqlValue
from helix_mcp.config import BackendKind, Environment
from helix_mcp.services.database.errors import (
    DatabaseMetadataResponseError,
    DatabaseObjectNotAllowedError,
    DatabaseObjectNotFoundError,
    DatabaseQueryLimitError,
    DatabaseReadDisabledError,
)
from helix_mcp.services.database.limiter import DatabaseRateLimiter
from helix_mcp.services.database.models import (
    DatabaseColumnCatalogQuery,
    DatabaseColumnCatalogResult,
    DatabaseColumnMetadata,
    DatabaseObjectCatalogQuery,
    DatabaseObjectCatalogResult,
    DatabaseObjectDescription,
    DatabaseObjectKind,
    DatabaseObjectMetadata,
    DatabaseObjectReference,
)
from helix_mcp.targeting import ResolvedTarget, TargetResolver

_OBJECT_SELECT = """
SELECT
    n.nspname AS schema_name,
    c.relname AS object_name,
    CASE c.relkind
        WHEN 'r' THEN 'table'
        WHEN 'p' THEN 'partitioned_table'
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'materialized_view'
        WHEN 'f' THEN 'foreign_table'
    END AS object_kind
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
""".strip()

_COLUMNS_SELECT = """
SELECT
    a.attname AS column_name,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
    NOT a.attnotnull AS nullable,
    a.attnum AS ordinal_position
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
""".strip()


class ArapiMetadataClient(Protocol):
    async def query_sql(
        self,
        *,
        sql: str,
        column_count: int,
        limit: int,
        timeout_seconds: int,
    ) -> ArapiSqlResult: ...


class ArapiMetadataClientProvider(Protocol):
    def get(self, target: ResolvedTarget) -> ArapiMetadataClient: ...


class DatabaseMetadataService:
    __slots__ = ("_clients", "_limiter", "_targets")

    def __init__(
        self,
        targets: TargetResolver,
        clients: ArapiMetadataClientProvider,
        *,
        limiter: DatabaseRateLimiter | None = None,
    ) -> None:
        self._targets = targets
        self._clients = clients
        self._limiter = limiter or DatabaseRateLimiter()

    async def list_objects(
        self,
        *,
        environment: str | Environment,
        query: DatabaseObjectCatalogQuery,
    ) -> DatabaseObjectCatalogResult:
        target = self._resolve_target(environment)
        _validate_limit(query.limit, target.policy.max_rows)
        await self._check_rate(target)
        sql = _build_objects_query(target, query)
        if sql is None:
            return DatabaseObjectCatalogResult(
                environment=target.key.environment,
                objects=(),
                offset=query.offset,
                limit=query.limit,
                truncated=False,
            )
        result = await self._query(
            target,
            sql=sql,
            columns=("schema_name", "object_name", "object_kind"),
            limit=query.limit,
        )
        return DatabaseObjectCatalogResult(
            environment=target.key.environment,
            objects=tuple(_object_from_row(row) for row in result[0]),
            offset=query.offset,
            limit=query.limit,
            truncated=result[1],
        )

    async def list_columns(
        self,
        *,
        environment: str | Environment,
        query: DatabaseColumnCatalogQuery,
    ) -> DatabaseColumnCatalogResult:
        target = self._resolve_target(environment)
        _validate_limit(query.limit, target.policy.max_rows)
        _ensure_object_permitted(
            target,
            schema=query.schema_name,
            object_name=query.object_name,
        )
        await self._check_rate(target)
        await self._resolve_object(
            target,
            schema=query.schema_name,
            object_name=query.object_name,
        )
        columns, truncated = await self._load_columns(
            target,
            schema=query.schema_name,
            object_name=query.object_name,
            name_contains=query.name_contains,
            offset=query.offset,
            limit=query.limit,
        )
        return DatabaseColumnCatalogResult(
            environment=target.key.environment,
            schema_name=query.schema_name,
            object_name=query.object_name,
            columns=columns,
            offset=query.offset,
            limit=query.limit,
            truncated=truncated,
        )

    async def describe_object(
        self,
        *,
        environment: str | Environment,
        reference: DatabaseObjectReference,
    ) -> DatabaseObjectDescription:
        target = self._resolve_target(environment)
        _ensure_object_permitted(
            target,
            schema=reference.schema_name,
            object_name=reference.object_name,
        )
        await self._check_rate(target)
        metadata = await self._resolve_object(
            target,
            schema=reference.schema_name,
            object_name=reference.object_name,
        )
        columns, truncated = await self._load_columns(
            target,
            schema=reference.schema_name,
            object_name=reference.object_name,
            name_contains=None,
            offset=0,
            limit=target.policy.max_rows,
        )
        return DatabaseObjectDescription(
            environment=target.key.environment,
            object=metadata,
            columns=columns,
            truncated=truncated,
        )

    def _resolve_target(
        self,
        environment: str | Environment,
    ) -> ResolvedTarget:
        target = self._targets.resolve(
            environment=environment,
            backend=BackendKind.ARAPI,
        )
        if not target.policy.allow_sql:
            raise DatabaseReadDisabledError(
                "database metadata reads are disabled by target policy"
            )
        return target

    async def _check_rate(self, target: ResolvedTarget) -> None:
        await self._limiter.check(
            target.key,
            target.policy.rate_limit_per_minute,
        )

    async def _query(
        self,
        target: ResolvedTarget,
        *,
        sql: str,
        columns: tuple[str, ...],
        limit: int,
    ) -> tuple[tuple[dict[str, ArapiSqlValue], ...], bool]:
        result = await self._clients.get(target).query_sql(
            sql=sql,
            column_count=len(columns),
            limit=limit,
            timeout_seconds=target.policy.query_timeout_seconds,
        )
        return (
            tuple(dict(zip(columns, row, strict=True)) for row in result.rows),
            result.truncated,
        )

    async def _resolve_object(
        self,
        target: ResolvedTarget,
        *,
        schema: str,
        object_name: str,
    ) -> DatabaseObjectMetadata:
        sql = "\n".join(
            (
                _OBJECT_SELECT,
                f"  AND n.nspname = {_sql_literal(schema)}",
                f"  AND c.relname = {_sql_literal(object_name)}",
            )
        )
        rows, _ = await self._query(
            target,
            sql=sql,
            columns=("schema_name", "object_name", "object_kind"),
            limit=1,
        )
        if not rows:
            raise DatabaseObjectNotFoundError("database object was not found")
        return _object_from_row(rows[0])

    async def _load_columns(
        self,
        target: ResolvedTarget,
        *,
        schema: str,
        object_name: str,
        name_contains: str | None,
        offset: int,
        limit: int,
    ) -> tuple[tuple[DatabaseColumnMetadata, ...], bool]:
        clauses = [
            _COLUMNS_SELECT,
            f"  AND n.nspname = {_sql_literal(schema)}",
            f"  AND c.relname = {_sql_literal(object_name)}",
            "  AND a.attnum > 0",
            "  AND NOT a.attisdropped",
        ]
        if name_contains is not None:
            clauses.append(
                "  AND strpos(lower(a.attname), lower("
                f"{_sql_literal(name_contains)})) > 0"
            )
        clauses.extend(("ORDER BY a.attnum", f"OFFSET {offset}"))
        rows, truncated = await self._query(
            target,
            sql="\n".join(clauses),
            columns=(
                "column_name",
                "data_type",
                "nullable",
                "ordinal_position",
            ),
            limit=limit,
        )
        return tuple(_column_from_row(row) for row in rows), truncated


def _build_objects_query(
    target: ResolvedTarget,
    query: DatabaseObjectCatalogQuery,
) -> str | None:
    clauses = [_OBJECT_SELECT]
    if not query.include_system:
        clauses.extend(
            (
                "  AND n.nspname <> 'pg_catalog'",
                "  AND n.nspname <> 'information_schema'",
                "  AND n.nspname NOT LIKE 'pg_toast%'",
                "  AND n.nspname NOT LIKE 'pg_temp_%'",
            )
        )
    if query.schema_name is not None:
        clauses.append(f"  AND n.nspname = {_sql_literal(query.schema_name)}")
    if query.name_contains is not None:
        clauses.append(
            "  AND strpos(lower(c.relname), lower("
            f"{_sql_literal(query.name_contains)})) > 0"
        )
    if query.kind is not None:
        clauses.append(f"  AND c.relkind = {_sql_literal(query.kind.relkind)}")
    if not target.policy.allow_all_sql_objects:
        unqualified, qualified = _allowed_object_names(target)
        filters: list[str] = []
        if unqualified:
            values = ", ".join(_sql_literal(item) for item in unqualified)
            filters.append(f"lower(c.relname) IN ({values})")
        if qualified:
            values = ", ".join(_sql_literal(item) for item in qualified)
            filters.append(
                f"lower(n.nspname || '.' || c.relname) IN ({values})"
            )
        if not filters:
            return None
        clauses.append("  AND (" + " OR ".join(filters) + ")")
    clauses.extend(("ORDER BY n.nspname, c.relname", f"OFFSET {query.offset}"))
    return "\n".join(clauses)


def _allowed_object_names(
    target: ResolvedTarget,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    unqualified: set[str] = set()
    qualified: set[str] = set()
    for raw_name in target.policy.allowed_sql_objects:
        parts = tuple(part.casefold() for part in raw_name.split("."))
        if len(parts) == 1:
            unqualified.add(parts[0])
        else:
            qualified.add(".".join(parts[-2:]))
    return tuple(sorted(unqualified)), tuple(sorted(qualified))


def _ensure_object_permitted(
    target: ResolvedTarget,
    *,
    schema: str,
    object_name: str,
) -> None:
    if target.policy.allow_all_sql_objects:
        return
    unqualified, qualified = _allowed_object_names(target)
    if (
        object_name.casefold() not in unqualified
        and f"{schema}.{object_name}".casefold() not in qualified
    ):
        raise DatabaseObjectNotAllowedError(
            "database object is outside the target policy"
        )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validate_limit(requested: int, maximum: int) -> None:
    if requested > maximum:
        raise DatabaseQueryLimitError(
            "database metadata limit exceeds the target policy"
        )


def _object_from_row(
    row: dict[str, ArapiSqlValue],
) -> DatabaseObjectMetadata:
    return DatabaseObjectMetadata(
        schema_name=_require_str(row, "schema_name"),
        name=_require_str(row, "object_name"),
        kind=DatabaseObjectKind(_require_str(row, "object_kind")),
    )


def _column_from_row(
    row: dict[str, ArapiSqlValue],
) -> DatabaseColumnMetadata:
    return DatabaseColumnMetadata(
        name=_require_str(row, "column_name"),
        data_type=_require_str(row, "data_type"),
        nullable=_require_bool(row, "nullable"),
        position=_require_int(row, "ordinal_position"),
    )


def _require_str(row: dict[str, ArapiSqlValue], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise DatabaseMetadataResponseError(
            "database metadata response is invalid"
        )
    return value


def _require_bool(row: dict[str, ArapiSqlValue], key: str) -> bool:
    value = row.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "t", "true"}:
            return True
        if normalized in {"0", "f", "false"}:
            return False
    raise DatabaseMetadataResponseError(
        "database metadata response is invalid"
    )


def _require_int(row: dict[str, ArapiSqlValue], key: str) -> int:
    value = row.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise DatabaseMetadataResponseError(
            "database metadata response is invalid"
        )
    return value
