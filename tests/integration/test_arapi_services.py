"""Integration of ARAPI targeting, secrets, form reads and form writes."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from helix_mcp.clients.arapi import ArapiBridgeClient
from helix_mcp.config import (
    ArapiBackendConfig,
    BackendKind,
    HelixConfig,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
    TargetPolicyConfig,
)
from helix_mcp.secrets import EnvironmentSecretProvider, SecretResolver
from helix_mcp.services.forms import (
    FormEntryQuery,
    FormFieldsQuery,
    FormQuery,
    FormQueryService,
)
from helix_mcp.services.writes import (
    ApplyWriteRequest,
    FormWriteConflictError,
    FormWriteService,
    UpdateValuesRequest,
    WritePlanStatus,
    WritePlanStore,
    WriteValuesRequest,
)
from helix_mcp.targeting import ResolvedTarget, TargetRegistry, TargetResolver

pytestmark = pytest.mark.integration

FORM = "Example:ComputerSystem"
ENTRY_ID = "000000000000123"
CREATED_ENTRY_ID = "000000000000999"
FIELD = "Name"
FIELD_DATASET = "DatasetId"
FIELD_DESCRIPTION = "ShortDescription"


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class ArapiBridgeStub:
    """Stateful loopback bridge contract backed by in-memory records."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {
            ENTRY_ID: {
                "Request ID": ENTRY_ID,
                FIELD: "existing-computer-system",
                FIELD_DATASET: "TEST.ASSET",
                FIELD_DESCRIPTION: "existing test computer system",
                "Password": "must-never-escape",
                "Unexpected": "must-never-escape",
            }
        }
        self.calls: list[str] = []
        self.create_calls = 0
        self.update_calls = 0
        self.modified_date = 1_785_434_400

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        form = parse_qs(request.content.decode("utf-8"))
        assert form["host"] == ["127.0.0.1"]
        assert form["port"] == ["46000"]
        assert form["username"] == ["integration-user"]
        assert form["password"] == ["integration-password"]
        assert form["form"] == [FORM]

        if request.url.path == "/v1/fields":
            fields = [
                {"id": 1, "name": "Request ID", "datatype": "CHAR"},
                {"id": 2, "name": FIELD, "datatype": "CHAR"},
                {"id": 3, "name": FIELD_DATASET, "datatype": "CHAR"},
                {"id": 4, "name": FIELD_DESCRIPTION, "datatype": "CHAR"},
                {"id": 5, "name": "Password", "datatype": "CHAR"},
            ]
            return httpx.Response(
                200,
                json={"fields": fields, "total": len(fields)},
            )

        if request.url.path == "/v1/entries/create":
            self.create_calls += 1
            self.records[CREATED_ENTRY_ID] = _decode_write_values(form)
            return httpx.Response(
                200,
                json={"entry_id": CREATED_ENTRY_ID},
            )

        if request.url.path == "/v1/entries/update":
            if form["precondition"] != [str(self.modified_date)]:
                return httpx.Response(
                    409,
                    json={"error": "entry changed after it was read"},
                )
            entry_id = form["entry_id"][0]
            self.update_calls += 1
            self.records[entry_id].update(_decode_write_values(form))
            self.modified_date += 1
            return httpx.Response(200, json={"entry_id": entry_id})

        requested = form["fields"][0].split(",")
        if request.url.path == "/v1/entries/prepare-update":
            entry_id = form["entry_id"][0]
            record = self.records[entry_id]
            return httpx.Response(
                200,
                json={
                    "entry_id": entry_id,
                    "entry": {
                        "values": {
                            field: record.get(field) for field in requested
                        }
                    },
                    "precondition": str(self.modified_date),
                },
            )

        if request.url.path == "/v1/entries/query":
            offset = int(form["offset"][0])
            limit = int(form["limit"][0])
            records = list(self.records.values())[offset : offset + limit]
            payload: dict[str, Any] = {
                "entries": [
                    {
                        "values": {
                            field: record.get(field) for field in requested
                        }
                    }
                    for record in records
                ],
                "offset": offset,
                "limit": limit,
            }
            if form["include_total"] == ["true"]:
                payload["total"] = len(self.records)
            return httpx.Response(200, json=payload)

        if request.url.path == "/v1/entries/get":
            entry_id = form["entry_id"][0]
            record = self.records[entry_id]
            return httpx.Response(
                200,
                json={
                    "entry_id": entry_id,
                    "entry": {
                        "values": {
                            field: record.get(field) for field in requested
                        }
                    },
                },
            )
        return httpx.Response(404)


def _decode_write_values(form: dict[str, list[str]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for index in range(int(form["value_count"][0])):
        field = form[f"field_{index}"][0]
        value_type = form[f"value_type_{index}"][0]
        raw = form[f"value_{index}"][0]
        if value_type == "boolean":
            values[field] = raw == "true"
        elif value_type == "integer":
            values[field] = int(raw)
        elif value_type == "number":
            values[field] = float(raw)
        else:
            assert value_type == "string"
            values[field] = raw
    return values


class StaticArapiProvider:
    def __init__(self, client: ArapiBridgeClient) -> None:
        self._client = client

    def get(self, target: ResolvedTarget) -> ArapiBridgeClient:
        assert target.backend is BackendKind.ARAPI
        return self._client


class IntegrationStack:
    def __init__(self) -> None:
        secret_ref = SecretRef(
            provider=SecretProviderKind.ENVIRONMENT,
            key="HELIX_CREDENTIAL_DEV",
        )
        arapi = ArapiBackendConfig(
            bridge_base_url="http://127.0.0.1:8090",
            gateway_host="127.0.0.1",
            gateway_port=46000,
            credentials=secret_ref,
        )
        policy = TargetPolicyConfig(
            name="integration",
            allow_all_forms=True,
            allow_all_fields=True,
            writable_forms=(FORM,),
            creatable_fields_by_form={
                FORM: (FIELD, FIELD_DATASET, FIELD_DESCRIPTION),
            },
            updatable_fields_by_form={
                FORM: (FIELD, FIELD_DATASET, FIELD_DESCRIPTION),
            },
            allow_form_reads=True,
            access_mode="read_write",
            require_human_approval=True,
            require_write_reason=True,
            max_rows=10,
            rate_limit_per_minute=30,
            write_rate_limit_per_minute=10,
            sensitive_fields=("Password",),
        )
        target = TargetConfig(
            environment="dev",
            display_name="Helix integration DEV",
            policy_ref=policy.name,
            arapi=arapi,
        )
        registry = TargetRegistry(
            HelixConfig(policies=(policy,), targets=(target,))
        )
        resolver = TargetResolver(registry)
        secrets = SecretResolver(
            [
                EnvironmentSecretProvider(
                    {
                        "HELIX_CREDENTIAL_DEV": json.dumps(
                            {
                                "username": "integration-user",
                                "password": "integration-password",
                            }
                        )
                    }
                )
            ]
        )
        self.arapi_stub = ArapiBridgeStub()
        self.arapi_http_client = httpx.AsyncClient(
            base_url="http://127.0.0.1:8090",
            transport=httpx.MockTransport(self.arapi_stub),
        )
        self.arapi_client = ArapiBridgeClient(
            target=target.key,
            config=arapi,
            secrets=secrets,
            http_client=self.arapi_http_client,
        )
        arapi_provider = StaticArapiProvider(self.arapi_client)
        self.reads = FormQueryService(
            resolver,
            arapi_provider,
            metadata_cache_ttl_seconds=60,
        )
        self.writes = FormWriteService(
            resolver,
            arapi_provider,
            WritePlanStore(ttl_seconds=600, max_pending=10),
        )

    async def aclose(self) -> None:
        await self.arapi_client.aclose()
        await self.arapi_http_client.aclose()


def test_read_service_uses_real_authenticated_client_and_filters_payloads() -> (
    None
):
    async def scenario() -> None:
        stack = IntegrationStack()
        try:
            fields = await stack.reads.list_fields(
                environment="dev",
                query=FormFieldsQuery(form=FORM, limit=10),
            )
            assert [field.name for field in fields.fields] == [
                "Request ID",
                FIELD,
                FIELD_DATASET,
                FIELD_DESCRIPTION,
            ]

            result = await stack.reads.search(
                environment="dev",
                query=FormQuery(
                    form=FORM,
                    fields=(FIELD, FIELD_DATASET, FIELD_DESCRIPTION),
                    limit=1,
                    include_total=True,
                ),
            )
            assert result.total == 1
            assert result.entries[0].values == {
                FIELD: "existing-computer-system",
                FIELD_DATASET: "TEST.ASSET",
                FIELD_DESCRIPTION: "existing test computer system",
            }

            entry = await stack.reads.get_entry(
                environment="dev",
                query=FormEntryQuery(
                    form=FORM,
                    entry_id=ENTRY_ID,
                    fields=("Request ID", FIELD_DESCRIPTION),
                ),
            )
            assert entry.entry.values == {
                "Request ID": ENTRY_ID,
                FIELD_DESCRIPTION: "existing test computer system",
            }
            assert stack.arapi_stub.calls == [
                "/v1/fields",
                "/v1/entries/query",
                "/v1/entries/get",
            ]
        finally:
            await stack.aclose()

    run(scenario())


def test_write_service_plans_then_applies_via_real_client_idempotently() -> (
    None
):
    async def scenario() -> None:
        stack = IntegrationStack()
        try:
            create_plan = await stack.writes.plan_create_for_form(
                environment="dev",
                form=FORM,
                request=WriteValuesRequest(
                    values={
                        FIELD: "sample-computer-system",
                        FIELD_DATASET: "TEST.SAMPLE",
                        FIELD_DESCRIPTION: "sample computer system",
                    },
                    reason="integration create validation",
                ),
            )
            assert create_plan.status is WritePlanStatus.PENDING
            assert stack.arapi_stub.create_calls == 0

            create_request = ApplyWriteRequest(
                plan_id=create_plan.plan_id,
                plan_digest=create_plan.plan_digest,
            )
            created = await stack.writes.apply_create(
                environment="dev",
                request=create_request,
            )
            reused = await stack.writes.apply_create(
                environment="dev",
                request=create_request,
            )
            assert created.entry_id == CREATED_ENTRY_ID
            assert created.reused_result is False
            assert reused.entry_id == CREATED_ENTRY_ID
            assert reused.reused_result is True
            assert stack.arapi_stub.create_calls == 1

            update_plan = await stack.writes.plan_update(
                environment="dev",
                form=FORM,
                request=UpdateValuesRequest(
                    entry_id=ENTRY_ID,
                    values={FIELD_DESCRIPTION: "updated"},
                    reason="integration update validation",
                ),
            )
            assert update_plan.current_values == {
                FIELD_DESCRIPTION: "existing test computer system"
            }
            updated = await stack.writes.apply_update(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=update_plan.plan_id,
                    plan_digest=update_plan.plan_digest,
                ),
            )
            assert updated.entry_id == ENTRY_ID
            assert (
                stack.arapi_stub.records[ENTRY_ID][FIELD_DESCRIPTION]
                == "updated"
            )
            assert stack.arapi_stub.update_calls == 1
        finally:
            await stack.aclose()

    run(scenario())


def test_stale_update_plan_is_rejected_without_overwriting_winner() -> None:
    async def scenario() -> None:
        stack = IntegrationStack()
        try:
            first = await stack.writes.plan_update(
                environment="dev",
                form=FORM,
                request=UpdateValuesRequest(
                    entry_id=ENTRY_ID,
                    values={FIELD_DESCRIPTION: "winner"},
                    reason="integration concurrency winner",
                ),
            )
            stale = await stack.writes.plan_update(
                environment="dev",
                form=FORM,
                request=UpdateValuesRequest(
                    entry_id=ENTRY_ID,
                    values={FIELD_DESCRIPTION: "stale"},
                    reason="integration concurrency stale plan",
                ),
            )

            await stack.writes.apply_update(
                environment="dev",
                request=ApplyWriteRequest(
                    plan_id=first.plan_id,
                    plan_digest=first.plan_digest,
                ),
            )
            with pytest.raises(FormWriteConflictError):
                await stack.writes.apply_update(
                    environment="dev",
                    request=ApplyWriteRequest(
                        plan_id=stale.plan_id,
                        plan_digest=stale.plan_digest,
                    ),
                )

            assert (
                stack.arapi_stub.records[ENTRY_ID][FIELD_DESCRIPTION]
                == "winner"
            )
            assert stack.arapi_stub.update_calls == 1
        finally:
            await stack.aclose()

    run(scenario())
