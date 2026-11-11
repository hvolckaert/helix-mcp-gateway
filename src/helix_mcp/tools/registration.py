"""Register application adapters as MCP tools."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from helix_mcp.config import Environment
from helix_mcp.observability import ToolAuditor
from helix_mcp.services.database import DatabaseObjectKind
from helix_mcp.services.forms import FormSort
from helix_mcp.services.writes import JsonScalar
from helix_mcp.targeting import TargetRegistry
from helix_mcp.tools.database import DatabaseToolAdapter
from helix_mcp.tools.forms import FormToolAdapter
from helix_mcp.tools.health import HealthToolAdapter
from helix_mcp.tools.models import (
    ApplyWriteOutput,
    SqlQueryPlanOutput,
    WritePlanOutput,
)
from helix_mcp.tools.read_models import (
    DescribeDatabaseObjectOutput,
    ExecuteSqlQueryOutput,
    GetEntryOutput,
    GetFormFieldDefinitionOutput,
    GetFormFieldMenuMetadataOutput,
    GetFormFieldSelectionValuesOutput,
    GetFormViewDefinitionOutput,
    GetSqlQueryPlanOutput,
    GetWritePlanOutput,
    HealthCheckOutput,
    ListDatabaseColumnsOutput,
    ListDatabaseObjectsOutput,
    ListFormFieldsOutput,
    ListFormsOutput,
    ListFormViewsOutput,
    ListTargetsOutput,
    QueryFormOutput,
    ResolveFormFieldMenuValuesOutput,
)
from helix_mcp.tools.reads import ReadToolRunner
from helix_mcp.tools.targets import TargetToolAdapter
from helix_mcp.tools.writes import FormWriteToolAdapter

_CATALOG_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_HELIX_READ_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
_WRITE_PLAN_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
_WRITE_PLAN_EXTERNAL_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
_WRITE_APPLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)
_WRITE_PLAN_READ_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_SQL_PLAN_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
_SQL_EXECUTE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


def register_mcp_tools(
    server: MCPServer,
    *,
    targets: TargetToolAdapter,
    forms: FormToolAdapter,
    database: DatabaseToolAdapter,
    health: HealthToolAdapter,
    writes: FormWriteToolAdapter,
    audit: ToolAuditor,
    registry: TargetRegistry,
) -> None:
    """Register thin, typed functions without business rules."""

    reads = ReadToolRunner(registry, audit)

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
        return await reads.execute(
            tool="list_targets",
            output_type=ListTargetsOutput,
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
        return await reads.execute(
            tool="health_check",
            output_type=HealthCheckOutput,
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
        return await reads.execute(
            tool="execute_sql_query",
            output_type=ExecuteSqlQueryOutput,
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
    ) -> GetSqlQueryPlanOutput:
        return await reads.execute(
            tool="get_sql_query_plan",
            output_type=GetSqlQueryPlanOutput,
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
        return await reads.execute(
            tool="list_database_objects",
            output_type=ListDatabaseObjectsOutput,
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
        return await reads.execute(
            tool="list_database_columns",
            output_type=ListDatabaseColumnsOutput,
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
        return await reads.execute(
            tool="describe_database_object",
            output_type=DescribeDatabaseObjectOutput,
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
        return await reads.execute(
            tool="list_forms",
            output_type=ListFormsOutput,
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
        return await reads.execute(
            tool="list_form_fields",
            output_type=ListFormFieldsOutput,
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
        name="list_form_views",
        title="List Helix form views",
        description="List view IDs, names, locales and declared types for one policy-visible form. Results are paginated.",
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def list_form_views(
        environment: Environment,
        form: str,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ListFormViewsOutput:
        return await reads.execute(
            tool="list_form_views",
            output_type=ListFormViewsOutput,
            environment=environment,
            operation=lambda: forms.list_form_views(
                environment=environment,
                form=form,
                name_contains=name_contains,
                offset=offset,
                limit=limit,
            ),
        )

    @server.tool(
        name="get_form_view_definition",
        title="Get Helix form-view definition",
        description=(
            "Read declared view and field display properties, including supported PWA settings, for an exact view ID. "
            "Only policy-visible fields are fetched; field_ids optionally selects exact IDs. Pagination and total refer to the selected visible field catalog, including fields absent from the view. "
            "Shared and view-specific properties remain separate. Missing properties are unspecified, not false; workflow may change runtime behavior."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def get_form_view_definition(
        environment: Environment,
        form: str,
        view_id: int,
        field_ids: tuple[int, ...] | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> GetFormViewDefinitionOutput:
        return await reads.execute(
            tool="get_form_view_definition",
            output_type=GetFormViewDefinitionOutput,
            environment=environment,
            operation=lambda: forms.get_form_view_definition(
                environment=environment,
                form=form,
                view_id=view_id,
                field_ids=field_ids,
                offset=offset,
                limit=limit,
            ),
        )

    @server.tool(
        name="get_form_field_definition",
        title="Get Helix form-field definition",
        description=(
            "Read the stored definition of one exact policy-visible field ID: "
            "typed default, limits, requiredness, create mode, and declared group "
            "permissions. These are metadata, not effective runtime authorization. "
            "View properties and workflow effects are separate."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def get_form_field_definition(
        environment: Environment,
        form: str,
        field_id: int,
    ) -> GetFormFieldDefinitionOutput:
        return await reads.execute(
            tool="get_form_field_definition",
            output_type=GetFormFieldDefinitionOutput,
            environment=environment,
            operation=lambda: forms.get_form_field_definition(
                environment=environment,
                form=form,
                field_id=field_id,
            ),
        )

    @server.tool(
        name="get_form_field_selection_values",
        title="Get Helix form-field selection values",
        description=(
            "Read the configured number-to-label mapping for one exact "
            "policy-visible BMC Helix field ID. ENUM fields return their "
            "regular or custom selection values. Non-selection fields "
            "return no selection style or values."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def get_form_field_selection_values(
        environment: Environment,
        form: str,
        field_id: int,
    ) -> GetFormFieldSelectionValuesOutput:
        return await reads.execute(
            tool="get_form_field_selection_values",
            output_type=GetFormFieldSelectionValuesOutput,
            environment=environment,
            operation=lambda: forms.get_form_field_selection_values(
                environment=environment,
                form=form,
                field_id=field_id,
            ),
        )

    @server.tool(
        name="get_form_field_menu_metadata",
        title="Get Helix form-field character-menu metadata",
        description=(
            "Read whether one exact policy-visible BMC Helix field ID has a "
            "character menu associated with it. CHAR fields return the menu "
            "name and append or overwrite style. Fields without a character "
            "menu return has_menu false and no menu name or style."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def get_form_field_menu_metadata(
        environment: Environment,
        form: str,
        field_id: int,
    ) -> GetFormFieldMenuMetadataOutput:
        return await reads.execute(
            tool="get_form_field_menu_metadata",
            output_type=GetFormFieldMenuMetadataOutput,
            environment=environment,
            operation=lambda: forms.get_form_field_menu_metadata(
                environment=environment,
                form=form,
                field_id=field_id,
            ),
        )

    @server.tool(
        name="resolve_form_field_menu_values",
        title="Resolve Helix form-field character-menu values",
        description=(
            "Resolve a bounded set of character-menu values for one exact "
            "policy-visible BMC Helix field ID. The menu must be associated "
            "with that field; arbitrary menu names are not accepted. Dynamic "
            "menus may read their backing data with the selected BMC account. "
            "No entry is modified."
        ),
        annotations=_HELIX_READ_ANNOTATIONS,
        structured_output=True,
    )
    async def resolve_form_field_menu_values(
        environment: Environment,
        form: str,
        field_id: int,
        limit: int = 100,
    ) -> ResolveFormFieldMenuValuesOutput:
        return await reads.execute(
            tool="resolve_form_field_menu_values",
            output_type=ResolveFormFieldMenuValuesOutput,
            environment=environment,
            operation=lambda: forms.resolve_form_field_menu_values(
                environment=environment,
                form=form,
                field_id=field_id,
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
        return await reads.execute(
            tool="query_form",
            output_type=QueryFormOutput,
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
        return await reads.execute(
            tool="get_entry",
            output_type=GetEntryOutput,
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
    ) -> GetWritePlanOutput:
        return await reads.execute(
            tool="get_write_plan",
            output_type=GetWritePlanOutput,
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
