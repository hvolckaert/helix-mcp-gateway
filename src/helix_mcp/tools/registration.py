"""Register application adapters as MCP tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from helix_mcp.config import Environment
from helix_mcp.observability import ToolAuditor
from helix_mcp.services.database import DatabaseObjectKind
from helix_mcp.services.forms import FormSort
from helix_mcp.services.writes import JsonScalar
from helix_mcp.tools.database import DatabaseToolAdapter
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
from helix_mcp.tools.targets import TargetToolAdapter
from helix_mcp.tools.writes import FormWriteToolAdapter

_CATALOG_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_HELIX_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
_WRITE_PLAN_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
_WRITE_PLAN_EXTERNAL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
_WRITE_APPLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)
_WRITE_PLAN_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_SQL_PLAN_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
_SQL_EXECUTE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


def register_mcp_tools(
    server: FastMCP,
    *,
    targets: TargetToolAdapter,
    forms: FormToolAdapter,
    database: DatabaseToolAdapter,
    health: HealthToolAdapter,
    writes: FormWriteToolAdapter,
    audit: ToolAuditor,
) -> None:
    """Register thin, typed functions without business rules."""

    @server.tool(
        name="list_targets",
        title="List Helix targets",
        description=(
            "List the fixed BMC Helix environments. "
            "Returns safe capabilities only; endpoints and credentials "
            "are never exposed."
        ),
        annotations=_CATALOG_ANNOTATIONS,
        structured_output=True,
    )
    async def list_targets(
        include_disabled: bool = False,
    ) -> ListTargetsOutput:
        return await audit.execute(
            tool="list_targets",
            operation=lambda: targets.list_targets(
                include_disabled=include_disabled,
            ),
        )

    @server.tool(
        name="health_check",
        title="Check Helix target health",
        description=(
            "Check the local ARAPI bridge, Kaazing "
            "connectivity for one "
            "explicit target. Returns only sanitized states, latencies, and "
            "error codes."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def health_check(
        environment: Environment,
        force_refresh: bool = False,
    ) -> HealthCheckOutput:
        return await audit.execute(
            tool="health_check",
            environment=environment,
            operation=lambda: health.health_check(
                environment=environment,
                force_refresh=force_refresh,
            ),
        )

    @server.tool(
        name="plan_sql_query",
        title="Plan a Helix ARAPI SQL query",
        description=(
            "Validate and persist one bounded, read-only ARAPI SQL query "
            "without executing it. Show the returned SQL, "
            "limit, environment, and digest to the user and wait for explicit "
            "approval in a later message before execute_sql_query. Treat "
            "status and remaining_seconds as authoritative; timestamps ending "
            "in Z are UTC. Every selected expression needs an explicit unique "
            "alias. Bound parameters are unavailable, so reviewed literals "
            "must be visible in SQL. Execution requires an AR System "
            "administrator account."
        ),
        annotations=_SQL_PLAN_ANNOTATIONS,
        structured_output=True,
    )
    async def plan_sql_query(
        environment: Environment,
        sql: str,
        limit: int = 100,
    ) -> SqlQueryPlanOutput:
        return await audit.execute(
            tool="plan_sql_query",
            environment=environment,
            operation=lambda: database.plan_sql_query(
                environment=environment,
                sql=sql,
                limit=limit,
            ),
        )

    @server.tool(
        name="execute_sql_query",
        title="Execute an approved Helix ARAPI SQL query",
        description=(
            "Execute an unchanged, unexpired SQL query plan exactly once. "
            "Never call this in the same turn as plan_sql_query. Call it only "
            "after the user has visibly reviewed and explicitly approved the "
            "exact environment, SQL, limit, and digest. Reuse that "
            "plan after approval; never generate a replacement plan."
        ),
        annotations=_SQL_EXECUTE_ANNOTATIONS,
        structured_output=True,
    )
    async def execute_sql_query(
        environment: Environment,
        plan_id: str,
        plan_digest: str,
    ) -> ExecuteSqlQueryOutput:
        return await audit.execute(
            tool="execute_sql_query",
            environment=environment,
            operation=lambda: database.execute_sql_query(
                environment=environment,
                plan_id=plan_id,
                plan_digest=plan_digest,
            ),
        )

    @server.tool(
        name="get_sql_query_plan",
        title="Get a pending SQL query plan",
        description=(
            "Return one SQL query plan for review without executing it. Its "
            "status and remaining_seconds are the authoritative expiry state."
        ),
        annotations=_WRITE_PLAN_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def get_sql_query_plan(
        environment: Environment,
        plan_id: str,
    ) -> SqlQueryPlanOutput:
        return await audit.execute(
            tool="get_sql_query_plan",
            environment=environment,
            operation=lambda: database.get_sql_query_plan(
                environment=environment,
                plan_id=plan_id,
            ),
        )

    @server.tool(
        name="cancel_sql_query_plan",
        title="Cancel a pending SQL query plan",
        description=(
            "Cancel one pending SQL query plan without executing it."
        ),
        annotations=_SQL_PLAN_ANNOTATIONS,
        structured_output=True,
    )
    async def cancel_sql_query_plan(
        environment: Environment,
        plan_id: str,
    ) -> SqlQueryPlanOutput:
        return await audit.execute(
            tool="cancel_sql_query_plan",
            environment=environment,
            operation=lambda: database.cancel_sql_query_plan(
                environment=environment,
                plan_id=plan_id,
            ),
        )

    @server.tool(
        name="list_database_objects",
        title="List Helix database objects through ARAPI",
        description=(
            "List policy-permitted Helix database tables and views through "
            "administrator-only ARAPI SQL for one "
            "explicit environment. Results are filtered and paginated."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def list_database_objects(
        environment: Environment,
        schema: str | None = None,
        name_contains: str | None = None,
        kind: DatabaseObjectKind | None = None,
        include_system: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> ListDatabaseObjectsOutput:
        return await audit.execute(
            tool="list_database_objects",
            environment=environment,
            operation=lambda: database.list_database_objects(
                environment=environment,
                schema=schema,
                name_contains=name_contains,
                kind=kind,
                include_system=include_system,
                offset=offset,
                limit=limit,
            ),
        )

    @server.tool(
        name="list_database_columns",
        title="List Helix database object columns",
        description=(
            "List safe column metadata through administrator-only ARAPI SQL "
            "for one policy-permitted table or view in an explicit environment."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def list_database_columns(
        environment: Environment,
        schema: str,
        object_name: str,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ListDatabaseColumnsOutput:
        return await audit.execute(
            tool="list_database_columns",
            environment=environment,
            operation=lambda: database.list_database_columns(
                environment=environment,
                schema=schema,
                object_name=object_name,
                name_contains=name_contains,
                offset=offset,
                limit=limit,
            ),
        )

    @server.tool(
        name="describe_database_object",
        title="Describe Helix database object",
        description=(
            "Describe one policy-permitted database table or view and its "
            "bounded metadata through administrator-only ARAPI SQL."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def describe_database_object(
        environment: Environment,
        schema: str,
        object_name: str,
    ) -> DescribeDatabaseObjectOutput:
        return await audit.execute(
            tool="describe_database_object",
            environment=environment,
            operation=lambda: database.describe_database_object(
                environment=environment,
                schema=schema,
                object_name=object_name,
            ),
        )

    @server.tool(
        name="list_forms",
        title="List Helix forms",
        description=(
            "List accessible BMC Helix forms for one explicit target. "
            "Results are filtered by policy and paginated."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def list_forms(
        environment: Environment,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ListFormsOutput:
        return await audit.execute(
            tool="list_forms",
            environment=environment,
            operation=lambda: forms.list_forms(
                environment=environment,
                name_contains=name_contains,
                offset=offset,
                limit=limit,
            ),
        )

    @server.tool(
        name="list_form_fields",
        title="List Helix form fields",
        description=(
            "List safe field metadata for one permitted BMC Helix form. "
            "Results are paginated and sensitive field names are omitted."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def list_form_fields(
        environment: Environment,
        form: str,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ListFormFieldsOutput:
        return await audit.execute(
            tool="list_form_fields",
            environment=environment,
            operation=lambda: forms.list_form_fields(
                environment=environment,
                form=form,
                name_contains=name_contains,
                offset=offset,
                limit=limit,
            ),
        )

    @server.tool(
        name="query_form",
        title="Query a Helix form",
        description=(
            "Read bounded entries from one explicitly selected BMC Helix "
            "environment. The form and every requested field "
            "must be permitted by target policy."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def query_form(
        environment: Environment,
        form: str,
        fields: list[str],
        qualification: str | None = None,
        sort: list[FormSort] | None = None,
        offset: int = 0,
        limit: int = 100,
        include_total: bool = False,
    ) -> QueryFormOutput:
        return await audit.execute(
            tool="query_form",
            environment=environment,
            operation=lambda: forms.query_form(
                environment=environment,
                form=form,
                fields=tuple(fields),
                qualification=qualification,
                sort=tuple(sort or ()),
                offset=offset,
                limit=limit,
                include_total=include_total,
            ),
        )

    @server.tool(
        name="get_entry",
        title="Get Helix entry",
        description=(
            "Read one BMC Helix entry by its exact ID from an explicitly "
            "selected target. The form and every requested field must be "
            "permitted by target policy."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def get_entry(
        environment: Environment,
        form: str,
        entry_id: str,
        fields: list[str],
    ) -> GetEntryOutput:
        return await audit.execute(
            tool="get_entry",
            environment=environment,
            operation=lambda: forms.get_entry(
                environment=environment,
                form=form,
                entry_id=entry_id,
                fields=tuple(fields),
            ),
        )

    @server.tool(
        name="plan_create_entry",
        title="Plan Helix entry creation",
        description=(
            "Validate and stage a temporary create plan for one explicit "
            "target. This does not write to Helix. Review every returned "
            "value and digest before requesting apply_create_entry."
        ),
        annotations=_WRITE_PLAN_ANNOTATIONS,
        structured_output=True,
    )
    async def plan_create_entry(
        environment: Environment,
        form: str,
        values: dict[str, JsonScalar],
        reason: str,
    ) -> WritePlanOutput:
        return await audit.execute(
            tool="plan_create_entry",
            environment=environment,
            operation=lambda: writes.plan_create_entry(
                environment=environment,
                form=form,
                values=values,
                reason=reason,
            ),
        )

    @server.tool(
        name="apply_create_entry",
        title="Apply Helix entry creation",
        description=(
            "Create one Helix entry from an unchanged, unexpired plan. "
            "Never call this tool until the user has reviewed the plan and "
            "explicitly approved this exact write."
        ),
        annotations=_WRITE_APPLY_ANNOTATIONS,
        structured_output=True,
    )
    async def apply_create_entry(
        environment: Environment,
        plan_id: str,
        plan_digest: str,
    ) -> ApplyWriteOutput:
        return await audit.execute(
            tool="apply_create_entry",
            environment=environment,
            operation=lambda: writes.apply_create_entry(
                environment=environment,
                plan_id=plan_id,
                plan_digest=plan_digest,
            ),
        )

    @server.tool(
        name="plan_update_entry",
        title="Plan Helix entry update",
        description=(
            "Read the current allowed values and stage a conditional update "
            "plan for one explicit target. This does not modify Helix."
        ),
        annotations=_WRITE_PLAN_EXTERNAL_ANNOTATIONS,
        structured_output=True,
    )
    async def plan_update_entry(
        environment: Environment,
        form: str,
        entry_id: str,
        values: dict[str, JsonScalar],
        reason: str,
    ) -> WritePlanOutput:
        return await audit.execute(
            tool="plan_update_entry",
            environment=environment,
            operation=lambda: writes.plan_update_entry(
                environment=environment,
                form=form,
                entry_id=entry_id,
                values=values,
                reason=reason,
            ),
        )

    @server.tool(
        name="apply_update_entry",
        title="Apply Helix entry update",
        description=(
            "Conditionally update one Helix entry from an unchanged, "
            "unexpired plan. Never call this tool until the user has reviewed "
            "the plan and explicitly approved this exact write."
        ),
        annotations=_WRITE_APPLY_ANNOTATIONS,
        structured_output=True,
    )
    async def apply_update_entry(
        environment: Environment,
        plan_id: str,
        plan_digest: str,
    ) -> ApplyWriteOutput:
        return await audit.execute(
            tool="apply_update_entry",
            environment=environment,
            operation=lambda: writes.apply_update_entry(
                environment=environment,
                plan_id=plan_id,
                plan_digest=plan_digest,
            ),
        )

    @server.tool(
        name="get_write_plan",
        title="Get Helix write plan",
        description=(
            "Return one temporary write plan for the explicitly selected "
            "target without modifying Helix."
        ),
        annotations=_WRITE_PLAN_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def get_write_plan(
        environment: Environment,
        plan_id: str,
    ) -> WritePlanOutput:
        return await audit.execute(
            tool="get_write_plan",
            environment=environment,
            operation=lambda: writes.get_write_plan(
                environment=environment,
                plan_id=plan_id,
            ),
        )

    @server.tool(
        name="cancel_write_plan",
        title="Cancel Helix write plan",
        description=(
            "Cancel one pending temporary write plan. This does not modify "
            "Helix and a cancelled plan cannot be applied."
        ),
        annotations=_WRITE_PLAN_ANNOTATIONS,
        structured_output=True,
    )
    async def cancel_write_plan(
        environment: Environment,
        plan_id: str,
    ) -> WritePlanOutput:
        return await audit.execute(
            tool="cancel_write_plan",
            environment=environment,
            operation=lambda: writes.cancel_write_plan(
                environment=environment,
                plan_id=plan_id,
            ),
        )
