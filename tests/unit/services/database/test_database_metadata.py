"""Tests for policy-aware metadata discovery through ARAPI SQL."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from helix_mcp.clients.arapi import ArapiSqlResult, ArapiSqlValue
from helix_mcp.config import (
    ArapiBackendConfig,
    HelixConfig,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
    TargetPolicyConfig,
)
from helix_mcp.services.database import (
    DatabaseColumnCatalogQuery,
    DatabaseMetadataResponseError,
    DatabaseMetadataService,
    DatabaseObjectCatalogQuery,
    DatabaseObjectKind,
    DatabaseObjectNotAllowedError,
    DatabaseObjectNotFoundError,
    DatabaseObjectReference,
    DatabaseQueryLimitError,
    DatabaseReadDisabledError,
)
from helix_mcp.targeting import ResolvedTarget, TargetRegistry, TargetResolver


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class QueuedMetadataClient:
    def __init__(self, results: Sequence[ArapiSqlResult]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    async def query_sql(self, **kwargs: object) -> ArapiSqlResult:
        self.calls.append(kwargs)
        return self.results.pop(0)


class StaticMetadataProvider:
    def __init__(self, client: QueuedMetadataClient) -> None:
        self.client = client

    def get(self, target: ResolvedTarget) -> QueuedMetadataClient:
        assert target.config.arapi is not None
        return self.client


def build_service(
    *results: ArapiSqlResult,
    allow_sql: bool = True,
    allow_all_objects: bool = True,
    allowed_objects: tuple[str, ...] = (),
    max_rows: int = 10,
) -> tuple[DatabaseMetadataService, QueuedMetadataClient]:
    policy = TargetPolicyConfig(
        name="database_metadata",
        allow_form_reads=False,
        allow_sql=allow_sql,
        allow_all_sql_objects=allow_all_objects if allow_sql else False,
        allowed_sql_objects=allowed_objects,
        max_rows=max_rows,
        query_timeout_seconds=120,
        rate_limit_per_minute=20,
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
    client = QueuedMetadataClient(results)
    return (
        DatabaseMetadataService(resolver, StaticMetadataProvider(client)),
        client,
    )


def result(
    *rows: tuple[ArapiSqlValue, ...],
    truncated: bool = False,
) -> ArapiSqlResult:
    return ArapiSqlResult(rows=rows, truncated=truncated)


def test_lists_filtered_objects_with_escaped_literals() -> None:
    service, client = build_service(
        result(("public", "example_base_element", "view"), truncated=True)
    )

    catalog = run(
        service.list_objects(
            environment="dev",
            query=DatabaseObjectCatalogQuery(
                schema_name="public",
                name_contains="Base'Element",
                kind=DatabaseObjectKind.VIEW,
                offset=5,
                limit=2,
            ),
        )
    )

    assert catalog.objects[0].schema_name == "public"
    assert catalog.objects[0].kind is DatabaseObjectKind.VIEW
    assert catalog.offset == 5
    assert catalog.truncated is True
    call = client.calls[0]
    assert "Base''Element" in str(call["sql"])
    assert call["column_count"] == 3
    assert call["limit"] == 2
    assert call["timeout_seconds"] == 120


def test_lists_columns_for_an_allowlisted_object() -> None:
    service, client = build_service(
        result(("public", "allowed_view", "view")),
        result(
            ("requestid", "character varying(15)", False, 1),
            ("name", "character varying(255)", True, 2),
        ),
        allow_all_objects=False,
        allowed_objects=("public.allowed_view",),
    )

    catalog = run(
        service.list_columns(
            environment="dev",
            query=DatabaseColumnCatalogQuery(
                schema_name="public",
                object_name="allowed_view",
                name_contains="name",
                limit=5,
            ),
        )
    )

    assert [column.name for column in catalog.columns] == ["requestid", "name"]
    assert catalog.columns[1].nullable is True
    assert len(client.calls) == 2
    assert "lower('name')" in str(client.calls[1]["sql"])


@pytest.mark.parametrize(
    ("wire_value", "expected"),
    (
        (0, False),
        (1, True),
        ("0", False),
        ("1", True),
        ("f", False),
        ("t", True),
        ("false", False),
        ("true", True),
    ),
)
def test_normalizes_arapi_boolean_metadata(
    wire_value: ArapiSqlValue,
    expected: bool,
) -> None:
    service, _ = build_service(
        result(("public", "allowed_view", "view")),
        result(("requestid", "character varying(15)", wire_value, 1)),
    )

    catalog = run(
        service.list_columns(
            environment="dev",
            query=DatabaseColumnCatalogQuery(
                schema_name="public",
                object_name="allowed_view",
                limit=5,
            ),
        )
    )

    assert catalog.columns[0].nullable is expected


def test_rejects_unknown_arapi_boolean_metadata() -> None:
    service, _ = build_service(
        result(("public", "allowed_view", "view")),
        result(("requestid", "text", "unknown", 1)),
    )

    with pytest.raises(DatabaseMetadataResponseError):
        run(
            service.list_columns(
                environment="dev",
                query=DatabaseColumnCatalogQuery(
                    schema_name="public",
                    object_name="allowed_view",
                    limit=5,
                ),
            )
        )


def test_unallowlisted_object_is_rejected_before_arapi() -> None:
    service, client = build_service(
        allow_all_objects=False,
        allowed_objects=("public.allowed_view",),
    )

    with pytest.raises(DatabaseObjectNotAllowedError):
        run(
            service.list_columns(
                environment="dev",
                query=DatabaseColumnCatalogQuery(
                    schema_name="public",
                    object_name="secret_table",
                    limit=10,
                ),
            )
        )

    assert client.calls == []


def test_describes_object_and_reports_truncated_columns() -> None:
    service, client = build_service(
        result(("public", "wide_view", "view")),
        result(("requestid", "text", False, 1), truncated=True),
    )

    description = run(
        service.describe_object(
            environment="dev",
            reference=DatabaseObjectReference(
                schema_name="public",
                object_name="wide_view",
            ),
        )
    )

    assert description.object.name == "wide_view"
    assert description.columns[0].name == "requestid"
    assert description.truncated is True
    assert client.calls[1]["limit"] == 10


def test_missing_object_and_disabled_policy_are_sanitized() -> None:
    service, client = build_service(result())

    with pytest.raises(DatabaseObjectNotFoundError):
        run(
            service.describe_object(
                environment="dev",
                reference=DatabaseObjectReference(
                    schema_name="public",
                    object_name="missing",
                ),
            )
        )
    assert len(client.calls) == 1

    locked, locked_client = build_service(allow_sql=False)
    with pytest.raises(DatabaseReadDisabledError):
        run(
            locked.list_objects(
                environment="dev",
                query=DatabaseObjectCatalogQuery(),
            )
        )
    assert locked_client.calls == []


def test_metadata_limit_is_enforced_before_arapi() -> None:
    service, client = build_service(max_rows=2)

    with pytest.raises(DatabaseQueryLimitError):
        run(
            service.list_objects(
                environment="dev",
                query=DatabaseObjectCatalogQuery(limit=3),
            )
        )

    assert client.calls == []
