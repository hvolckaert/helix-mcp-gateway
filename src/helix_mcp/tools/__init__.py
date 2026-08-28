"""MCP tool adapters."""

from helix_mcp.tools.database import DatabaseToolAdapter
from helix_mcp.tools.errors import (
    ToolAdapterError,
    ToolExecutionError,
    ToolInputError,
)
from helix_mcp.tools.forms import FormToolAdapter
from helix_mcp.tools.health import HealthToolAdapter
from helix_mcp.tools.models import (
    ApplyWriteOutput,
    DescribeDatabaseObjectOutput,
    ExecuteSqlQueryOutput,
    GetEntryOutput,
    HealthCheckOutput,
    ListDatabaseColumnsOutput,
    ListDatabaseObjectsOutput,
    ListFormFieldsOutput,
    ListFormsOutput,
    ListTargetsOutput,
    QueryFormOutput,
    SqlQueryPlanOutput,
    WritePlanOutput,
)
from helix_mcp.tools.registration import register_mcp_tools
from helix_mcp.tools.targets import TargetToolAdapter
from helix_mcp.tools.writes import FormWriteToolAdapter

__all__ = [
    "ApplyWriteOutput",
    "DatabaseToolAdapter",
    "DescribeDatabaseObjectOutput",
    "ExecuteSqlQueryOutput",
    "FormToolAdapter",
    "FormWriteToolAdapter",
    "GetEntryOutput",
    "HealthCheckOutput",
    "HealthToolAdapter",
    "ListDatabaseColumnsOutput",
    "ListDatabaseObjectsOutput",
    "ListFormFieldsOutput",
    "ListFormsOutput",
    "ListTargetsOutput",
    "QueryFormOutput",
    "SqlQueryPlanOutput",
    "TargetToolAdapter",
    "ToolAdapterError",
    "ToolExecutionError",
    "ToolInputError",
    "WritePlanOutput",
    "register_mcp_tools",
]
