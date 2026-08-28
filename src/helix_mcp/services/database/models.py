"""Validated database-query inputs and bounded structured outputs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from helix_mcp.clients.arapi import ArapiSqlValue
from helix_mcp.config import Environment
from helix_mcp.config.models import FrozenModel


class DatabaseQuery(FrozenModel):
    sql: str = Field(min_length=1, max_length=16_384, repr=False)
    limit: int = Field(default=100, ge=1, le=100_000)

    @field_validator("sql")
    @classmethod
    def normalize_sql(cls, value: str) -> str:
        value = value.strip()
        if not value or any(
            ord(character) < 0x20 and character not in "\n\r\t"
            for character in value
        ):
            raise ValueError("SQL text is invalid")
        return value


class DatabaseQueryResult(FrozenModel):
    environment: Environment
    columns: tuple[str, ...]
    rows: tuple[dict[str, ArapiSqlValue], ...] = Field(repr=False)
    row_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    truncated: bool

    def __repr__(self) -> str:
        return (
            f"<DatabaseQueryResult environment={self.environment.value} "
            f"columns={len(self.columns)} rows={self.row_count} "
            f"truncated={self.truncated} values=redacted>"
        )


SqlQueryPlanId = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{32}$"),
]
SqlQueryPlanDigest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


class SqlQueryPlanStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SqlQueryPlanResult(FrozenModel):
    plan_id: SqlQueryPlanId
    plan_digest: SqlQueryPlanDigest
    environment: Environment
    sql: str = Field(min_length=1, max_length=16_384, repr=False)
    limit: int = Field(ge=1)
    status: SqlQueryPlanStatus
    server_time: datetime
    expires_at: datetime
    remaining_seconds: int = Field(ge=0)

    def __repr__(self) -> str:
        return (
            f"<SqlQueryPlanResult id={self.plan_id} "
            f"environment={self.environment.value} status={self.status.value} "
            "sql=redacted>"
        )


class ExecuteSqlQueryRequest(FrozenModel):
    plan_id: SqlQueryPlanId
    plan_digest: SqlQueryPlanDigest


class SqlQueryPlanLookupRequest(FrozenModel):
    plan_id: SqlQueryPlanId


class SqlQueryExecutionResult(DatabaseQueryResult):
    plan_id: SqlQueryPlanId
    plan_digest: SqlQueryPlanDigest


class DatabaseObjectKind(StrEnum):
    TABLE = "table"
    PARTITIONED_TABLE = "partitioned_table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    FOREIGN_TABLE = "foreign_table"

    @property
    def relkind(self) -> str:
        return {
            DatabaseObjectKind.TABLE: "r",
            DatabaseObjectKind.PARTITIONED_TABLE: "p",
            DatabaseObjectKind.VIEW: "v",
            DatabaseObjectKind.MATERIALIZED_VIEW: "m",
            DatabaseObjectKind.FOREIGN_TABLE: "f",
        }[self]


class DatabaseObjectCatalogQuery(FrozenModel):
    schema_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    name_contains: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    kind: DatabaseObjectKind | None = None
    include_system: bool = False
    offset: int = Field(default=0, ge=0, le=10_000_000)
    limit: int = Field(default=100, ge=1, le=100_000)

    @field_validator("schema_name", "name_contains")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _normalize_metadata_text(value)


class DatabaseColumnCatalogQuery(FrozenModel):
    schema_name: str = Field(min_length=1, max_length=128)
    object_name: str = Field(min_length=1, max_length=128)
    name_contains: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    offset: int = Field(default=0, ge=0, le=10_000_000)
    limit: int = Field(default=100, ge=1, le=100_000)

    @field_validator("schema_name", "object_name", "name_contains")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return _normalize_metadata_text(value)


class DatabaseObjectReference(FrozenModel):
    schema_name: str = Field(min_length=1, max_length=128)
    object_name: str = Field(min_length=1, max_length=128)

    @field_validator("schema_name", "object_name")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = _normalize_metadata_text(value)
        if normalized is None:
            raise ValueError("database object identity is required")
        return normalized


class DatabaseObjectMetadata(FrozenModel):
    schema_name: str
    name: str
    kind: DatabaseObjectKind


class DatabaseColumnMetadata(FrozenModel):
    name: str
    data_type: str
    nullable: bool
    position: int = Field(ge=1)


class DatabaseObjectCatalogResult(FrozenModel):
    environment: Environment
    objects: tuple[DatabaseObjectMetadata, ...]
    offset: int
    limit: int = Field(ge=1)
    truncated: bool


class DatabaseColumnCatalogResult(FrozenModel):
    environment: Environment
    schema_name: str
    object_name: str
    columns: tuple[DatabaseColumnMetadata, ...]
    offset: int
    limit: int = Field(ge=1)
    truncated: bool


class DatabaseObjectDescription(FrozenModel):
    environment: Environment
    object: DatabaseObjectMetadata
    columns: tuple[DatabaseColumnMetadata, ...]
    truncated: bool


def _normalize_metadata_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or any(
        ord(character) < 0x20 for character in normalized
    ):
        raise ValueError("database metadata text is invalid")
    return normalized
