"""Structured MCP tool results."""

from __future__ import annotations

from helix_mcp.config import Environment
from helix_mcp.config.models import FrozenModel
from helix_mcp.services.database import (
    DatabaseColumnCatalogResult,
    DatabaseObjectCatalogResult,
    DatabaseObjectDescription,
    SqlQueryExecutionResult,
    SqlQueryPlanResult,
)
from helix_mcp.services.forms import (
    FormEntry,
    FormFieldMetadata,
    FormMetadata,
)
from helix_mcp.services.health import HealthCheckResult
from helix_mcp.services.writes import ApplyWriteResult, WritePlanResult
from helix_mcp.targeting import TargetDescriptor

HealthCheckOutput = HealthCheckResult
WritePlanOutput = WritePlanResult
ApplyWriteOutput = ApplyWriteResult
SqlQueryPlanOutput = SqlQueryPlanResult
ExecuteSqlQueryOutput = SqlQueryExecutionResult
ListDatabaseObjectsOutput = DatabaseObjectCatalogResult
ListDatabaseColumnsOutput = DatabaseColumnCatalogResult
DescribeDatabaseObjectOutput = DatabaseObjectDescription


class ListTargetsOutput(FrozenModel):
    """Safe target catalog without endpoints or secret references."""

    targets: tuple[TargetDescriptor, ...]


class QueryFormOutput(FrozenModel):
    """Structured, bounded result from one explicit environment."""

    environment: Environment
    form: str
    entries: tuple[FormEntry, ...]
    offset: int
    limit: int
    total: int | None = None

    def __repr__(self) -> str:
        return (
            f"<QueryFormOutput environment={self.environment.value} "
            f"form={self.form!r} entries={len(self.entries)} "
            "values=redacted>"
        )


class GetEntryOutput(FrozenModel):
    """One directly addressed entry from an explicit environment."""

    environment: Environment
    form: str
    entry_id: str
    entry: FormEntry

    def __repr__(self) -> str:
        return (
            f"<GetEntryOutput environment={self.environment.value} "
            f"form={self.form!r} entry_id={self.entry_id!r} "
            f"fields={len(self.entry.values)} values=redacted>"
        )


class ListFormFieldsOutput(FrozenModel):
    """Safe, paginated field metadata for one form."""

    environment: Environment
    form: str
    fields: tuple[FormFieldMetadata, ...]
    offset: int
    limit: int
    total: int


class ListFormsOutput(FrozenModel):
    """Safe, paginated catalog of accessible forms."""

    environment: Environment
    forms: tuple[FormMetadata, ...]
    offset: int
    limit: int
    total: int
