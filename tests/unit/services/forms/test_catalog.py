"""Tests for the ARAPI-backed form catalog service."""

from __future__ import annotations

import asyncio

import pytest

from helix_mcp.config import (
    ArapiBackendConfig,
    HelixConfig,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
    TargetPolicyConfig,
)
from helix_mcp.services.forms import (
    FormCatalogQuery,
    FormCatalogService,
    FormQueryLimitError,
    FormReadDisabledError,
)
from helix_mcp.targeting import ResolvedTarget, TargetRegistry, TargetResolver


def run(coroutine):
    return asyncio.run(coroutine)


class FakeArapiClient:
    def __init__(self, forms: tuple[str, ...]) -> None:
        self.forms = forms
        self.calls = 0

    async def list_forms(self) -> tuple[str, ...]:
        self.calls += 1
        return self.forms


class FakeProvider:
    def __init__(self, client: FakeArapiClient) -> None:
        self.client = client
        self.targets: list[ResolvedTarget] = []

    def get(self, target: ResolvedTarget) -> FakeArapiClient:
        self.targets.append(target)
        return self.client


def build_service(
    client: FakeArapiClient,
    *,
    allow_reads: bool = True,
    allow_all_forms: bool = True,
    allowed_forms: tuple[str, ...] = (),
    max_rows: int = 2,
    metadata_cache_ttl: int = 0,
    time_source=lambda: 100.0,
) -> FormCatalogService:
    policy = TargetPolicyConfig(
        name="catalog",
        allow_form_reads=allow_reads,
        allow_all_forms=allow_all_forms,
        allowed_forms=allowed_forms,
        max_rows=max_rows,
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
    return FormCatalogService(
        TargetResolver(registry),
        FakeProvider(client),
        metadata_cache_ttl_seconds=metadata_cache_ttl,
        time_source=time_source,
    )


def test_catalog_filters_sorts_and_paginates_accessible_forms() -> None:
    client = FakeArapiClient(
        (
            "ZZZ:Other",
            "Example.Support:WorkLog",
            "Example:BaseElement",
            "Example.Support:HelpDesk",
        )
    )
    service = build_service(client)

    result = run(
        service.list_forms(
            environment="dev",
            query=FormCatalogQuery(
                name_contains="Example.Support:",
                offset=1,
                limit=1,
            ),
        )
    )

    assert result.total == 2
    assert [form.name for form in result.forms] == ["Example.Support:WorkLog"]
    assert client.calls == 1


def test_catalog_applies_form_allowlist() -> None:
    client = FakeArapiClient(("Hidden:Form", "Example:HelpDesk"))
    service = build_service(
        client,
        allow_all_forms=False,
        allowed_forms=("Example:HelpDesk",),
    )

    result = run(
        service.list_forms(
            environment="dev",
            query=FormCatalogQuery(limit=2),
        )
    )

    assert [form.name for form in result.forms] == ["Example:HelpDesk"]


def test_catalog_cache_reuses_and_expires_arapi_metadata() -> None:
    now = [100.0]
    client = FakeArapiClient(("Example:HelpDesk", "Example:WorkLog"))
    service = build_service(
        client,
        metadata_cache_ttl=30,
        time_source=lambda: now[0],
    )

    first = run(
        service.list_forms(
            environment="dev",
            query=FormCatalogQuery(name_contains="Help", limit=2),
        )
    )
    second = run(
        service.list_forms(
            environment="dev",
            query=FormCatalogQuery(name_contains="Work", limit=2),
        )
    )
    now[0] += 30.1
    run(
        service.list_forms(
            environment="dev",
            query=FormCatalogQuery(limit=2),
        )
    )

    assert [form.name for form in first.forms] == ["Example:HelpDesk"]
    assert [form.name for form in second.forms] == ["Example:WorkLog"]
    assert client.calls == 2


def test_catalog_blocks_disabled_reads_and_excessive_limits_before_io() -> (
    None
):
    disabled_client = FakeArapiClient(("Example:HelpDesk",))
    disabled = build_service(disabled_client, allow_reads=False)
    with pytest.raises(FormReadDisabledError):
        run(
            disabled.list_forms(
                environment="dev",
                query=FormCatalogQuery(limit=1),
            )
        )
    assert disabled_client.calls == 0

    limited_client = FakeArapiClient(("Example:HelpDesk",))
    limited = build_service(limited_client, max_rows=1)
    with pytest.raises(FormQueryLimitError):
        run(
            limited.list_forms(
                environment="dev",
                query=FormCatalogQuery(limit=2),
            )
        )
    assert limited_client.calls == 0
