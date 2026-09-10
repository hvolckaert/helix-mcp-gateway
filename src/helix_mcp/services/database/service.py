"""Policy-enforced SQL reads executed through BMC ARAPI."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import sqlglot
from sqlglot import TokenType, exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import ParseError, TokenError
from sqlglot.parsers.postgres import PostgresParser

from helix_mcp.clients.arapi import ArapiSqlResult
from helix_mcp.config import BackendKind, Environment, TargetKey
from helix_mcp.services.database.errors import (
    DatabaseObjectNotAllowedError,
    DatabaseQueryAliasInvalidError,
    DatabaseQueryAliasRequiredError,
    DatabaseQueryInvalidError,
    DatabaseQueryLimitError,
    DatabaseQueryWildcardNotAllowedError,
    DatabaseReadDisabledError,
)
from helix_mcp.services.database.limiter import DatabaseRateLimiter
from helix_mcp.services.database.models import (
    DatabaseQuery,
    DatabaseQueryResult,
    SqlQueryExecutionResult,
    SqlQueryPlanResult,
)
from helix_mcp.services.database.plans import (
    SqlQueryPlanStore,
    StoredSqlQueryPlan,
)
from helix_mcp.targeting import ResolvedTarget, TargetResolver

_OBJECT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_$]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$"
)
_COLUMN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_ALLOWED_FUNCTIONS = frozenset(
    {
        "abs",
        "avg",
        "ceil",
        "ceiling",
        "char_length",
        "coalesce",
        "count",
        "date_part",
        "extract",
        "floor",
        "greatest",
        "least",
        "length",
        "lower",
        "max",
        "min",
        "nullif",
        "round",
        "string_agg",
        "substring",
        "sum",
        "trim",
        "upper",
    }
)
_ALLOWED_FUNCTION_CALLS = _ALLOWED_FUNCTIONS | {"cast"}
_ALLOWED_PARENTHESIZED_SYNTAX = frozenset({"all", "any", "some"})


class _ValidationPostgresParser(PostgresParser):
    """PostgreSQL parser retaining every parenthesized callable spelling."""

    def _parse_function_call(
        self,
        functions: dict[str, Callable[..., Any]] | None = None,
        anonymous: bool = False,
        optional_parens: bool = True,
        any_token: bool = False,
    ) -> exp.Expr | None:
        token = self._curr
        after_dot = self._prev.token_type is TokenType.DOT
        parenthesized = self._next.token_type is TokenType.L_PAREN
        function_candidate = parenthesized and (
            (any_token and token.token_type not in self.RESERVED_TOKENS)
            or (not any_token and token.token_type in self.FUNC_TOKENS)
        )
        if function_candidate and (
            after_dot
            or token.token_type is TokenType.IDENTIFIER
            or token.text.casefold()
            not in (_ALLOWED_FUNCTION_CALLS | _ALLOWED_PARENTHESIZED_SYNTAX)
        ):
            raise DatabaseQueryInvalidError(
                "SQL contains a function outside the read-only allowlist"
            )
        return super()._parse_function_call(
            functions=functions,
            anonymous=anonymous,
            optional_parens=optional_parens,
            any_token=any_token,
        )


class _ValidationPostgres(Postgres):
    """PostgreSQL dialect retaining original names for allowlist checks."""

    PRESERVE_ORIGINAL_NAMES = True
    Parser = _ValidationPostgresParser


class ArapiSqlClient(Protocol):
    async def query_sql(
        self,
        *,
        sql: str,
        column_count: int,
        limit: int,
        timeout_seconds: int,
    ) -> ArapiSqlResult: ...


class ArapiSqlClientProvider(Protocol):
    def get(self, target: ResolvedTarget) -> ArapiSqlClient: ...


class SqlPlanProvider(Protocol):
    async def create(
        self, *, target: TargetKey, query: DatabaseQuery
    ) -> SqlQueryPlanResult: ...

    async def acquire(
        self, *, plan_id: str, plan_digest: str, target: TargetKey
    ) -> StoredSqlQueryPlan: ...

    async def complete(self, plan_id: str) -> None: ...

    async def fail(self, plan_id: str) -> None: ...

    async def get(
        self, *, plan_id: str, target: TargetKey
    ) -> SqlQueryPlanResult: ...

    async def cancel(
        self, *, plan_id: str, target: TargetKey
    ) -> SqlQueryPlanResult: ...


class DatabaseQueryService:
    __slots__ = ("_clients", "_limiter", "_plans", "_targets")

    def __init__(
        self,
        targets: TargetResolver,
        clients: ArapiSqlClientProvider,
        *,
        time_source: Callable[[], float] | None = None,
        limiter: DatabaseRateLimiter | None = None,
        plans: SqlPlanProvider | None = None,
    ) -> None:
        self._targets = targets
        self._clients = clients
        if limiter is not None and time_source is not None:
            raise ValueError("limiter and time_source are mutually exclusive")
        self._limiter = limiter or DatabaseRateLimiter(time_source)
        self._plans = plans or SqlQueryPlanStore(
            ttl_seconds=600,
            max_pending=100,
        )

    async def plan(
        self,
        *,
        environment: str | Environment,
        query: DatabaseQuery,
    ) -> SqlQueryPlanResult:
        target, _ = self._validate(environment=environment, query=query)
        return await self._plans.create(target=target.key, query=query)

    async def execute_plan(
        self,
        *,
        environment: str | Environment,
        plan_id: str,
        plan_digest: str,
    ) -> SqlQueryExecutionResult:
        target = self._resolve_target(environment)
        acquired = await self._plans.acquire(
            plan_id=plan_id,
            plan_digest=plan_digest,
            target=target.key,
        )
        try:
            result = await self.query(
                environment=environment,
                query=acquired.query,
            )
        except Exception:
            await self._plans.fail(plan_id)
            raise
        await self._plans.complete(plan_id)
        return SqlQueryExecutionResult(
            plan_id=plan_id,
            plan_digest=plan_digest,
            **result.model_dump(),
        )

    async def get_plan(
        self,
        *,
        environment: str | Environment,
        plan_id: str,
    ) -> SqlQueryPlanResult:
        target = self._resolve_target(environment)
        return await self._plans.get(plan_id=plan_id, target=target.key)

    async def cancel_plan(
        self,
        *,
        environment: str | Environment,
        plan_id: str,
    ) -> SqlQueryPlanResult:
        target = self._resolve_target(environment)
        return await self._plans.cancel(plan_id=plan_id, target=target.key)

    async def query(
        self,
        *,
        environment: str | Environment,
        query: DatabaseQuery,
    ) -> DatabaseQueryResult:
        target, columns = self._validate(
            environment=environment,
            query=query,
        )
        await self._limiter.check(
            target.key,
            target.policy.rate_limit_per_minute,
        )
        result = await self._clients.get(target).query_sql(
            sql=query.sql,
            column_count=len(columns),
            limit=query.limit,
            timeout_seconds=target.policy.query_timeout_seconds,
        )
        rows = tuple(
            dict(zip(columns, row, strict=True)) for row in result.rows
        )
        return DatabaseQueryResult(
            environment=target.key.environment,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            limit=query.limit,
            truncated=result.truncated,
        )

    def _resolve_target(
        self,
        environment: str | Environment,
    ) -> ResolvedTarget:
        return self._targets.resolve(
            environment=environment,
            backend=BackendKind.ARAPI,
        )

    def _validate(
        self,
        *,
        environment: str | Environment,
        query: DatabaseQuery,
    ) -> tuple[ResolvedTarget, tuple[str, ...]]:
        target = self._resolve_target(environment)
        if not target.policy.allow_sql:
            raise DatabaseReadDisabledError(
                "SQL reads are disabled by target policy"
            )
        if query.limit > target.policy.max_rows:
            raise DatabaseQueryLimitError(
                "SQL row limit exceeds the target policy"
            )
        columns = _validate_read_query(
            query.sql,
            allow_all_objects=target.policy.allow_all_sql_objects,
            allowed_objects=target.policy.allowed_sql_objects,
        )
        return target, columns


def _validate_read_query(
    sql: str,
    *,
    allow_all_objects: bool,
    allowed_objects: Sequence[str],
) -> tuple[str, ...]:
    if ";" in sql or "$" in sql or "--" in sql or "/*" in sql or "*/" in sql:
        raise DatabaseQueryInvalidError(
            "SQL comments, dollar quoting, and semicolons are not allowed"
        )
    try:
        statements = sqlglot.parse(sql, read=_ValidationPostgres)
    except (ParseError, TokenError):
        raise DatabaseQueryInvalidError("SQL syntax is invalid") from None
    if (
        len(statements) != 1
        or statements[0] is None
        or not isinstance(statements[0], exp.Query)
    ):
        raise DatabaseQueryInvalidError(
            "exactly one read-only SQL statement is required"
        )

    statement = statements[0]
    forbidden_nodes = (
        exp.Delete,
        exp.Insert,
        exp.Into,
        exp.Lock,
        exp.Merge,
        exp.Update,
    )
    if any(
        statement.find(node_type) is not None for node_type in forbidden_nodes
    ):
        raise DatabaseQueryInvalidError(
            "SQL data changes, SELECT INTO, and row locking are not allowed"
        )
    if any(isinstance(node, exp.Placeholder) for node in statement.walk()):
        raise DatabaseQueryInvalidError(
            "SQL parameters are not supported by ARAPI; use reviewed literals"
        )
    if statement.find(exp.Operator) is not None:
        raise DatabaseQueryInvalidError("SQL custom operators are not allowed")
    for function in statement.find_all(exp.Func):
        function_name = _sql_function_name(function)
        if function_name is None:
            continue
        if function_name.casefold() not in _ALLOWED_FUNCTIONS:
            raise DatabaseQueryInvalidError(
                "SQL contains a function outside the read-only allowlist"
            )

    columns = _output_columns(statement)
    allowed = {_normalize_allowed_object(item) for item in allowed_objects}
    cte_names = {
        cte.alias_or_name.casefold() for cte in statement.find_all(exp.CTE)
    }
    referenced: set[str] = set()
    for table in statement.find_all(exp.Table):
        if (
            not table.db
            and not table.catalog
            and table.name.casefold() in cte_names
        ):
            continue
        parts = [
            part for part in (table.catalog, table.db, table.name) if part
        ]
        referenced.add(".".join(parts).casefold())
    if not referenced:
        raise DatabaseQueryInvalidError(
            "SQL queries must reference an allowed object"
        )
    if not allow_all_objects and not referenced.issubset(allowed):
        raise DatabaseObjectNotAllowedError(
            "SQL query references an object outside the allowlist"
        )
    return columns


def _sql_function_name(function: exp.Func) -> str | None:
    """Return a callable SQL function name, excluding structural AST nodes."""
    original_name = function.meta.get("name")
    if isinstance(original_name, str):
        return original_name
    if type(function) in {exp.And, exp.Case, exp.Or}:
        return None
    if (
        type(function) is exp.If
        and type(function.parent) is exp.Case
        and function.arg_key == "ifs"
    ):
        return None
    if type(function) is exp.Cast:
        target = function.args.get("to")
        if isinstance(target, exp.DataType) and not any(
            isinstance(node, exp.DataType)
            and node.this is exp.DataType.Type.USERDEFINED
            for node in target.walk()
        ):
            return None
    if type(function) is exp.GroupConcat:
        # Non-STRING_AGG spellings are rejected before AST normalization.
        return "string_agg"
    if isinstance(function, exp.Anonymous):
        # Every allowlisted call has an exact spelling check and a typed node.
        return function.sql_name()
    return function.sql_name()


def _output_columns(statement: exp.Query) -> tuple[str, ...]:
    selected = tuple(statement.selects)
    if not selected or len(selected) > 128:
        raise DatabaseQueryInvalidError(
            "SQL must select between 1 and 128 columns"
        )
    columns: list[str] = []
    folded: set[str] = set()
    for expression in selected:
        if _has_forbidden_output_wildcard(expression):
            raise DatabaseQueryWildcardNotAllowedError(
                "SQL output wildcards are not allowed"
            )
        if not isinstance(expression, exp.Alias):
            raise DatabaseQueryAliasRequiredError(
                "every selected expression requires an explicit alias"
            )
        alias = expression.alias
        if not _COLUMN_PATTERN.fullmatch(alias) or alias.casefold() in folded:
            raise DatabaseQueryAliasInvalidError(
                "SQL output aliases must be unique safe identifiers"
            )
        folded.add(alias.casefold())
        columns.append(alias)
    return tuple(columns)


def _has_forbidden_output_wildcard(expression: exp.Expr) -> bool:
    return any(
        not isinstance(wildcard.parent, exp.Count)
        for wildcard in expression.find_all(exp.Star)
    )


def _normalize_allowed_object(value: str) -> str:
    normalized = value.strip()
    if not _OBJECT_PATTERN.fullmatch(normalized):
        raise DatabaseObjectNotAllowedError(
            "database object allowlist contains an invalid name"
        )
    return normalized.casefold()
