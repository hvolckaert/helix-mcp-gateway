"""In-memory MCP contract tests for the initial Helix tools."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from helix_mcp.bootstrap import ApplicationContext
from helix_mcp.config import (
    ArapiBackendConfig,
    Environment,
    HelixConfig,
    RuntimeSettings,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
    TargetPolicyConfig,
)
from helix_mcp.observability import ToolAuditor
from helix_mcp.secrets import EnvironmentSecretProvider, SecretResolver
from helix_mcp.server import SERVER_INSTRUCTIONS, create_mcp_server
from helix_mcp.services.database import (
    DatabaseColumnCatalogResult,
    DatabaseColumnMetadata,
    DatabaseObjectCatalogResult,
    DatabaseObjectDescription,
    DatabaseObjectKind,
    DatabaseObjectMetadata,
    SqlQueryExecutionResult,
    SqlQueryPlanResult,
    SqlQueryPlanStatus,
)
from helix_mcp.services.forms import (
    FormCatalogQuery,
    FormCatalogResult,
    FormEntry,
    FormEntryQuery,
    FormEntryResult,
    FormFieldMetadata,
    FormFieldsQuery,
    FormFieldsResult,
    FormMetadata,
    FormQuery,
    FormQueryResult,
)
from helix_mcp.services.health import (
    HealthCheckResult,
    HealthComponent,
    HealthComponentResult,
    HealthStatus,
)
from helix_mcp.services.writes import (
    ApplyWriteResult,
    WriteOperation,
    WritePlanResult,
    WritePlanStatus,
)
from helix_mcp.targeting import RuntimeTargetContext, TargetRegistry
from helix_mcp.tools import (
    FormToolAdapter,
    HealthToolAdapter,
    TargetToolAdapter,
    register_mcp_tools,
)


def run(coroutine):
    return asyncio.run(coroutine)


def target_config(*, allow_reads: bool) -> tuple[HelixConfig, TargetConfig]:
    policy = TargetPolicyConfig(
        name="read_policy",
        allowed_forms=("Example:HelpDesk",) if allow_reads else (),
        allowed_fields_by_form=(
            {"Example:HelpDesk": ("Incident Number", "Status")}
            if allow_reads
            else {}
        ),
        allow_form_reads=allow_reads,
    )
    target = TargetConfig(
        environment=Environment.DEV,
        display_name="Helix DEV",
        policy_ref=policy.name,
        arapi=ArapiBackendConfig(
            bridge_base_url="http://127.0.0.1:8090",
            gateway_host="127.0.0.1",
            gateway_port=46_000,
            credentials=SecretRef(
                provider=SecretProviderKind.ENVIRONMENT,
                key="HELIX_CREDENTIAL_DEV",
            ),
        ),
    )
    return HelixConfig(policies=(policy,), targets=(target,)), target


class FakeFormService:
    def __init__(self) -> None:
        self.calls: list[tuple[Environment, FormQuery]] = []
        self.field_calls: list[tuple[Environment, FormFieldsQuery]] = []
        self.catalog_calls: list[tuple[Environment, FormCatalogQuery]] = []
        self.entry_calls: list[tuple[Environment, FormEntryQuery]] = []

    async def list_forms(
        self,
        *,
        environment: Environment,
        query: FormCatalogQuery,
    ) -> FormCatalogResult:
        self.catalog_calls.append((environment, query))
        return FormCatalogResult(
            forms=(FormMetadata(name="Example:HelpDesk"),),
            offset=query.offset,
            limit=query.limit,
            total=1,
        )

    async def list_fields(
        self,
        *,
        environment: Environment,
        query: FormFieldsQuery,
    ) -> FormFieldsResult:
        self.field_calls.append((environment, query))
        return FormFieldsResult(
            fields=(
                FormFieldMetadata(
                    id=1,
                    name="Incident Number",
                    datatype="CHAR",
                ),
            ),
            offset=query.offset,
            limit=query.limit,
            total=1,
        )

    async def search(
        self,
        *,
        environment: Environment,
        query: FormQuery,
    ) -> FormQueryResult:
        self.calls.append((environment, query))
        return FormQueryResult(
            entries=(
                FormEntry(
                    values={
                        "Incident Number": "INC0001",
                        "Status": "Assigned",
                    }
                ),
            ),
            offset=query.offset,
            limit=query.limit,
            total=1 if query.include_total else None,
        )

    async def get_entry(
        self,
        *,
        environment: Environment,
        query: FormEntryQuery,
    ) -> FormEntryResult:
        self.entry_calls.append((environment, query))
        return FormEntryResult(
            entry_id=query.entry_id,
            entry=FormEntry(
                values={
                    "Incident Number": query.entry_id,
                    "Status": "Assigned",
                }
            ),
        )


class FakeHealthService:
    def __init__(self) -> None:
        self.calls: list[tuple[Environment, bool]] = []

    async def check(
        self,
        *,
        environment: Environment,
        force_refresh: bool = False,
    ) -> HealthCheckResult:
        self.calls.append((environment, force_refresh))
        return HealthCheckResult(
            environment=environment,
            status=HealthStatus.HEALTHY,
            cached=False,
            checks=(
                HealthComponentResult(
                    component=HealthComponent.ARAPI_BRIDGE,
                    status=HealthStatus.HEALTHY,
                    latency_ms=12,
                ),
            ),
        )


class FakeWriteTools:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def plan_create_entry(self, **kwargs) -> WritePlanResult:
        self.calls.append("plan_create_entry")
        return _write_plan(
            operation=WriteOperation.CREATE,
            form=kwargs["form"],
            values=kwargs["values"],
            reason=kwargs["reason"],
        )

    async def plan_update_entry(self, **kwargs) -> WritePlanResult:
        self.calls.append("plan_update_entry")
        return _write_plan(
            operation=WriteOperation.UPDATE,
            form=kwargs["form"],
            values=kwargs["values"],
            reason=kwargs["reason"],
            entry_id=kwargs["entry_id"],
        )

    async def apply_create_entry(self, **kwargs) -> ApplyWriteResult:
        self.calls.append("apply_create_entry")
        return _apply_result(WriteOperation.CREATE)

    async def apply_update_entry(self, **kwargs) -> ApplyWriteResult:
        self.calls.append("apply_update_entry")
        return _apply_result(WriteOperation.UPDATE)

    async def get_write_plan(self, **kwargs) -> WritePlanResult:
        self.calls.append("get_write_plan")
        return _write_plan(
            operation=WriteOperation.CREATE,
            form="Example:Writable",
            values={"Field": "value"},
            reason="approved test plan",
        )

    async def cancel_write_plan(self, **kwargs) -> WritePlanResult:
        self.calls.append("cancel_write_plan")
        return _write_plan(
            operation=WriteOperation.CREATE,
            form="Example:Writable",
            values={},
            reason="",
            status=WritePlanStatus.CANCELLED,
        )


class FakeDatabaseTools:
    async def plan_sql_query(self, **kwargs) -> SqlQueryPlanResult:
        return SqlQueryPlanResult(
            plan_id="1" * 32,
            plan_digest="2" * 64,
            environment=Environment.DEV,
            sql=kwargs["sql"],
            limit=kwargs["limit"],
            status=SqlQueryPlanStatus.PENDING,
            server_time=datetime(2026, 8, 2, 21, 30, tzinfo=UTC),
            expires_at=datetime(2026, 8, 2, 21, 40, tzinfo=UTC),
            remaining_seconds=600,
        )

    async def execute_sql_query(self, **kwargs) -> SqlQueryExecutionResult:
        return SqlQueryExecutionResult(
            plan_id=kwargs["plan_id"],
            plan_digest=kwargs["plan_digest"],
            environment=Environment.DEV,
            columns=("status",),
            rows=({"status": "ok"},),
            row_count=1,
            limit=5,
            truncated=False,
        )

    async def get_sql_query_plan(self, **kwargs) -> SqlQueryPlanResult:
        return await self.plan_sql_query(
            sql="SELECT status AS status FROM allowed_table",
            limit=5,
        )

    async def cancel_sql_query_plan(self, **kwargs) -> SqlQueryPlanResult:
        plan = await self.get_sql_query_plan(**kwargs)
        return plan.model_copy(update={"status": SqlQueryPlanStatus.CANCELLED})

    async def list_database_objects(
        self,
        **kwargs,
    ) -> DatabaseObjectCatalogResult:
        return DatabaseObjectCatalogResult(
            environment=Environment.DEV,
            objects=(
                DatabaseObjectMetadata(
                    schema_name="public",
                    name="example_base_element",
                    kind=DatabaseObjectKind.VIEW,
                ),
            ),
            offset=kwargs["offset"],
            limit=kwargs["limit"],
            truncated=False,
        )

    async def list_database_columns(
        self,
        **kwargs,
    ) -> DatabaseColumnCatalogResult:
        return DatabaseColumnCatalogResult(
            environment=Environment.DEV,
            schema_name=kwargs["schema"],
            object_name=kwargs["object_name"],
            columns=(
                DatabaseColumnMetadata(
                    name="instanceid",
                    data_type="character varying(38)",
                    nullable=False,
                    position=1,
                ),
            ),
            offset=kwargs["offset"],
            limit=kwargs["limit"],
            truncated=False,
        )

    async def describe_database_object(
        self,
        **kwargs,
    ) -> DatabaseObjectDescription:
        return DatabaseObjectDescription(
            environment=Environment.DEV,
            object=DatabaseObjectMetadata(
                schema_name=kwargs["schema"],
                name=kwargs["object_name"],
                kind=DatabaseObjectKind.VIEW,
            ),
            columns=(
                DatabaseColumnMetadata(
                    name="instanceid",
                    data_type="character varying(38)",
                    nullable=False,
                    position=1,
                ),
            ),
            truncated=False,
        )


def _write_plan(
    *,
    operation: WriteOperation,
    form: str,
    values: dict,
    reason: str,
    entry_id: str | None = None,
    status: WritePlanStatus = WritePlanStatus.PENDING,
) -> WritePlanResult:
    return WritePlanResult(
        plan_id="a" * 32,
        plan_digest="b" * 64,
        operation=operation,
        environment=Environment.DEV,
        form=form,
        entry_id=entry_id,
        proposed_values=values,
        reason=reason,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        status=status,
    )


def _apply_result(operation: WriteOperation) -> ApplyWriteResult:
    return ApplyWriteResult(
        plan_id="a" * 32,
        operation=operation,
        environment=Environment.DEV,
        form="Example:Writable",
        entry_id="000000000000001",
    )


def test_tools_publish_structured_schemas_annotations_and_safe_outputs() -> (
    None
):
    config, _ = target_config(allow_reads=True)
    fake_forms = FakeFormService()
    fake_health = FakeHealthService()
    fake_writes = FakeWriteTools()
    fake_database = FakeDatabaseTools()
    server = FastMCP("contract-test")
    register_mcp_tools(
        server,
        targets=TargetToolAdapter(TargetRegistry(config)),
        forms=FormToolAdapter(fake_forms, fake_forms),
        database=fake_database,
        health=HealthToolAdapter(fake_health),
        writes=fake_writes,
        audit=ToolAuditor(),
    )

    async def scenario() -> None:
        async with create_connected_server_and_client_session(
            server
        ) as client:
            listed = await client.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            assert set(by_name) == {
                "health_check",
                "apply_create_entry",
                "apply_update_entry",
                "cancel_write_plan",
                "get_write_plan",
                "get_entry",
                "list_form_fields",
                "list_forms",
                "list_database_columns",
                "list_database_objects",
                "list_targets",
                "plan_create_entry",
                "plan_update_entry",
                "query_form",
                "plan_sql_query",
                "execute_sql_query",
                "get_sql_query_plan",
                "cancel_sql_query_plan",
                "describe_database_object",
            }

            public_contract = "\n".join(
                (
                    SERVER_INSTRUCTIONS,
                    *(
                        f"{tool.title or ''}\n{tool.description or ''}"
                        for tool in listed.tools
                    ),
                )
            ).casefold()
            for obsolete_term in (
                "postgresql",
                "psycopg",
                "_".join(("helix", "db", "")),
                "direct database connection",
            ):
                assert obsolete_term not in public_contract
            for tool_name in (
                "plan_sql_query",
                "list_database_objects",
                "list_database_columns",
                "describe_database_object",
            ):
                assert (
                    "arapi"
                    in (by_name[tool_name].description or "").casefold()
                )

            catalog = by_name["list_targets"]
            assert catalog.annotations.readOnlyHint is True
            assert catalog.annotations.destructiveHint is False
            assert catalog.annotations.openWorldHint is False
            assert catalog.outputSchema is not None

            query_tool = by_name["query_form"]
            assert query_tool.annotations.readOnlyHint is True
            assert query_tool.annotations.destructiveHint is False
            assert query_tool.annotations.openWorldHint is True
            assert set(query_tool.inputSchema["required"]) == {
                "environment",
                "form",
                "fields",
            }

            update_plan_tool = by_name["plan_update_entry"]
            assert update_plan_tool.annotations.readOnlyHint is False
            assert update_plan_tool.annotations.destructiveHint is False
            assert update_plan_tool.annotations.openWorldHint is True

            sql_plan_tool = by_name["plan_sql_query"]
            assert sql_plan_tool.annotations.readOnlyHint is False
            assert sql_plan_tool.annotations.destructiveHint is False
            assert sql_plan_tool.annotations.openWorldHint is False
            assert set(sql_plan_tool.inputSchema["required"]) == {
                "environment",
                "sql",
            }

            sql_execute_tool = by_name["execute_sql_query"]
            assert sql_execute_tool.annotations.readOnlyHint is True
            assert sql_execute_tool.annotations.destructiveHint is False
            assert sql_execute_tool.annotations.openWorldHint is True
            assert set(sql_execute_tool.inputSchema["required"]) == {
                "environment",
                "plan_id",
                "plan_digest",
            }

            objects_tool = by_name["list_database_objects"]
            assert objects_tool.annotations.readOnlyHint is True
            assert objects_tool.annotations.destructiveHint is False
            assert set(objects_tool.inputSchema["required"]) == {
                "environment",
            }

            columns_tool = by_name["list_database_columns"]
            assert columns_tool.annotations.readOnlyHint is True
            assert set(columns_tool.inputSchema["required"]) == {
                "environment",
                "schema",
                "object_name",
            }

            describe_tool = by_name["describe_database_object"]
            assert describe_tool.annotations.readOnlyHint is True
            assert set(describe_tool.inputSchema["required"]) == {
                "environment",
                "schema",
                "object_name",
            }

            entry_tool = by_name["get_entry"]
            assert entry_tool.annotations.readOnlyHint is True
            assert entry_tool.annotations.destructiveHint is False
            assert entry_tool.annotations.openWorldHint is True
            assert set(entry_tool.inputSchema["required"]) == {
                "environment",
                "form",
                "entry_id",
                "fields",
            }

            field_tool = by_name["list_form_fields"]
            assert field_tool.annotations.readOnlyHint is True
            assert field_tool.annotations.destructiveHint is False
            assert field_tool.annotations.openWorldHint is True
            assert set(field_tool.inputSchema["required"]) == {
                "environment",
                "form",
            }

            forms_tool = by_name["list_forms"]
            assert forms_tool.annotations.readOnlyHint is True
            assert forms_tool.annotations.destructiveHint is False
            assert forms_tool.annotations.openWorldHint is True
            assert set(forms_tool.inputSchema["required"]) == {
                "environment",
            }

            health_tool = by_name["health_check"]
            assert health_tool.annotations.readOnlyHint is True
            assert health_tool.annotations.destructiveHint is False
            assert health_tool.annotations.openWorldHint is True
            assert set(health_tool.inputSchema["required"]) == {
                "environment",
            }

            plan_tool = by_name["plan_create_entry"]
            assert plan_tool.annotations.readOnlyHint is False
            assert plan_tool.annotations.destructiveHint is False
            assert set(plan_tool.inputSchema["required"]) == {
                "environment",
                "form",
                "values",
                "reason",
            }
            apply_tool = by_name["apply_create_entry"]
            assert apply_tool.annotations.readOnlyHint is False
            assert apply_tool.annotations.destructiveHint is True
            assert apply_tool.annotations.idempotentHint is True

            targets = await client.call_tool(
                "list_targets",
                {"include_disabled": False},
            )
            assert targets.isError is False
            serialized = json.dumps(targets.structuredContent)
            assert "helix.example.invalid" not in serialized
            assert "HELIX_CREDENTIAL" not in serialized
            assert (
                targets.structuredContent["targets"][0]["environment"] == "dev"
            )
            assert "instance" not in targets.structuredContent["targets"][0]

            health = await client.call_tool(
                "health_check",
                {
                    "environment": "dev",
                    "force_refresh": True,
                },
            )
            assert health.isError is False
            assert health.structuredContent == {
                "environment": "dev",
                "status": "healthy",
                "cached": False,
                "checks": [
                    {
                        "component": "arapi_bridge",
                        "status": "healthy",
                        "latency_ms": 12,
                        "error_code": None,
                    }
                ],
            }

            plan = await client.call_tool(
                "plan_create_entry",
                {
                    "environment": "dev",
                    "form": "Example:Writable",
                    "values": {"Field": "review-value"},
                    "reason": "contract test write",
                },
            )
            assert plan.isError is False
            assert plan.structuredContent["status"] == "pending"
            assert plan.structuredContent["proposed_values"] == {
                "Field": "review-value"
            }

            forms = await client.call_tool(
                "list_forms",
                {
                    "environment": "dev",
                    "name_contains": "help",
                    "limit": 10,
                },
            )
            assert forms.isError is False
            assert forms.structuredContent["forms"] == [
                {"name": "Example:HelpDesk"}
            ]
            assert forms.structuredContent["total"] == 1

            fields = await client.call_tool(
                "list_form_fields",
                {
                    "environment": "dev",
                    "form": "Example:HelpDesk",
                    "name_contains": "incident",
                    "limit": 10,
                },
            )
            assert fields.isError is False
            assert fields.structuredContent["fields"] == [
                {
                    "id": 1,
                    "name": "Incident Number",
                    "datatype": "CHAR",
                }
            ]
            assert fields.structuredContent["total"] == 1

            result = await client.call_tool(
                "query_form",
                {
                    "environment": "dev",
                    "form": "Example:HelpDesk",
                    "fields": ["Incident Number", "Status"],
                    "qualification": "'Status'=\"Assigned\"",
                    "sort": [
                        {
                            "field": "Incident Number",
                            "direction": "desc",
                        }
                    ],
                    "limit": 2,
                    "include_total": True,
                },
            )
            assert result.isError is False
            assert result.structuredContent["entries"][0]["values"] == {
                "Incident Number": "INC0001",
                "Status": "Assigned",
            }
            assert result.structuredContent["total"] == 1

            sql_plan = await client.call_tool(
                "plan_sql_query",
                {
                    "environment": "dev",
                    "sql": "SELECT status AS status FROM allowed_table",
                    "limit": 5,
                },
            )
            assert sql_plan.isError is False
            assert sql_plan.structuredContent["sql"] == (
                "SELECT status AS status FROM allowed_table"
            )
            assert sql_plan.structuredContent["status"] == "pending"

            sql_result = await client.call_tool(
                "execute_sql_query",
                {
                    "environment": "dev",
                    "plan_id": "1" * 32,
                    "plan_digest": "2" * 64,
                },
            )
            assert sql_result.isError is False
            assert sql_result.structuredContent == {
                "plan_id": "1" * 32,
                "plan_digest": "2" * 64,
                "environment": "dev",
                "columns": ["status"],
                "rows": [{"status": "ok"}],
                "row_count": 1,
                "limit": 5,
                "truncated": False,
            }

            objects = await client.call_tool(
                "list_database_objects",
                {
                    "environment": "dev",
                    "schema": "public",
                    "name_contains": "base_element",
                    "kind": "view",
                    "limit": 5,
                },
            )
            assert objects.isError is False
            assert objects.structuredContent["objects"] == [
                {
                    "schema_name": "public",
                    "name": "example_base_element",
                    "kind": "view",
                }
            ]

            columns = await client.call_tool(
                "list_database_columns",
                {
                    "environment": "dev",
                    "schema": "public",
                    "object_name": "example_base_element",
                    "limit": 5,
                },
            )
            assert columns.isError is False
            assert columns.structuredContent["columns"][0]["name"] == (
                "instanceid"
            )

            description = await client.call_tool(
                "describe_database_object",
                {
                    "environment": "dev",
                    "schema": "public",
                    "object_name": "example_base_element",
                },
            )
            assert description.isError is False
            assert description.structuredContent["object"]["kind"] == "view"

            entry = await client.call_tool(
                "get_entry",
                {
                    "environment": "dev",
                    "form": "Example:HelpDesk",
                    "entry_id": "INC0001",
                    "fields": ["Incident Number", "Status"],
                },
            )
            assert entry.isError is False
            assert entry.structuredContent == {
                "environment": "dev",
                "form": "Example:HelpDesk",
                "entry_id": "INC0001",
                "entry": {
                    "values": {
                        "Incident Number": "INC0001",
                        "Status": "Assigned",
                    }
                },
            }

    run(scenario())
    assert len(fake_forms.calls) == 1
    assert fake_forms.calls[0][1].sort[0].direction.value == "desc"
    assert len(fake_forms.field_calls) == 1
    assert fake_forms.field_calls[0][1].name_contains == "incident"
    assert len(fake_forms.catalog_calls) == 1
    assert fake_forms.catalog_calls[0][1].name_contains == "help"
    assert len(fake_forms.entry_calls) == 1
    assert fake_forms.entry_calls[0][1].entry_id == "INC0001"
    assert fake_health.calls == [(Environment.DEV, True)]
    assert fake_writes.calls == ["plan_create_entry"]


def test_invalid_tool_query_is_sanitized_before_service_call() -> None:
    config, _ = target_config(allow_reads=True)
    fake_forms = FakeFormService()
    fake_health = FakeHealthService()
    fake_writes = FakeWriteTools()
    fake_database = FakeDatabaseTools()
    server = FastMCP("contract-test")
    register_mcp_tools(
        server,
        targets=TargetToolAdapter(TargetRegistry(config)),
        forms=FormToolAdapter(fake_forms, fake_forms),
        database=fake_database,
        health=HealthToolAdapter(fake_health),
        writes=fake_writes,
        audit=ToolAuditor(),
    )

    async def scenario() -> None:
        async with create_connected_server_and_client_session(
            server,
            raise_exceptions=False,
        ) as client:
            result = await client.call_tool(
                "query_form",
                {
                    "environment": "dev",
                    "form": "Example:HelpDesk",
                    "fields": ["Status", "status"],
                    "qualification": "'Secret'=\"private-value\"",
                },
            )
            assert result.isError is True
            assert "code=TOOL_INPUT_INVALID" in result.content[0].text
            assert "operation_id=" in result.content[0].text
            assert "private-value" not in result.content[0].text

    run(scenario())
    assert fake_forms.calls == []


def test_application_server_lifespan_closes_clients_and_keeps_policy_locked() -> (
    None
):
    config, target = target_config(allow_reads=False)
    runtime = RuntimeTargetContext(
        settings=RuntimeSettings(
            config_path=Path("/runtime/helix.yaml"),
        ),
        config=config,
        registry=TargetRegistry(config),
        secrets=SecretResolver(
            [
                EnvironmentSecretProvider(
                    {
                        target.arapi.credentials.key: (
                            '{"username":"unused","password":"unused"}'
                        )
                    }
                )
            ]
        ),
    )
    application = ApplicationContext(runtime)

    class FakeBridgeProcess:
        def __init__(self) -> None:
            self.started = False
            self.closed = False

        async def start(self) -> None:
            self.started = True

        async def aclose(self) -> None:
            self.closed = True

    bridge = FakeBridgeProcess()
    application.arapi_bridge = bridge
    server = create_mcp_server(application)

    async def scenario() -> None:
        async with create_connected_server_and_client_session(
            server,
            raise_exceptions=False,
        ) as client:
            result = await client.call_tool(
                "query_form",
                {
                    "environment": "dev",
                    "form": "Example:HelpDesk",
                    "fields": ["Status"],
                },
            )
            assert result.isError is True
            assert "code=FORM_READ_DISABLED" in result.content[0].text
            assert "operation_id=" in result.content[0].text
            assert len(application.arapi_clients) == 0

    run(scenario())
    assert application.closed is True
    assert bridge.started is True
    assert bridge.closed is True
