"""Reproducible MCP stdio E2E with a managed Java bridge test double."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.support.java_bridge import available_port, build_test_runtime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_E2E_ENABLED = os.environ.get("HELIX_LOCAL_E2E_TESTS") == "1"

FORM = "Sample:Form"
ENTRY_ID = "000000000000001"
CREATED_ENTRY_ID = "000000000000999"
USERNAME = "java-bridge-user"
PASSWORD = "java-bridge-password-never-expose"
FIELDS = ("Request ID", "Name", "Count", "Enabled", "Description")
EXPECTED_TOOLS = {
    "apply_create_entry",
    "apply_update_entry",
    "cancel_sql_query_plan",
    "cancel_write_plan",
    "describe_database_object",
    "get_entry",
    "get_sql_query_plan",
    "get_write_plan",
    "health_check",
    "list_database_columns",
    "list_database_objects",
    "list_form_fields",
    "list_forms",
    "list_targets",
    "plan_create_entry",
    "plan_sql_query",
    "plan_update_entry",
    "query_form",
    "execute_sql_query",
}

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.local_e2e,
    pytest.mark.skipif(
        not LOCAL_E2E_ENABLED,
        reason="set HELIX_LOCAL_E2E_TESTS=1 for the local stdio E2E",
    ),
]


def test_reproducible_stdio_read_write_flow(tmp_path: Path) -> None:
    """Exercise the real MCP process without external Helix dependencies."""

    bridge_jar, library_dir = build_test_runtime(tmp_path)
    bridge_port = available_port()
    config_path = tmp_path / "helix.yaml"
    dotenv_path = tmp_path / ".env"
    _write_config(config_path, bridge_port)
    with tempfile.TemporaryDirectory(
        prefix="helix-mcp-e2e-state-",
        dir="/tmp",
    ) as state_directory:
        state_path = Path(state_directory)
        audit_path = state_path / "audit.jsonl"
        _write_dotenv(
            dotenv_path,
            config_path=config_path,
            bridge_jar=bridge_jar,
            library_dir=library_dir,
            audit_path=audit_path,
            write_plan_db=state_path / "write-plans.sqlite3",
            write_plan_key=_write_plan_key(state_path),
        )

        asyncio.run(
            _reproducible_stdio_read_write_flow(
                dotenv_path=dotenv_path,
                audit_path=audit_path,
                bridge_port=bridge_port,
            )
        )


def test_write_plan_survives_complete_stdio_restart(tmp_path: Path) -> None:
    """Persist one pending plan across two independent MCP processes."""

    bridge_jar, library_dir = build_test_runtime(tmp_path)
    bridge_port = available_port()
    config_path = tmp_path / "helix.yaml"
    dotenv_path = tmp_path / ".env"
    _write_config(config_path, bridge_port)
    with tempfile.TemporaryDirectory(
        prefix="helix-mcp-e2e-state-",
        dir="/tmp",
    ) as state_directory:
        state_path = Path(state_directory)
        _write_dotenv(
            dotenv_path,
            config_path=config_path,
            bridge_jar=bridge_jar,
            library_dir=library_dir,
            audit_path=state_path / "audit.jsonl",
            write_plan_db=state_path / "write-plans.sqlite3",
            write_plan_key=_write_plan_key(state_path),
        )

        asyncio.run(
            _write_plan_restart_flow(
                dotenv_path=dotenv_path,
                bridge_port=bridge_port,
            )
        )


async def _reproducible_stdio_read_write_flow(
    *,
    dotenv_path: Path,
    audit_path: Path,
    bridge_port: int,
) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("HELIX_")
    }
    environment.update(
        {
            "HELIX_ARAPI_BRIDGE_PORT": str(bridge_port),
            "HELIX_ARAPI_TEST_DATA": "true",
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "helix_mcp.server", "--dotenv", str(dotenv_path)],
        cwd=PROJECT_ROOT,
        env=environment,
    )

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        async with (
            stdio_client(parameters, errlog=stderr) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS

            await _assert_targets_and_health(session)
            await _assert_reads_and_errors(session)
            await _assert_two_phase_sql(session)
            await _assert_two_phase_writes(session)

        stderr.seek(0)
        captured = stderr.read()

    await _wait_until_port_closed(bridge_port)
    audit_text = audit_path.read_text(encoding="utf-8")
    metrics_text = (audit_path.parent / "metrics.json").read_text(
        encoding="utf-8"
    )
    operation_text = (audit_path.parent / "operations.jsonl").read_text(
        encoding="utf-8"
    )
    audit_events = [
        json.loads(line) for line in audit_text.splitlines() if line
    ]
    assert audit_events
    assert all(event["event"] == "tool_call" for event in audit_events)
    assert {event["outcome"] for event in audit_events} == {
        "error",
        "success",
    }
    assert any(
        event["tool"] == "apply_create_entry" and event["outcome"] == "success"
        for event in audit_events
    )
    _assert_no_business_data(captured)
    _assert_no_business_data(audit_text)
    _assert_no_business_data(metrics_text)
    _assert_no_business_data(operation_text)
    metrics = json.loads(metrics_text)
    assert metrics["schema_version"] == 1
    assert any(
        item["tool"] == "apply_create_entry" and item["count"] >= 1
        for item in metrics["tools"]
    )
    operation_events = [
        json.loads(line) for line in operation_text.splitlines() if line
    ]
    assert any(
        event["event"] == "application_ready" for event in operation_events
    )
    assert any(
        event["event"] == "application_stopped" for event in operation_events
    )
    assert all("message" not in event for event in operation_events)


async def _write_plan_restart_flow(
    *,
    dotenv_path: Path,
    bridge_port: int,
) -> None:
    parameters = _stdio_parameters(dotenv_path, bridge_port)
    values = {
        "Name": "persisted-across-stdio-restart",
        "Description": "planned before the server restart",
    }
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        try:
            async with (
                stdio_client(parameters, errlog=stderr) as streams,
                ClientSession(*streams) as session,
            ):
                await session.initialize()
                plan = await session.call_tool(
                    "plan_create_entry",
                    {
                        "environment": "dev",
                        "form": FORM,
                        "values": values,
                        "reason": "verify persistence across MCP restart",
                    },
                )
                assert plan.isError is False
                persisted = dict(plan.structuredContent)
        except BaseExceptionGroup as exc:
            stderr.seek(0)
            raise AssertionError(stderr.read()) from exc

        await _wait_until_port_closed(bridge_port)

        try:
            async with (
                stdio_client(parameters, errlog=stderr) as streams,
                ClientSession(*streams) as session,
            ):
                await session.initialize()
                recovered = await session.call_tool(
                    "get_write_plan",
                    {
                        "environment": "dev",
                        "plan_id": persisted["plan_id"],
                    },
                )
                assert recovered.isError is False
                assert recovered.structuredContent["status"] == "pending"
                assert recovered.structuredContent["proposed_values"] == values
                applied = await session.call_tool(
                    "apply_create_entry",
                    {
                        "environment": "dev",
                        "plan_id": persisted["plan_id"],
                        "plan_digest": persisted["plan_digest"],
                    },
                )
                assert applied.isError is False
                assert (
                    applied.structuredContent["entry_id"] == CREATED_ENTRY_ID
                )
        except BaseExceptionGroup as exc:
            stderr.seek(0)
            raise AssertionError(stderr.read()) from exc

        stderr.seek(0)
        captured = stderr.read()

    await _wait_until_port_closed(bridge_port)
    _assert_no_business_data(captured)
    assert "persisted-across-stdio-restart" not in captured


async def _assert_targets_and_health(session: ClientSession) -> None:
    targets = await session.call_tool(
        "list_targets",
        {"include_disabled": False},
    )
    assert targets.isError is False
    by_environment = {
        item["environment"]: item
        for item in targets.structuredContent["targets"]
    }
    assert set(by_environment) == {"dev", "qa", "prod"}
    assert by_environment["dev"]["backends"] == ["arapi"]
    assert by_environment["dev"]["read_only"] is False
    assert by_environment["dev"]["capabilities"]["form_create"] is True
    assert by_environment["dev"]["capabilities"]["form_update"] is True
    assert by_environment["qa"]["read_only"] is True
    assert by_environment["prod"]["read_only"] is True

    health = await session.call_tool(
        "health_check",
        {"environment": "dev", "force_refresh": True},
    )
    assert health.isError is False
    checks = {
        check["component"]: check
        for check in health.structuredContent["checks"]
    }
    assert checks["arapi_bridge"]["status"] == "healthy"


async def _assert_reads_and_errors(session: ClientSession) -> None:
    forms = await session.call_tool(
        "list_forms",
        {
            "environment": "dev",
            "offset": 0,
            "limit": 10,
        },
    )
    assert forms.isError is False
    assert forms.structuredContent["forms"] == [{"name": FORM}]
    assert forms.structuredContent["total"] == 1

    fields = await session.call_tool(
        "list_form_fields",
        {
            "environment": "dev",
            "form": FORM,
            "offset": 0,
            "limit": 10,
        },
    )
    assert fields.isError is False
    assert [
        field["name"] for field in fields.structuredContent["fields"]
    ] == list(FIELDS)

    query = await session.call_tool(
        "query_form",
        {
            "environment": "dev",
            "form": FORM,
            "fields": list(FIELDS),
            "sort": [{"field": "Name", "direction": "asc"}],
            "offset": 0,
            "limit": 10,
            "include_total": True,
        },
    )
    assert query.isError is False
    assert query.structuredContent["total"] == 1
    assert query.structuredContent["entries"][0]["values"] == {
        "Request ID": ENTRY_ID,
        "Name": "sample-system",
        "Count": 7,
        "Enabled": 1,
        "Description": "initial description",
    }

    direct = await _get_entry(session, ENTRY_ID)
    assert direct == {
        "Request ID": ENTRY_ID,
        "Name": "sample-system",
        "Count": 7,
        "Enabled": 1,
        "Description": "initial description",
    }

    over_limit = await session.call_tool(
        "query_form",
        {
            "environment": "dev",
            "form": FORM,
            "fields": list(FIELDS),
            "limit": 11,
        },
    )
    _assert_tool_error(over_limit, "FORM_QUERY_LIMIT_EXCEEDED")

    locked = await session.call_tool(
        "query_form",
        {
            "environment": "qa",
            "form": FORM,
            "fields": ["Name"],
            "limit": 1,
        },
    )
    _assert_tool_error(locked, "FORM_READ_DISABLED")


async def _assert_two_phase_writes(session: ClientSession) -> None:
    create = await session.call_tool(
        "plan_create_entry",
        {
            "environment": "dev",
            "form": FORM,
            "values": {
                "Name": "local-e2e-created",
                "Count": 8,
                "Enabled": False,
                "Description": "created through local E2E",
            },
            "reason": "reproducible local E2E create",
        },
    )
    assert create.isError is False
    assert create.structuredContent["status"] == "pending"
    plan_id = create.structuredContent["plan_id"]
    plan_digest = create.structuredContent["plan_digest"]

    stored = await session.call_tool(
        "get_write_plan",
        {"environment": "dev", "plan_id": plan_id},
    )
    assert stored.isError is False
    assert stored.structuredContent["status"] == "pending"
    assert stored.structuredContent["plan_digest"] == plan_digest

    applied = await session.call_tool(
        "apply_create_entry",
        {
            "environment": "dev",
            "plan_id": plan_id,
            "plan_digest": plan_digest,
        },
    )
    assert applied.isError is False
    assert applied.structuredContent["status"] == "applied"
    assert applied.structuredContent["entry_id"] == CREATED_ENTRY_ID
    assert applied.structuredContent["reused_result"] is False

    replay = await session.call_tool(
        "apply_create_entry",
        {
            "environment": "dev",
            "plan_id": plan_id,
            "plan_digest": plan_digest,
        },
    )
    assert replay.isError is False
    assert replay.structuredContent["reused_result"] is True
    created = await _get_entry(session, CREATED_ENTRY_ID)
    assert created["Name"] == "local-e2e-created"
    assert created["Enabled"] == 0

    update = await _plan_update(
        session,
        values={"Description": "updated through local E2E"},
        reason="reproducible local E2E update",
    )
    updated = await _apply_update(session, update)
    assert updated.isError is False
    assert updated.structuredContent["entry_id"] == ENTRY_ID
    assert (await _get_entry(session, ENTRY_ID))["Description"] == (
        "updated through local E2E"
    )

    first = await _plan_update(
        session,
        values={"Name": "first-concurrent-update"},
        reason="first reproducible conflict plan",
    )
    stale = await _plan_update(
        session,
        values={"Name": "stale-concurrent-update"},
        reason="second reproducible conflict plan",
    )
    assert (await _apply_update(session, first)).isError is False
    conflict = await _apply_update(session, stale)
    _assert_tool_error(conflict, "FORM_WRITE_CONFLICT")

    cancellable = await session.call_tool(
        "plan_create_entry",
        {
            "environment": "dev",
            "form": FORM,
            "values": {"Name": "cancelled-local-e2e"},
            "reason": "reproducible local E2E cancellation",
        },
    )
    assert cancellable.isError is False
    cancelled = await session.call_tool(
        "cancel_write_plan",
        {
            "environment": "dev",
            "plan_id": cancellable.structuredContent["plan_id"],
        },
    )
    assert cancelled.isError is False
    assert cancelled.structuredContent["status"] == "cancelled"

    production_write = await session.call_tool(
        "plan_create_entry",
        {
            "environment": "prod",
            "form": FORM,
            "values": {"Name": "must-not-be-created"},
            "reason": "verify production remains read only",
        },
    )
    _assert_tool_error(production_write, "FORM_WRITE_DISABLED")


async def _assert_two_phase_sql(session: ClientSession) -> None:
    sql = "SELECT id AS id, name AS name FROM public.sample_table"
    planned = await session.call_tool(
        "plan_sql_query",
        {"environment": "dev", "sql": sql, "limit": 10},
    )
    assert planned.isError is False
    assert planned.structuredContent["sql"] == sql
    assert planned.structuredContent["status"] == "pending"

    stored = await session.call_tool(
        "get_sql_query_plan",
        {
            "environment": "dev",
            "plan_id": planned.structuredContent["plan_id"],
        },
    )
    assert stored.isError is False
    assert (
        stored.structuredContent["plan_digest"]
        == (planned.structuredContent["plan_digest"])
    )

    executed = await session.call_tool(
        "execute_sql_query",
        {
            "environment": "dev",
            "plan_id": planned.structuredContent["plan_id"],
            "plan_digest": planned.structuredContent["plan_digest"],
        },
    )
    assert executed.isError is False
    assert executed.structuredContent["columns"] == ["id", "name"]
    assert executed.structuredContent["rows"] == [
        {"id": 7, "name": "sample-system"}
    ]

    locked = await session.call_tool(
        "plan_sql_query",
        {"environment": "qa", "sql": sql, "limit": 1},
    )
    _assert_tool_error(locked, "DATABASE_READ_DISABLED")


async def _plan_update(
    session: ClientSession,
    *,
    values: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    result = await session.call_tool(
        "plan_update_entry",
        {
            "environment": "dev",
            "form": FORM,
            "entry_id": ENTRY_ID,
            "values": values,
            "reason": reason,
        },
    )
    assert result.isError is False
    assert result.structuredContent["status"] == "pending"
    return result.structuredContent


async def _apply_update(
    session: ClientSession,
    plan: dict[str, Any],
) -> Any:
    return await session.call_tool(
        "apply_update_entry",
        {
            "environment": "dev",
            "plan_id": plan["plan_id"],
            "plan_digest": plan["plan_digest"],
        },
    )


async def _get_entry(
    session: ClientSession,
    entry_id: str,
) -> dict[str, Any]:
    result = await session.call_tool(
        "get_entry",
        {
            "environment": "dev",
            "form": FORM,
            "entry_id": entry_id,
            "fields": list(FIELDS),
        },
    )
    assert result.isError is False
    assert result.structuredContent["entry_id"] == entry_id
    return result.structuredContent["entry"]["values"]


def _assert_tool_error(result: Any, code: str) -> None:
    assert result.isError is True
    text = " ".join(getattr(item, "text", "") for item in result.content)
    assert f"code={code}" in text
    assert FORM not in text
    assert ENTRY_ID not in text


async def _wait_until_port_closed(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.1)
            if connection.connect_ex(("127.0.0.1", port)) != 0:
                return
        await asyncio.sleep(0.05)
    raise AssertionError("managed Java bridge was not stopped")


def _assert_no_business_data(text: str) -> None:
    forbidden = (
        "HELIX_CREDENTIAL_",
        USERNAME,
        PASSWORD,
        FORM,
        ENTRY_ID,
        CREATED_ENTRY_ID,
        "local-e2e-created",
        "updated through local E2E",
        "first-concurrent-update",
        "stale-concurrent-update",
    )
    assert all(value not in text for value in forbidden)


def _write_config(path: Path, bridge_port: int) -> None:
    path.write_text(
        f"""schema_version: 2

server:
  transport: stdio
  log_level: INFO
  metadata_cache_ttl_seconds: 0
  health_cache_ttl_seconds: 0
  write_plan_ttl_seconds: 600
  max_pending_write_plans: 20

policies:
  - name: local_e2e_read_write
    allow_all_forms: false
    allow_all_fields: false
    allowed_forms:
      - {FORM}
    allowed_fields_by_form:
      {FORM}:
        - Request ID
        - Name
        - Count
        - Enabled
        - Description
    writable_forms:
      - {FORM}
    creatable_fields_by_form:
      {FORM}:
        - Name
        - Count
        - Enabled
        - Description
    updatable_fields_by_form:
      {FORM}:
        - Name
        - Count
        - Enabled
        - Description
    allow_all_sql_objects: true
    allowed_sql_objects: []
    allow_form_reads: true
    allow_sql: true
    access_mode: read_write
    require_human_approval: true
    require_write_reason: true
    max_rows: 10
    query_timeout_seconds: 5
    rate_limit_per_minute: 100
    write_rate_limit_per_minute: 100

  - name: local_e2e_locked
    allow_all_forms: false
    allow_all_fields: false
    allowed_forms: []
    allowed_fields_by_form: {{}}
    writable_forms: []
    creatable_fields_by_form: {{}}
    updatable_fields_by_form: {{}}
    allow_all_sql_objects: false
    allowed_sql_objects: []
    allow_form_reads: false
    allow_sql: false
    access_mode: read_only
    require_human_approval: true
    require_write_reason: true
    max_rows: 10
    query_timeout_seconds: 5
    rate_limit_per_minute: 100
    write_rate_limit_per_minute: 1

policy_by_environment:
  dev: local_e2e_read_write
  qa: local_e2e_locked
  prod: local_e2e_locked

arapi:
  bridge_base_url: http://127.0.0.1:{bridge_port}
  request_timeout_seconds: 5
  pool_size: 2

""",
        encoding="utf-8",
    )


def _write_dotenv(
    path: Path,
    *,
    config_path: Path,
    bridge_jar: Path,
    library_dir: Path,
    audit_path: Path,
    write_plan_db: Path,
    write_plan_key: Path,
) -> None:
    credential = json.dumps(
        {"username": USERNAME, "password": PASSWORD},
        separators=(",", ":"),
    )
    path.write_text(
        f"HELIX_CONFIG_PATH='{config_path}'\n"
        f"HELIX_ARAPI_BRIDGE_JAR_PATH='{bridge_jar}'\n"
        f"HELIX_ARAPI_LIB_DIR='{library_dir}'\n"
        f"HELIX_AUDIT_LOG_PATH='{audit_path}'\n"
        f"HELIX_METRICS_PATH='{audit_path.parent / 'metrics.json'}'\n"
        f"HELIX_OPERATION_LOG_PATH='{audit_path.parent / 'operations.jsonl'}'\n"
        "HELIX_OPERATION_LOG_MAX_BYTES=10485760\n"
        "HELIX_OPERATION_LOG_BACKUP_COUNT=5\n"
        f"HELIX_WRITE_PLAN_DB_PATH='{write_plan_db}'\n"
        f"HELIX_WRITE_PLAN_KEY_PATH='{write_plan_key}'\n"
        f"HELIX_CREDENTIAL_DEV='{credential}'\n"
        f"HELIX_CREDENTIAL_QA='{credential}'\n"
        f"HELIX_CREDENTIAL_PROD='{credential}'\n",
        encoding="utf-8",
    )


def _write_plan_key(tmp_path: Path) -> Path:
    path = tmp_path / "write-plans.key"
    path.write_bytes(b"local-e2e-encryption-key-value!!")
    path.chmod(0o600)
    return path


def _stdio_parameters(
    dotenv_path: Path,
    bridge_port: int,
) -> StdioServerParameters:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("HELIX_")
    }
    environment.update(
        {
            "HELIX_ARAPI_BRIDGE_PORT": str(bridge_port),
            "HELIX_ARAPI_TEST_DATA": "true",
        }
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "helix_mcp.server", "--dotenv", str(dotenv_path)],
        cwd=PROJECT_ROOT,
        env=environment,
    )
