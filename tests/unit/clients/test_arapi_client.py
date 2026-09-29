"""Tests for the loopback ARAPI bridge client."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from helix_mcp.clients.arapi import (
    ArapiAdminRequiredError,
    ArapiBridgeClient,
    ArapiBridgeClosedError,
    ArapiBridgeConflictError,
    ArapiBridgeProtocolError,
    ArapiFieldAmbiguousError,
    ArapiFieldNotQueryableError,
    ArapiFormNotFoundError,
)
from helix_mcp.config import (
    ArapiBackendConfig,
    Environment,
    SecretProviderKind,
    SecretRef,
    TargetKey,
)
from helix_mcp.secrets import EnvironmentSecretProvider, SecretResolver

TARGET = TargetKey(instance="helix", environment=Environment.DEV)
SECRET_NAME = "HELIX_CREDENTIAL_DEV"


def run(coroutine):
    return asyncio.run(coroutine)


def config() -> ArapiBackendConfig:
    return ArapiBackendConfig(
        bridge_base_url="http://127.0.0.1:8090",
        gateway_host="127.0.0.1",
        gateway_port=46000,
        credentials=SecretRef(
            provider=SecretProviderKind.ENVIRONMENT,
            key=SECRET_NAME,
        ),
    )


def secrets() -> SecretResolver:
    return SecretResolver(
        [
            EnvironmentSecretProvider(
                {
                    SECRET_NAME: (
                        '{"username":"service-user",'
                        '"password":"private-password",'
                        '"domain":"CORPORATE"}'
                    )
                }
            )
        ]
    )


def test_declared_oversized_response_is_rejected_before_buffering() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(8 * 1024 * 1024 + 1)},
            content=b"",
        )

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError, match="safety limit"):
            await client.list_forms()
        await client.aclose()

    run(scenario())


def test_sql_query_uses_positional_contract_and_detects_admin_requirement() -> (
    None
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={"rows": [[7, "sample"], [8, None]], "truncated": True},
            )
        return httpx.Response(
            403,
            json={
                "error": "ARAPI administrator permission required",
                "code": "ARAPI_ADMIN_REQUIRED",
                "private": "must-not-escape",
            },
        )

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        result = await client.query_sql(
            sql="SELECT id AS id, name AS name FROM sample",
            column_count=2,
            limit=2,
            timeout_seconds=120,
        )
        assert result.rows == ((7, "sample"), (8, None))
        assert result.truncated is True
        assert "sample" not in repr(result)
        with pytest.raises(ArapiAdminRequiredError) as exc_info:
            await client.query_sql(
                sql="SELECT id AS id FROM sample",
                column_count=1,
                limit=1,
                timeout_seconds=120,
            )
        assert exc_info.value.code == "ARAPI_ADMIN_REQUIRED"
        assert "must-not-escape" not in str(exc_info.value)
        await client.aclose()

    run(scenario())
    form = parse_qs(requests[0].content.decode("ascii"))
    assert form["sql"] == ["SELECT id AS id, name AS name FROM sample"]
    assert form["column_count"] == ["2"]
    assert form["limit"] == ["2"]


@pytest.mark.parametrize(
    "payload",
    [
        {"rows": [[1]], "truncated": "false"},
        {"rows": [[1, 2]], "truncated": False},
        {"rows": [[float("inf")]], "truncated": False},
        {"rows": [], "truncated": False, "extra": True},
    ],
)
def test_sql_query_rejects_malformed_bridge_payload(payload: object) -> None:
    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=json.dumps(payload).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError):
            await client.query_sql(
                sql="SELECT id AS id FROM sample",
                column_count=1,
                limit=1,
                timeout_seconds=120,
            )
        await client.aclose()

    run(scenario())


def test_list_forms_sends_expected_loopback_contract_and_validates_output() -> (
    None
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "forms": ["Example:BaseElement", "Example:HelpDesk"],
                "total": 2,
            },
        )

    async def scenario() -> tuple[str, ...]:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        result = await client.list_forms()
        await client.aclose()
        return result

    assert run(scenario()) == (
        "Example:BaseElement",
        "Example:HelpDesk",
    )
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/forms"
    assert parse_qs(requests[0].content.decode("ascii")) == {
        "host": ["127.0.0.1"],
        "port": ["46000"],
        "username": ["service-user"],
        "password": ["private-password"],
        "authentication": ["CORPORATE"],
    }


def test_field_query_and_entry_contracts_are_typed_and_bounded() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/fields":
            return httpx.Response(
                200,
                json={
                    "fields": [
                        {"id": 1, "name": "Request ID", "datatype": "CHAR"},
                        {"id": 7, "name": "Status", "datatype": "ENUM"},
                    ],
                    "total": 2,
                },
            )
        if request.url.path == "/v1/fields/selection-values":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "name": "Status",
                    "datatype": "ENUM",
                    "selection_style": "custom",
                    "values": [
                        {"number": 0, "label": "New"},
                        {"number": 7, "label": "Resolved"},
                    ],
                },
            )
        if request.url.path == "/v1/fields/menu-metadata":
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "name": "Owner",
                    "datatype": "CHAR",
                    "has_menu": True,
                    "menu_name": "Sample:Owners",
                    "menu_style": "append",
                },
            )
        if request.url.path == "/v1/fields/menu-values":
            return httpx.Response(
                200,
                json={
                    "id": 2,
                    "name": "Owner",
                    "datatype": "CHAR",
                    "has_menu": True,
                    "menu_name": "Sample:Owners",
                    "menu_style": "append",
                    "menu_type": "query",
                    "values": [
                        {
                            "label": "Primary owner",
                            "value": "owner-one",
                            "path": [],
                        },
                        {
                            "label": "Secondary owner",
                            "value": "owner-two",
                            "path": ["Other owners"],
                        },
                    ],
                    "limit": 10,
                    "truncated": False,
                },
            )
        if request.url.path == "/v1/entries/query":
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            "values": {
                                "Request ID": "000000000000001",
                                "Status": 1,
                            }
                        }
                    ],
                    "offset": 5,
                    "limit": 10,
                    "total": 21,
                },
            )
        return httpx.Response(
            200,
            json={
                "entry_id": "000000000000001",
                "entry": {
                    "values": {
                        "Request ID": "000000000000001",
                        "Status": 1,
                    }
                },
            },
        )

    async def scenario():
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        fields = await client.list_fields("Example:HelpDesk")
        selection = await client.get_field_selection_values(
            form="Example:HelpDesk",
            field_id=7,
        )
        menu = await client.get_field_menu_metadata(
            form="Example:HelpDesk",
            field_id=2,
        )
        menu_values = await client.resolve_field_menu_values(
            form="Example:HelpDesk",
            field_id=2,
            limit=10,
        )
        page = await client.query_entries(
            form="Example:HelpDesk",
            fields=("Request ID", "Status"),
            qualification="'Status' = 1",
            sort=(("Request ID", "desc"),),
            offset=5,
            limit=10,
            include_total=True,
        )
        entry = await client.get_entry(
            form="Example:HelpDesk",
            entry_id="000000000000001",
            fields=("Request ID", "Status"),
        )
        await client.aclose()
        return fields, selection, menu, menu_values, page, entry

    fields, selection, menu, menu_values, page, entry = run(scenario())
    assert [(field.id, field.name, field.datatype) for field in fields] == [
        (1, "Request ID", "CHAR"),
        (7, "Status", "ENUM"),
    ]
    assert page.total == 21
    assert selection.id == 7
    assert selection.selection_style == "custom"
    assert [(item.number, item.label) for item in selection.values] == [
        (0, "New"),
        (7, "Resolved"),
    ]
    assert menu.id == 2
    assert menu.datatype == "CHAR"
    assert menu.has_menu is True
    assert menu.menu_name == "Sample:Owners"
    assert menu.menu_style == "append"
    assert menu_values.menu_type == "query"
    assert [
        (item.label, item.value, item.path) for item in menu_values.values
    ] == [
        ("Primary owner", "owner-one", ()),
        ("Secondary owner", "owner-two", ("Other owners",)),
    ]
    assert "owner-one" not in repr(menu_values)
    assert page.entries[0].values == {
        "Request ID": "000000000000001",
        "Status": 1,
    }
    assert entry.entry.values == page.entries[0].values
    assert "000000000000001" not in repr(page.entries[0])
    assert [request.url.path for request in requests] == [
        "/v1/fields",
        "/v1/fields/selection-values",
        "/v1/fields/menu-metadata",
        "/v1/fields/menu-values",
        "/v1/entries/query",
        "/v1/entries/get",
    ]
    selection_form = parse_qs(requests[1].content.decode("ascii"))
    assert selection_form["form"] == ["Example:HelpDesk"]
    assert selection_form["field_id"] == ["7"]
    menu_form = parse_qs(requests[2].content.decode("ascii"))
    assert menu_form["form"] == ["Example:HelpDesk"]
    assert menu_form["field_id"] == ["2"]
    menu_values_form = parse_qs(requests[3].content.decode("ascii"))
    assert menu_values_form["form"] == ["Example:HelpDesk"]
    assert menu_values_form["field_id"] == ["2"]
    assert menu_values_form["limit"] == ["10"]
    query_form = parse_qs(requests[4].content.decode("ascii"))
    assert query_form["form"] == ["Example:HelpDesk"]
    assert query_form["fields"] == ["Request ID,Status"]
    assert query_form["qualification"] == ["'Status' = 1"]
    assert query_form["sort"] == ["Request ID.desc"]
    assert query_form["offset"] == ["5"]
    assert query_form["limit"] == ["10"]
    assert query_form["include_total"] == ["true"]


def test_field_metadata_accepts_duplicate_names_with_distinct_ids() -> None:
    async def scenario():
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "fields": [
                            {
                                "id": 100,
                                "name": " Status ",
                                "datatype": "ENUM",
                            },
                            {"id": 200, "name": "Status", "datatype": "CHAR"},
                        ],
                        "total": 2,
                    },
                )
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        fields = await client.list_fields("Example:ComputerSystem")
        await client.aclose()
        return fields

    fields = run(scenario())
    assert [(field.id, field.name) for field in fields] == [
        (100, "Status"),
        (200, "Status"),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": 7,
            "name": "Status",
            "datatype": "ENUM",
            "selection_style": None,
            "values": [{"number": 0, "label": "New"}],
        },
        {
            "id": 7,
            "name": "Status",
            "datatype": "ENUM",
            "selection_style": "custom",
            "values": [
                {"number": 0, "label": "New"},
                {"number": 0, "label": "Duplicate"},
            ],
        },
        {
            "id": 7,
            "name": "Status",
            "datatype": "ENUM",
            "selection_style": "dynamic",
            "values": [],
        },
        {
            "id": 7,
            "name": "Status",
            "datatype": "ENUM",
            "selection_style": "custom",
            "values": [{"number": 2_147_483_648, "label": "Invalid"}],
        },
    ],
)
def test_selection_metadata_rejects_malformed_bridge_payloads(payload) -> None:
    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError):
            await client.get_field_selection_values(
                form="Example:HelpDesk",
                field_id=7,
            )
        await client.aclose()

    run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": 2,
            "name": "Owner",
            "datatype": "CHAR",
            "has_menu": False,
            "menu_name": "Sample:Owners",
            "menu_style": None,
        },
        {
            "id": 2,
            "name": "Owner",
            "datatype": "CHAR",
            "has_menu": True,
            "menu_name": None,
            "menu_style": "append",
        },
        {
            "id": 2,
            "name": "Owner",
            "datatype": "CHAR",
            "has_menu": True,
            "menu_name": "Sample:Owners",
            "menu_style": "dynamic",
        },
    ],
)
def test_character_menu_rejects_malformed_bridge_payloads(payload) -> None:
    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError):
            await client.get_field_menu_metadata(
                form="Example:HelpDesk",
                field_id=2,
            )
        await client.aclose()

    run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": 2,
            "name": "Owner",
            "datatype": "CHAR",
            "has_menu": False,
            "menu_name": None,
            "menu_style": None,
            "menu_type": None,
            "values": [{"label": "Unexpected", "value": "value", "path": []}],
            "limit": 10,
            "truncated": False,
        },
        {
            "id": 2,
            "name": "Owner",
            "datatype": "CHAR",
            "has_menu": True,
            "menu_name": "Sample:Owners",
            "menu_style": "append",
            "menu_type": "unsupported",
            "values": [],
            "limit": 10,
            "truncated": False,
        },
        {
            "id": 2,
            "name": "Owner",
            "datatype": "CHAR",
            "has_menu": True,
            "menu_name": "Sample:Owners",
            "menu_style": "append",
            "menu_type": "query",
            "values": [{"label": "Owner", "value": "value", "path": []}],
            "limit": 11,
            "truncated": False,
        },
        {
            "id": 2,
            "name": "Owner",
            "datatype": "CHAR",
            "has_menu": True,
            "menu_name": "Sample:Owners",
            "menu_style": "append",
            "menu_type": "query",
            "values": [
                {"label": "Owner", "value": "value", "path": "invalid"}
            ],
            "limit": 10,
            "truncated": False,
        },
    ],
)
def test_character_menu_values_reject_malformed_bridge_payloads(
    payload,
) -> None:
    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError):
            await client.resolve_field_menu_values(
                form="Example:HelpDesk",
                field_id=2,
                limit=10,
            )
        await client.aclose()

    run(scenario())


def test_ambiguous_field_bridge_error_has_a_stable_public_code() -> None:
    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    400,
                    json={
                        "error": "ambiguous field name",
                        "code": "FORM_FIELD_AMBIGUOUS",
                        "private": "must-not-escape",
                    },
                )
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiFieldAmbiguousError) as exc_info:
            await client.get_entry(
                form="Example:ComputerSystem",
                entry_id="000000000000001",
                fields=("Status",),
            )
        assert exc_info.value.code == "FORM_FIELD_AMBIGUOUS"
        assert "must-not-escape" not in str(exc_info.value)
        await client.aclose()

    run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {
            "entries": [{"values": {"Status": 1, "Unexpected": "secret"}}],
            "offset": 0,
            "limit": 1,
        },
        {
            "entries": [{"values": {"Status": 1}}],
            "offset": 0,
            "limit": 1,
            "total": True,
        },
        {
            "entries": [{"values": {"Status": 1}}],
            "offset": 1,
            "limit": 1,
        },
    ],
)
def test_query_rejects_malformed_bridge_payloads(payload) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError):
            await client.query_entries(
                form="Example:HelpDesk",
                fields=("Status",),
                qualification=None,
                sort=(),
                offset=0,
                limit=1,
                include_total=False,
            )
        await client.aclose()

    run(scenario())


def test_prepare_create_and_update_write_contracts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/entries/prepare-update":
            return httpx.Response(
                200,
                json={
                    "entry_id": "000000000000123",
                    "entry": {
                        "values": {
                            "DatasetId": "TEST.ASSET",
                            "ShortDescription": "target",
                        }
                    },
                    "precondition": "1785502800",
                },
            )
        if request.url.path == "/v1/entries/create":
            return httpx.Response(
                200,
                json={"entry_id": "000000000000999"},
            )
        return httpx.Response(
            200,
            json={"entry_id": "000000000000123"},
        )

    async def scenario():
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        prepared = await client.prepare_update(
            form="Example:ComputerSystem",
            entry_id="000000000000123",
            fields=("DatasetId", "ShortDescription"),
        )
        created = await client.create_entry(
            form="Example:ComputerSystem",
            values={
                "DatasetId": "TEST.SAMPLE",
                "ShortDescription": "target",
            },
        )
        await client.update_entry(
            form="Example:ComputerSystem",
            entry_id="000000000000123",
            values={"ShortDescription": "updated"},
            precondition=prepared.precondition,
        )
        await client.aclose()
        return prepared, created

    prepared, created = run(scenario())
    assert prepared.entry.values == {
        "DatasetId": "TEST.ASSET",
        "ShortDescription": "target",
    }
    assert prepared.precondition == "1785502800"
    assert "1785502800" not in repr(prepared)
    assert created == "000000000000999"
    assert [request.url.path for request in requests] == [
        "/v1/entries/prepare-update",
        "/v1/entries/create",
        "/v1/entries/update",
    ]
    create_form = parse_qs(
        requests[1].content.decode("ascii"),
        keep_blank_values=True,
    )
    assert create_form["value_count"] == ["2"]
    assert create_form["field_0"] == ["DatasetId"]
    assert create_form["value_type_0"] == ["string"]
    assert create_form["value_0"] == ["TEST.SAMPLE"]
    update_form = parse_qs(
        requests[2].content.decode("ascii"),
        keep_blank_values=True,
    )
    assert update_form["entry_id"] == ["000000000000123"]
    assert update_form["precondition"] == ["1785502800"]
    assert update_form["value_count"] == ["1"]
    assert update_form["value_0"] == ["updated"]


def test_create_accepts_a_missing_entry_id_from_compound_form() -> None:
    async def scenario() -> str | None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"entry_id": None},
                )
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        result = await client.create_entry(
            form="Example:ComputerSystem",
            values={"ShortDescription": "target"},
        )
        await client.aclose()
        return result

    assert run(scenario()) is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"entry_id": ""},
        {"entry_id": None, "unexpected": True},
    ],
)
def test_create_rejects_malformed_write_result(payload) -> None:
    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError):
            await client.create_entry(
                form="Example:ComputerSystem",
                values={"ShortDescription": "target"},
            )
        await client.aclose()

    run(scenario())


def test_update_conflict_is_distinct_and_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": "ARAPI operation failed",
                "codes": [309],
                "private": "must-not-escape",
            },
        )

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeConflictError) as exc_info:
            await client.update_entry(
                form="Example:ComputerSystem",
                entry_id="000000000000123",
                values={"ShortDescription": "updated"},
                precondition="1785502800",
            )
        assert "must-not-escape" not in str(exc_info.value)
        await client.aclose()

    run(scenario())


def test_arerr_303_becomes_a_sanitized_form_not_found_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={
                "error": "ARAPI operation failed",
                "codes": [303],
                "private": "must-not-escape",
            },
        )

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiFormNotFoundError) as exc_info:
            await client.list_fields("Missing:Form")
        assert exc_info.value.code == "FORM_NOT_FOUND"
        assert exc_info.value.status_code == 502
        assert "must-not-escape" not in str(exc_info.value)
        await client.aclose()

    run(scenario())


def test_arerr_286_becomes_a_sanitized_field_not_queryable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={
                "error": "ARAPI operation failed",
                "codes": [286],
                "private": "must-not-escape",
            },
        )

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiFieldNotQueryableError) as exc_info:
            await client.query_entries(
                form="Example:ComputerSystem",
                fields=("Request ID",),
                qualification="'DataSet Name' = \"TEST.ASSET\"",
                sort=(),
                offset=0,
                limit=1,
                include_total=False,
            )
        assert exc_info.value.code == "FORM_FIELD_NOT_QUERYABLE"
        assert exc_info.value.status_code == 502
        assert "must-not-escape" not in str(exc_info.value)
        await client.aclose()

    run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "different error", "codes": [303]},
        {"error": "ARAPI operation failed", "codes": [True]},
        {"error": "ARAPI operation failed", "codes": "303"},
    ],
)
def test_malformed_arerr_303_remains_a_protocol_error(payload) -> None:
    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(502, json=payload)
            ),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError):
            await client.list_fields("Missing:Form")
        await client.aclose()

    run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {
            "entry_id": "000000000000123",
            "entry": {"values": {"ShortDescription": "old"}},
            "precondition": "not-a-time",
        },
        {
            "entry_id": "000000000000123",
            "entry": {"values": {"Unexpected": "old"}},
            "precondition": "1785502800",
        },
    ],
)
def test_prepare_update_rejects_invalid_payload(payload) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError):
            await client.prepare_update(
                form="Example:ComputerSystem",
                entry_id="000000000000123",
                fields=("ShortDescription",),
            )
        await client.aclose()

    run(scenario())


def test_probe_bridge_validates_the_liveness_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"})

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        await client.probe_bridge()
        await client.aclose()

    run(scenario())
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/health"
    assert requests[0].content == b""


def test_probe_bridge_rejects_invalid_health_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "private-detail"})

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError) as exc_info:
            await client.probe_bridge()
        assert "private-detail" not in str(exc_info.value)
        await client.aclose()

    run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"forms": ["One"], "total": 2},
        {"forms": ["One", "one"], "total": 2},
        {"forms": ["Bad\nName"], "total": 1},
        {"forms": "not-a-list", "total": 1},
    ],
)
def test_invalid_bridge_payload_is_rejected_without_exposure(payload) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        transport = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(handler),
        )
        client = ArapiBridgeClient(
            target=TARGET,
            config=config(),
            secrets=secrets(),
            http_client=transport,
        )
        with pytest.raises(ArapiBridgeProtocolError) as exc_info:
            await client.list_forms()
        assert "Bad" not in str(exc_info.value)
        await client.aclose()
        with pytest.raises(ArapiBridgeClosedError):
            await client.list_forms()

    run(scenario())
