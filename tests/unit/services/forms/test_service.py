"""Tests for policy-enforced BMC Helix form queries."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from helix_mcp.clients.arapi import (
    ArapiEntry,
    ArapiEntryResult,
    ArapiField,
    ArapiFormNotFoundError,
    ArapiQueryPage,
)
from helix_mcp.config import (
    ArapiBackendConfig,
    Environment,
    HelixConfig,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
    TargetKey,
    TargetPolicyConfig,
)
from helix_mcp.services.forms import (
    FormEntryQuery,
    FormFieldNotAllowedError,
    FormFieldsQuery,
    FormNotAllowedError,
    FormNotFoundError,
    FormQuery,
    FormQueryLimitError,
    FormQueryService,
    FormRateLimitError,
    FormReadDisabledError,
    FormSort,
)
from helix_mcp.targeting import ResolvedTarget, TargetRegistry, TargetResolver


def run(coroutine):
    return asyncio.run(coroutine)


class FakeClient:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_fields(self, form: str) -> tuple[ArapiField, ...]:
        self.calls.append(("list_fields", {"form": form}))
        return tuple(
            ArapiField(
                id=item["id"],
                name=item["name"],
                datatype=item["datatype"],
            )
            for item in self.payload
        )

    async def query_entries(
        self,
        *,
        form: str,
        fields: tuple[str, ...],
        qualification: str | None,
        sort: tuple[tuple[str, str], ...],
        offset: int,
        limit: int,
        include_total: bool,
    ) -> ArapiQueryPage:
        arguments = {
            "form": form,
            "fields": fields,
            "qualification": qualification,
            "sort": sort,
            "offset": offset,
            "limit": limit,
            "include_total": include_total,
        }
        self.calls.append(("query_entries", arguments))
        if isinstance(self.payload, dict):
            raw_entries = self.payload["entries"]
            total = self.payload.get("numMatches")
        else:
            raw_entries = self.payload
            total = None
        return ArapiQueryPage(
            entries=tuple(
                ArapiEntry(values=dict(item["values"])) for item in raw_entries
            ),
            offset=offset,
            limit=limit,
            total=total,
        )

    async def get_entry(
        self,
        *,
        form: str,
        entry_id: str,
        fields: tuple[str, ...],
    ) -> ArapiEntryResult:
        arguments = {
            "form": form,
            "entry_id": entry_id,
            "fields": fields,
        }
        self.calls.append(("get_entry", arguments))
        return ArapiEntryResult(
            entry_id=entry_id,
            entry=ArapiEntry(values=dict(self.payload["values"])),
        )


class FakeProvider:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.targets: list[ResolvedTarget] = []

    def get(self, target: ResolvedTarget) -> FakeClient:
        self.targets.append(target)
        return self.client


class MissingFormClient(FakeClient):
    async def list_fields(self, form: str) -> tuple[ArapiField, ...]:
        self.calls.append(("list_fields", {"form": form}))
        raise ArapiFormNotFoundError(
            TargetKey(instance="helix", environment=Environment.DEV),
            "form does not exist",
            status_code=502,
        )

    async def list_forms(self) -> tuple[str, ...]:
        self.calls.append(("list_forms", {}))
        return (
            "Example:CompletelyDifferent",
            "Sample:CMDB:INT:FieldMapping",
            "Sample:Unrelated",
        )


def build_service(
    client: FakeClient,
    *,
    allow_reads: bool = True,
    allowed_forms: tuple[str, ...] = ("Example:HelpDesk",),
    allowed_fields: tuple[str, ...] = (
        "Incident Number",
        "Status",
        "Description",
    ),
    sensitive_fields: tuple[str, ...] = ("Password",),
    sensitive_markers: tuple[str, ...] = (),
    allow_all_forms: bool = False,
    allow_all_fields: bool = False,
    max_rows: int = 2,
    rate_limit: int = 60,
    metadata_cache_ttl: int = 0,
    time_source=lambda: 100.0,
) -> tuple[FormQueryService, FakeProvider]:
    policy = TargetPolicyConfig(
        name="form_read",
        allow_all_forms=allow_all_forms,
        allow_all_fields=allow_all_fields,
        allowed_forms=() if allow_all_forms else allowed_forms,
        allowed_fields_by_form=(
            {"Example:HelpDesk": allowed_fields}
            if allowed_fields and not allow_all_fields
            else {}
        ),
        allow_form_reads=allow_reads,
        sensitive_fields=sensitive_fields,
        sensitive_field_markers=sensitive_markers,
        max_rows=max_rows,
        rate_limit_per_minute=rate_limit,
    )
    target = TargetConfig(
        environment="dev",
        display_name="Global DEV",
        policy_ref=policy.name,
        arapi=ArapiBackendConfig(
            bridge_base_url="http://127.0.0.1:8090",
            gateway_host="127.0.0.1",
            gateway_port=46000,
            credentials=SecretRef(
                provider=SecretProviderKind.ENVIRONMENT,
                key="HELIX_CREDENTIAL_DEV",
            ),
        ),
    )
    registry = TargetRegistry(
        HelixConfig(policies=(policy,), targets=(target,))
    )
    provider = FakeProvider(client)
    return (
        FormQueryService(
            TargetResolver(registry),
            provider,
            metadata_cache_ttl_seconds=metadata_cache_ttl,
            time_source=time_source,
        ),
        provider,
    )


def test_search_builds_encoded_bmc_request_and_filters_response_fields() -> (
    None
):
    client = FakeClient(
        {
            "entries": [
                {
                    "values": {
                        "Incident Number": "INC0001",
                        "Status": "Assigned",
                        "Password": "must-not-escape",
                        "Unexpected": "must-not-escape",
                    },
                    "_links": {"self": [{"href": "ignored"}]},
                }
            ],
            "numMatches": 1,
        }
    )
    service, provider = build_service(client)
    query = FormQuery(
        form="Example:HelpDesk",
        fields=("Incident Number", "Status"),
        qualification="'Status' = \"Assigned\"",
        sort=(FormSort(field="Incident Number", direction="desc"),),
        offset=0,
        limit=2,
        include_total=True,
    )

    result = run(
        service.search(
            environment="dev",
            query=query,
        )
    )

    assert result.total == 1
    assert result.entries[0].values == {
        "Incident Number": "INC0001",
        "Status": "Assigned",
    }
    assert client.calls == [
        (
            "query_entries",
            {
                "form": "Example:HelpDesk",
                "fields": ("Incident Number", "Status"),
                "qualification": "'Status' = \"Assigned\"",
                "sort": (("Incident Number", "desc"),),
                "offset": 0,
                "limit": 2,
                "include_total": True,
            },
        )
    ]
    assert provider.targets[-1].backend.value == "arapi"
    assert "Assigned" not in repr(result.entries[0])
    assert "must-not-escape" not in repr(result)


def test_missing_form_returns_only_close_policy_visible_suggestions() -> None:
    client = MissingFormClient([])
    service, _ = build_service(
        client,
        allow_all_forms=True,
        allow_all_fields=True,
    )

    with pytest.raises(FormNotFoundError) as exc_info:
        run(
            service.list_fields(
                environment="dev",
                query=FormFieldsQuery(
                    form="Sample:CHAR:INT:FieldMapping",
                    limit=2,
                ),
            )
        )

    assert exc_info.value.code == "FORM_NOT_FOUND"
    assert exc_info.value.suggestions == ("Sample:CMDB:INT:FieldMapping",)
    assert client.calls == [
        ("list_fields", {"form": "Sample:CHAR:INT:FieldMapping"}),
        ("list_forms", {}),
    ]


def test_get_entry_builds_direct_request_and_filters_response_fields() -> None:
    client = FakeClient(
        {
            "values": {
                "Incident Number": "INC0001",
                "Status": "Assigned",
                "Password": "must-not-escape",
                "Unexpected": "must-not-escape",
            },
            "_links": {"self": [{"href": "ignored"}]},
        }
    )
    service, provider = build_service(client)
    query = FormEntryQuery(
        form="Example:HelpDesk",
        entry_id="INC0001/escaped",
        fields=("Incident Number", "Status"),
    )

    result = run(
        service.get_entry(
            environment="dev",
            query=query,
        )
    )

    assert result.entry_id == "INC0001/escaped"
    assert result.entry.values == {
        "Incident Number": "INC0001",
        "Status": "Assigned",
    }
    assert client.calls == [
        (
            "get_entry",
            {
                "form": "Example:HelpDesk",
                "entry_id": "INC0001/escaped",
                "fields": ("Incident Number", "Status"),
            },
        )
    ]
    assert provider.targets[-1].backend.value == "arapi"
    assert "Assigned" not in repr(result)
    assert "must-not-escape" not in repr(result)


def test_get_entry_blocks_disallowed_fields_before_io() -> None:
    client = FakeClient({})
    service, _ = build_service(client)

    with pytest.raises(FormFieldNotAllowedError):
        run(
            service.get_entry(
                environment="dev",
                query=FormEntryQuery(
                    form="Example:HelpDesk",
                    entry_id="INC0001",
                    fields=("Password",),
                ),
            )
        )
    assert client.calls == []


def test_list_fields_filters_policy_sensitive_names_and_paginates() -> None:
    client = FakeClient(
        [
            {"id": 1, "name": "Incident Number", "datatype": "CHAR"},
            {"id": 7, "name": "Password", "datatype": "CHAR"},
            {"id": 2, "name": "Status", "datatype": "ENUM"},
            {"id": 9, "name": "Unexpected", "datatype": "CHAR"},
        ]
    )
    service, _ = build_service(client, max_rows=2)

    result = run(
        service.list_fields(
            environment="dev",
            query=FormFieldsQuery(
                form="Example:HelpDesk",
                offset=1,
                limit=1,
            ),
        )
    )

    assert result.total == 2
    assert [
        (field.id, field.name, field.datatype) for field in result.fields
    ] == [(2, "Status", "ENUM")]
    assert client.calls == [("list_fields", {"form": "Example:HelpDesk"})]


def test_list_fields_read_all_supports_case_insensitive_name_filter() -> None:
    client = FakeClient(
        [
            {"id": 1, "name": "RequestId", "datatype": "CHAR"},
            {"id": 179, "name": "InstanceId", "datatype": "CHAR"},
            {"id": 180, "name": "Session Token", "datatype": "CHAR"},
        ]
    )
    service, _ = build_service(
        client,
        allowed_forms=(),
        allowed_fields=(),
        sensitive_fields=(),
        sensitive_markers=("token",),
        allow_all_forms=True,
        allow_all_fields=True,
        max_rows=5,
    )

    result = run(
        service.list_fields(
            environment="dev",
            query=FormFieldsQuery(
                form="Example:BaseElement",
                name_contains="ID",
                limit=5,
            ),
        )
    )

    assert result.total == 2
    assert [field.name for field in result.fields] == [
        "RequestId",
        "InstanceId",
    ]


def test_field_metadata_cache_reuses_and_expires_arapi_response() -> None:
    now = [100.0]
    client = FakeClient(
        [
            {"id": 1, "name": "Incident Number", "datatype": "CHAR"},
            {"id": 2, "name": "Status", "datatype": "ENUM"},
        ]
    )
    service, _ = build_service(
        client,
        max_rows=5,
        metadata_cache_ttl=30,
        time_source=lambda: now[0],
    )

    first = run(
        service.list_fields(
            environment="dev",
            query=FormFieldsQuery(
                form="Example:HelpDesk",
                name_contains="Incident",
                limit=5,
            ),
        )
    )
    second = run(
        service.list_fields(
            environment="dev",
            query=FormFieldsQuery(
                form="Example:HelpDesk",
                name_contains="Status",
                limit=5,
            ),
        )
    )
    now[0] += 30.1
    run(
        service.list_fields(
            environment="dev",
            query=FormFieldsQuery(
                form="Example:HelpDesk",
                limit=5,
            ),
        )
    )

    assert [field.name for field in first.fields] == ["Incident Number"]
    assert [field.name for field in second.fields] == ["Status"]
    assert len(client.calls) == 2


def test_policy_blocks_disabled_unlisted_and_disallowed_fields_before_io() -> (
    None
):
    query = FormQuery(form="Example:HelpDesk", fields=("Status",), limit=1)

    disabled_client = FakeClient([])
    disabled, _ = build_service(disabled_client, allow_reads=False)
    with pytest.raises(FormReadDisabledError):
        run(
            disabled.search(
                environment="dev",
                query=query,
            )
        )
    assert disabled_client.calls == []

    unlisted_client = FakeClient([])
    unlisted, _ = build_service(
        unlisted_client,
        allowed_forms=("Example:Change",),
        allowed_fields=(),
    )
    with pytest.raises(FormNotAllowedError):
        run(
            unlisted.search(
                environment="dev",
                query=query,
            )
        )
    assert unlisted_client.calls == []

    field_client = FakeClient([])
    field_service, _ = build_service(field_client)
    forbidden = FormQuery(
        form="Example:HelpDesk",
        fields=("Submitter",),
        limit=1,
    )
    with pytest.raises(FormFieldNotAllowedError):
        run(
            field_service.search(
                environment="dev",
                query=forbidden,
            )
        )
    assert field_client.calls == []


def test_sensitive_field_and_policy_row_limit_are_enforced() -> None:
    client = FakeClient([])
    service, _ = build_service(client)

    with pytest.raises(FormFieldNotAllowedError):
        run(
            service.search(
                environment="dev",
                query=FormQuery(
                    form="Example:HelpDesk",
                    fields=("PASSWORD",),
                    limit=1,
                ),
            )
        )
    with pytest.raises(FormQueryLimitError):
        run(
            service.search(
                environment="dev",
                query=FormQuery(
                    form="Example:HelpDesk",
                    fields=("Status",),
                    limit=3,
                ),
            )
        )
    assert client.calls == []


def test_rate_limit_is_per_target_and_uses_a_sliding_minute() -> None:
    now = [100.0]
    client = FakeClient([])
    service, _ = build_service(
        client,
        rate_limit=2,
        time_source=lambda: now[0],
    )
    query = FormQuery(form="Example:HelpDesk", fields=("Status",), limit=1)

    for _ in range(2):
        run(
            service.search(
                environment="dev",
                query=query,
            )
        )
    with pytest.raises(FormRateLimitError):
        run(
            service.search(
                environment="dev",
                query=query,
            )
        )

    now[0] += 60.1
    run(
        service.search(
            environment="dev",
            query=query,
        )
    )
    assert len(client.calls) == 3


def test_query_models_reject_ambiguous_fields_and_redact_qualification() -> (
    None
):
    with pytest.raises(ValidationError, match="must be unique"):
        FormQuery(
            form="Example:HelpDesk",
            fields=("Status", "status"),
        )
    with pytest.raises(ValidationError, match="commas"):
        FormQuery(
            form="Example:HelpDesk",
            fields=("Status,Password",),
        )
    with pytest.raises(ValidationError, match="control"):
        FormQuery(
            form="Example:HelpDesk",
            fields=("Status",),
            qualification="'Status'=\"Assigned\"\nOR 1=1",
        )

    query = FormQuery(
        form="Example:HelpDesk",
        fields=("Status",),
        qualification="'Secret'=\"private-value\"",
    )
    assert "private-value" not in repr(query)

    with pytest.raises(ValidationError, match="must be unique"):
        FormEntryQuery(
            form="Example:HelpDesk",
            entry_id="INC0001",
            fields=("Status", "status"),
        )
    with pytest.raises(ValidationError, match="control"):
        FormEntryQuery(
            form="Example:HelpDesk",
            entry_id="INC0001\nother",
            fields=("Status",),
        )


def test_explicit_read_all_mode_allows_unknown_forms_but_denies_markers() -> (
    None
):
    client = FakeClient(
        [
            {
                "values": {
                    "Arbitrary Field": "visible",
                    "Session Token Value": "must-not-escape",
                }
            }
        ]
    )
    service, _ = build_service(
        client,
        allowed_forms=(),
        allowed_fields=(),
        sensitive_fields=(),
        sensitive_markers=("token", "password"),
        allow_all_forms=True,
        allow_all_fields=True,
        max_rows=5,
    )

    result = run(
        service.search(
            environment="dev",
            query=FormQuery(
                form="Custom:Previously Unknown",
                fields=("Arbitrary Field",),
                limit=1,
            ),
        )
    )
    assert result.entries[0].values == {"Arbitrary Field": "visible"}

    with pytest.raises(FormFieldNotAllowedError):
        run(
            service.search(
                environment="dev",
                query=FormQuery(
                    form="Custom:Previously Unknown",
                    fields=("Session TOKEN Value",),
                    limit=1,
                ),
            )
        )
    assert len(client.calls) == 1
