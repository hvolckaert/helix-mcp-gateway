"""Tests for safe administrator-only SQL execution through ARAPI."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from helix_mcp.clients.arapi import ArapiAdminRequiredError, ArapiSqlResult
from helix_mcp.config import (
    ArapiBackendConfig,
    HelixConfig,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
    TargetKey,
    TargetPolicyConfig,
)
from helix_mcp.services.database import (
    DatabaseObjectNotAllowedError,
    DatabaseQuery,
    DatabaseQueryAliasInvalidError,
    DatabaseQueryAliasRequiredError,
    DatabaseQueryInvalidError,
    DatabaseQueryLimitError,
    DatabaseQueryService,
    DatabaseQueryWildcardNotAllowedError,
)
from helix_mcp.targeting import ResolvedTarget, TargetRegistry, TargetResolver

ALLOWED = "public.allowed_table"


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class FakeSqlClient:
    def __init__(
        self,
        result: ArapiSqlResult,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def query_sql(self, **kwargs: object) -> ArapiSqlResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class StaticProvider:
    def __init__(self, client: FakeSqlClient) -> None:
        self.client = client

    def get(self, target: ResolvedTarget) -> FakeSqlClient:
        assert target.config.arapi is not None
        return self.client


def build_service(
    *,
    rows: tuple[tuple[object, ...], ...] = ((7, "sample"),),
    truncated: bool = False,
    max_rows: int = 10,
    allow_all_objects: bool = False,
    error: Exception | None = None,
) -> tuple[DatabaseQueryService, FakeSqlClient]:
    policy = TargetPolicyConfig(
        name="database_read",
        allow_form_reads=False,
        allow_sql=True,
        allow_all_sql_objects=allow_all_objects,
        allowed_sql_objects=() if allow_all_objects else (ALLOWED,),
        max_rows=max_rows,
        query_timeout_seconds=120,
        rate_limit_per_minute=10,
    )
    target = TargetConfig(
        environment="dev",
        display_name="Helix DEV",
        policy_ref=policy.name,
        arapi=ArapiBackendConfig(
            bridge_base_url="http://127.0.0.1:8090",
            gateway_port=46_000,
            credentials=SecretRef(
                provider=SecretProviderKind.ENVIRONMENT,
                key="HELIX_CREDENTIAL_DEV",
            ),
        ),
    )
    resolver = TargetResolver(
        TargetRegistry(HelixConfig(policies=(policy,), targets=(target,)))
    )
    client = FakeSqlClient(
        ArapiSqlResult(rows=rows, truncated=truncated),
        error=error,
    )
    return DatabaseQueryService(resolver, StaticProvider(client)), client


def test_select_is_bounded_mapped_and_uses_policy_timeout() -> None:
    service, client = build_service(truncated=True)
    sql = "SELECT id AS id, name AS name FROM public.allowed_table"

    result = run(
        service.query(
            environment="dev",
            query=DatabaseQuery(sql=sql, limit=1),
        )
    )

    assert result.columns == ("id", "name")
    assert result.rows == ({"id": 7, "name": "sample"},)
    assert result.row_count == 1
    assert result.truncated is True
    assert client.calls == [
        {
            "sql": sql,
            "column_count": 2,
            "limit": 1,
            "timeout_seconds": 120,
        }
    ]
    assert "sample" not in repr(result)


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("DELETE FROM public.allowed_table", DatabaseQueryInvalidError),
        (
            "SELECT * FROM public.allowed_table",
            DatabaseQueryWildcardNotAllowedError,
        ),
        (
            "SELECT id FROM public.allowed_table",
            DatabaseQueryAliasRequiredError,
        ),
        (
            "SELECT id AS value, name AS value FROM public.allowed_table",
            DatabaseQueryAliasInvalidError,
        ),
        (
            "SELECT id AS id FROM public.allowed_table FOR UPDATE",
            DatabaseQueryInvalidError,
        ),
        (
            "SELECT id AS id FROM public.allowed_table WHERE id = %s",
            DatabaseQueryInvalidError,
        ),
        (
            "SELECT pg_sleep(1) AS waited FROM public.allowed_table",
            DatabaseQueryInvalidError,
        ),
        (
            "SELECT id AS id FROM public.allowed_table; SELECT 1 AS value",
            DatabaseQueryInvalidError,
        ),
        (
            "SELECT id AS id FROM public.other_table",
            DatabaseObjectNotAllowedError,
        ),
    ],
)
def test_unsafe_queries_are_rejected_before_arapi(
    sql: str,
    error: type[Exception],
) -> None:
    service, client = build_service()

    with pytest.raises(error):
        run(
            service.query(
                environment="dev",
                query=DatabaseQuery(sql=sql, limit=1),
            )
        )

    assert client.calls == []


def test_policy_row_limit_is_enforced_before_arapi() -> None:
    service, client = build_service(max_rows=2)

    with pytest.raises(DatabaseQueryLimitError):
        run(
            service.query(
                environment="dev",
                query=DatabaseQuery(
                    sql="SELECT id AS id FROM public.allowed_table",
                    limit=3,
                ),
            )
        )

    assert client.calls == []


def test_allow_all_sql_objects_accepts_an_unlisted_table() -> None:
    service, client = build_service(
        allow_all_objects=True,
        rows=((7,),),
    )
    result = run(
        service.query(
            environment="dev",
            query=DatabaseQuery(
                sql="SELECT id AS id FROM any_schema.any_table",
                limit=1,
            ),
        )
    )

    assert result.row_count == 1
    assert len(client.calls) == 1


def test_count_star_with_an_explicit_alias_is_allowed() -> None:
    service, client = build_service(rows=((2,),))
    sql = "SELECT COUNT(*) AS row_count FROM public.allowed_table"

    result = run(
        service.query(
            environment="dev",
            query=DatabaseQuery(sql=sql, limit=1),
        )
    )

    assert result.columns == ("row_count",)
    assert result.rows == ({"row_count": 2},)
    assert client.calls[0]["column_count"] == 1


def test_sql_query_requires_a_plan_before_execution() -> None:
    service, client = build_service(rows=((7,),))
    query = DatabaseQuery(
        sql="SELECT id AS id FROM public.allowed_table",
        limit=1,
    )

    plan = run(service.plan(environment="dev", query=query))
    assert plan.sql == query.sql
    assert plan.status.value == "pending"
    assert client.calls == []

    result = run(
        service.execute_plan(
            environment="dev",
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
        )
    )
    assert result.plan_id == plan.plan_id
    assert result.row_count == 1
    assert len(client.calls) == 1


def test_non_admin_failure_is_propagated_with_a_stable_code() -> None:
    error = ArapiAdminRequiredError(
        TargetKey(environment="dev"),
        "administrator permission is required",
        status_code=403,
    )
    service, _ = build_service(error=error)

    with pytest.raises(ArapiAdminRequiredError) as exc_info:
        run(
            service.query(
                environment="dev",
                query=DatabaseQuery(
                    sql="SELECT id AS id FROM public.allowed_table",
                    limit=1,
                ),
            )
        )

    assert exc_info.value.code == "ARAPI_ADMIN_REQUIRED"
