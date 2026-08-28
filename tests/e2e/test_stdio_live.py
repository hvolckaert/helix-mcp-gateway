"""Opt-in, read-only MCP stdio validation against a controlled DEV target."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.support.live_validation import live_query_qualification

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE_ENABLED = os.environ.get("HELIX_LIVE_TESTS") == "1"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not LIVE_ENABLED,
        reason="set HELIX_LIVE_TESTS=1 for the read-only live test",
    ),
]


def test_stdio_read_only_flow() -> None:
    """Launch the real server and read one explicitly configured entry."""

    asyncio.run(_stdio_read_only_flow())


async def _stdio_read_only_flow() -> None:
    environment = _required("HELIX_LIVE_ENVIRONMENT")
    form = _required("HELIX_LIVE_FORM")
    configured_entry_id = os.environ.get(
        "HELIX_LIVE_ENTRY_ID",
        "",
    ).strip()
    qualification = (
        os.environ.get(
            "HELIX_LIVE_QUALIFICATION",
            "",
        ).strip()
        or None
    )
    fields = tuple(
        field.strip()
        for field in _required("HELIX_LIVE_FIELDS").split(",")
        if field.strip()
    )
    if not fields:
        raise AssertionError(
            "HELIX_LIVE_FIELDS must contain at least one field"
        )
    database_object = os.environ.get(
        "HELIX_LIVE_DATABASE_OBJECT",
        "",
    ).strip()
    write_plan_values = _optional_write_values()
    write_plan_form = ""
    write_plan_entry_id = ""
    if write_plan_values is not None:
        write_plan_form = _required("HELIX_LIVE_WRITE_PLAN_FORM")
        write_plan_entry_id = _required("HELIX_LIVE_WRITE_PLAN_ENTRY_ID")

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "helix_mcp.server"],
        cwd=PROJECT_ROOT,
    )
    with tempfile.TemporaryFile(
        mode="w+",
        encoding="utf-8",
    ) as stderr:
        async with (
            stdio_client(parameters, errlog=stderr) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {
                "list_targets",
                "health_check",
                "list_forms",
                "list_form_fields",
                "query_form",
                "get_entry",
                "plan_sql_query",
                "execute_sql_query",
                "get_sql_query_plan",
                "cancel_sql_query_plan",
                "list_database_objects",
                "list_database_columns",
                "describe_database_object",
            }.issubset(names)

            targets = await session.call_tool(
                "list_targets",
                {"include_disabled": False},
            )
            assert targets.isError is False
            assert any(
                target["environment"] == environment
                for target in targets.structuredContent["targets"]
            )

            if database_object:
                schema, separator, object_name = database_object.partition(".")
                if not separator or not schema or not object_name:
                    raise AssertionError(
                        "HELIX_LIVE_DATABASE_OBJECT must be schema.object"
                    )
                database_objects = await session.call_tool(
                    "list_database_objects",
                    {
                        "environment": environment,
                        "schema": schema,
                        "name_contains": object_name,
                        "limit": 10,
                    },
                )
                assert database_objects.isError is False
                assert any(
                    item["schema_name"] == schema
                    and item["name"] == object_name
                    for item in database_objects.structuredContent["objects"]
                )

                database_columns = await session.call_tool(
                    "list_database_columns",
                    {
                        "environment": environment,
                        "schema": schema,
                        "object_name": object_name,
                        "limit": 10,
                    },
                )
                assert database_columns.isError is False
                assert database_columns.structuredContent["columns"]

                database_description = await session.call_tool(
                    "describe_database_object",
                    {
                        "environment": environment,
                        "schema": schema,
                        "object_name": object_name,
                    },
                )
                assert database_description.isError is False
                assert (
                    database_description.structuredContent["object"][
                        "schema_name"
                    ]
                    == schema
                )
                assert (
                    database_description.structuredContent["object"]["name"]
                    == object_name
                )

            health = await session.call_tool(
                "health_check",
                {
                    "environment": environment,
                    "force_refresh": True,
                },
            )
            assert health.isError is False

            forms = await session.call_tool(
                "list_forms",
                {
                    "environment": environment,
                    "name_contains": form,
                    "offset": 0,
                    "limit": 10,
                },
            )
            assert forms.isError is False
            assert forms.structuredContent["offset"] == 0
            assert forms.structuredContent["limit"] == 10
            assert any(
                item["name"] == form
                for item in forms.structuredContent["forms"]
            )

            field_metadata = await session.call_tool(
                "list_form_fields",
                {
                    "environment": environment,
                    "form": form,
                    "name_contains": fields[0],
                    "offset": 0,
                    "limit": 10,
                },
            )
            assert field_metadata.isError is False
            assert field_metadata.structuredContent["offset"] == 0
            assert field_metadata.structuredContent["limit"] == 10
            assert any(
                item["name"] == fields[0]
                for item in field_metadata.structuredContent["fields"]
            )

            id_metadata = await session.call_tool(
                "list_form_fields",
                {
                    "environment": environment,
                    "form": form,
                    "offset": 0,
                    "limit": 1,
                },
            )
            assert id_metadata.isError is False
            id_fields = [
                item["name"]
                for item in id_metadata.structuredContent["fields"]
                if item["id"] == 1
            ]
            assert len(id_fields) == 1
            id_field = id_fields[0]
            query_fields = tuple(dict.fromkeys((*fields, id_field)))
            qualification = live_query_qualification(
                entry_id=configured_entry_id,
                qualification=qualification,
                id_field=id_field,
            )

            query = await session.call_tool(
                "query_form",
                {
                    "environment": environment,
                    "form": form,
                    "fields": list(query_fields),
                    "qualification": qualification,
                    "offset": 0,
                    "limit": 1,
                    "include_total": False,
                },
            )
            assert query.isError is False
            assert query.structuredContent["offset"] == 0
            assert query.structuredContent["limit"] == 1
            assert len(query.structuredContent["entries"]) == 1
            queried_values = query.structuredContent["entries"][0]["values"]
            assert set(queried_values).issubset(query_fields)
            queried_entry_id = queried_values[id_field]
            entry_id = configured_entry_id or queried_entry_id
            assert isinstance(entry_id, str)
            assert entry_id
            assert queried_entry_id == entry_id

            entry = await session.call_tool(
                "get_entry",
                {
                    "environment": environment,
                    "form": form,
                    "entry_id": entry_id,
                    "fields": list(fields),
                },
            )
            assert entry.isError is False
            assert entry.structuredContent["entry_id"] == entry_id
            returned = entry.structuredContent["entry"]["values"]
            assert set(returned).issubset(fields)

            over_limit = await session.call_tool(
                "query_form",
                {
                    "environment": environment,
                    "form": form,
                    "fields": list(fields),
                    "qualification": qualification,
                    "limit": 100_000,
                },
            )
            assert over_limit.isError is True
            failure_text = " ".join(
                getattr(item, "text", "") for item in over_limit.content
            )
            assert "code=FORM_QUERY_LIMIT_EXCEEDED" in failure_text
            assert form not in failure_text
            assert entry_id not in failure_text

            if write_plan_values is not None:
                write_plan = await session.call_tool(
                    "plan_update_entry",
                    {
                        "environment": environment,
                        "form": write_plan_form,
                        "entry_id": write_plan_entry_id,
                        "values": write_plan_values,
                        "reason": "opt-in live write-plan validation",
                    },
                )
                assert write_plan.isError is False
                assert write_plan.structuredContent["status"] == "pending"
                assert write_plan.structuredContent["proposed_values"] == (
                    write_plan_values
                )
                cancelled = await session.call_tool(
                    "cancel_write_plan",
                    {
                        "environment": environment,
                        "plan_id": write_plan.structuredContent["plan_id"],
                    },
                )
                assert cancelled.isError is False
                assert cancelled.structuredContent["status"] == "cancelled"

        stderr.seek(0)
        captured = stderr.read()
    assert "HELIX_CREDENTIAL_" not in captured
    assert '"username"' not in captured
    assert '"password"' not in captured
    assert form not in captured
    assert entry_id not in captured
    assert qualification not in captured
    assert all(field not in captured for field in fields)
    if database_object:
        assert database_object not in captured
    if write_plan_values is not None:
        assert write_plan_form not in captured
        assert write_plan_entry_id not in captured
        assert all(field not in captured for field in write_plan_values)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AssertionError(f"{name} is required for the live test")
    return value


def _optional_write_values() -> dict[str, str | int | float | bool] | None:
    encoded = os.environ.get("HELIX_LIVE_WRITE_PLAN_VALUES", "").strip()
    if not encoded:
        return None
    try:
        parsed = json.loads(encoded)
    except json.JSONDecodeError:
        raise AssertionError(
            "HELIX_LIVE_WRITE_PLAN_VALUES must be a JSON object"
        ) from None
    if (
        not isinstance(parsed, dict)
        or not parsed
        or not all(isinstance(field, str) and field for field in parsed)
        or not all(
            isinstance(value, (str, int, float, bool))
            for value in parsed.values()
        )
    ):
        raise AssertionError(
            "HELIX_LIVE_WRITE_PLAN_VALUES must contain scalar field values"
        )
    return parsed
