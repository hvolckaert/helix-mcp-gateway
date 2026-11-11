"""Provenance reflects policy, acquisition time and isolated request state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from helix_mcp.clients.arapi import ArapiField
from helix_mcp.config import (
    ArapiBackendConfig,
    Environment,
    HelixConfig,
    SecretRef,
    TargetConfig,
    TargetPolicyConfig,
)
from helix_mcp.observability import ToolAuditor, ToolExecutionError
from helix_mcp.provenance import (
    capture_cache_reads,
    policy_version,
    record_cache_read,
)
from helix_mcp.services.forms import FormCatalogService, FormQueryService
from helix_mcp.services.health import HealthCheckService
from helix_mcp.targeting import TargetRegistry, TargetResolver
from helix_mcp.tools import (
    FormToolAdapter,
    HealthToolAdapter,
    TargetToolAdapter,
)
from helix_mcp.tools.models import ListFormsOutput as FormsPayload
from helix_mcp.tools.read_models import (
    GetFormFieldDefinitionOutput,
    HealthCheckOutput,
    ListFormFieldsOutput,
    ListFormsOutput,
    ListTargetsOutput,
)
from helix_mcp.tools.reads import ReadToolRunner
from tests.support.metadata import field_definition

T0 = datetime(2026, 11, 11, 12, tzinfo=UTC)


class Bridge:
    def __init__(self):
        self.calls = 0

    async def list_forms(self):
        self.calls += 1
        await asyncio.sleep(0)
        return ("Sample:Form",)

    async def list_fields(self, form):
        self.calls += 1
        await asyncio.sleep(0)
        return (
            ArapiField(id=1, name="Status", datatype="CHAR"),
            ArapiField(id=2, name="Password", datatype="CHAR"),
        )

    async def get_field_definition(self, *, form, field_id):
        return field_definition(
            field_id=field_id, name="Status", datatype="CHAR"
        )

    async def probe_bridge(self):
        self.calls += 1
        await asyncio.sleep(0)

    def get(self, target):
        return self


def registry():
    policies = tuple(
        TargetPolicyConfig(
            name=env,
            allow_all_forms=True,
            allow_all_fields=True,
            max_rows=10 + i,
        )
        for i, env in enumerate(("dev", "qa", "prod"))
    )
    targets = tuple(
        TargetConfig(
            environment=p.name,
            display_name=p.name,
            policy_ref=p.name,
            enabled=p.name != "prod",
            arapi=ArapiBackendConfig(
                bridge_base_url="http://127.0.0.1:8090",
                gateway_port=46000,
                credentials=SecretRef(
                    provider="environment", key="PRIVATE_REF"
                ),
            ),
        )
        for p in policies
    )
    return TargetRegistry(HelixConfig(policies=policies, targets=targets))


def test_policy_fingerprint_is_canonical_and_ignores_policy_label():
    first = TargetPolicyConfig(
        name="first",
        allowed_forms=("Sample:A", "Sample:B"),
        allowed_fields_by_form={
            "Sample:A": ("One", "Two"),
            "Sample:B": ("X",),
        },
    )
    equivalent = TargetPolicyConfig(
        name="renamed",
        allowed_forms=("Sample:B", "Sample:A"),
        allowed_fields_by_form={
            "Sample:B": ("X",),
            "Sample:A": ("Two", "One"),
        },
    )
    assert policy_version(first) == policy_version(equivalent)
    assert policy_version(first).startswith("sha256:")
    assert len(policy_version(first)) == 71


@pytest.mark.parametrize(
    "change",
    [
        {"max_rows": 3},
        {"query_timeout_seconds": 2},
        {"sensitive_fields": ("Custom secret",)},
        {"sensitive_field_markers": ("sensitive",)},
        {
            "allowed_forms": ("Sample:Form",),
            "allowed_fields_by_form": {"Sample:Form": ("One",)},
        },
        {"allowed_forms": ("Sample:Other",)},
        {"allow_form_reads": False},
    ],
)
def test_effective_policy_changes_change_fingerprint(change):
    first = TargetPolicyConfig(name="read")
    changed = TargetPolicyConfig.model_validate(
        {**first.model_dump(), **change}
    )
    assert policy_version(first) != policy_version(changed)


@pytest.mark.parametrize("kind", ["forms", "fields", "health"])
@pytest.mark.parametrize("ttl", [0, 30])
def test_cache_hit_preserves_original_acquisition_and_expiry_refreshes(
    monkeypatch, kind, ttl
):
    wall = [T0]
    elapsed = [100.0]
    for module in ("forms.catalog", "forms.service", "health.service"):
        monkeypatch.setattr(
            f"helix_mcp.services.{module}.utc_now", lambda: wall[0]
        )
    reg = registry()
    resolver = TargetResolver(reg)
    bridge = Bridge()
    forms = FormToolAdapter(
        FormQueryService(
            resolver,
            bridge,
            metadata_cache_ttl_seconds=ttl,
            time_source=lambda: elapsed[0],
        ),
        FormCatalogService(
            resolver,
            bridge,
            metadata_cache_ttl_seconds=ttl,
            time_source=lambda: elapsed[0],
        ),
    )

    async def tcp(*args):
        pass

    health = HealthToolAdapter(
        HealthCheckService(
            resolver,
            bridge,
            cache_ttl_seconds=ttl,
            clock=lambda: elapsed[0],
            tcp_probe=tcp,
        )
    )
    runner = ReadToolRunner(
        reg, ToolAuditor(), clock=lambda: wall[0] + timedelta(seconds=1)
    )
    tool, output, op = {
        "forms": (
            "list_forms",
            ListFormsOutput,
            lambda: forms.list_forms(environment=Environment.DEV, limit=5),
        ),
        "fields": (
            "list_form_fields",
            ListFormFieldsOutput,
            lambda: forms.list_form_fields(
                environment=Environment.DEV, form="Sample:Form", limit=5
            ),
        ),
        "health": (
            "health_check",
            HealthCheckOutput,
            lambda: health.health_check(environment=Environment.DEV),
        ),
    }[kind]

    async def read():
        return await runner.execute(
            tool=tool,
            output_type=output,
            environment=Environment.DEV,
            operation=op,
        )

    async def scenario():
        # Warm the service even without a provenance collector. The timestamp
        # must be stored with the cache entry, not with the first MCP caller.
        await op()
        wall[0] += timedelta(seconds=5)
        hit = await read()
        assert hit.provenance.cached is (ttl > 0)
        assert hit.provenance.data_read_at == (T0 if ttl else wall[0])
        assert hit.provenance.read_at == wall[0] + timedelta(seconds=1)
        assert hit.provenance.cache_reads[0].cached is (ttl > 0)
        assert set(hit.provenance.policy_versions) == {Environment.DEV}
        if kind == "fields":
            assert [f.name for f in hit.fields] == ["Status"]
        if kind == "health":
            assert hit.cached == hit.provenance.cached
        elapsed[0] += 31
        wall[0] += timedelta(seconds=31)
        fresh = await read()
        assert fresh.provenance.cached is False
        assert fresh.provenance.data_read_at == wall[0]
        assert (
            fresh.provenance.policy_versions == hit.provenance.policy_versions
        )
        assert bridge.calls == (2 if ttl else 3)

    asyncio.run(scenario())


def test_fresh_definition_distinguishes_cached_authorization_metadata(
    monkeypatch,
):
    wall = [T0]
    monkeypatch.setattr(
        "helix_mcp.services.forms.service.utc_now", lambda: wall[0]
    )
    reg = registry()
    resolver = TargetResolver(reg)
    bridge = Bridge()
    forms = FormToolAdapter(
        FormQueryService(resolver, bridge, metadata_cache_ttl_seconds=60),
        FormCatalogService(resolver, bridge),
    )
    runner = ReadToolRunner(reg, ToolAuditor(), clock=lambda: wall[0])

    async def scenario():
        await forms.list_form_fields(
            environment=Environment.DEV, form="Sample:Form", limit=5
        )
        wall[0] += timedelta(seconds=10)
        result = await runner.execute(
            tool="get_form_field_definition",
            output_type=GetFormFieldDefinitionOutput,
            environment=Environment.DEV,
            operation=lambda: forms.get_form_field_definition(
                environment=Environment.DEV, form="Sample:Form", field_id=1
            ),
        )
        assert result.provenance.cached is False
        assert result.provenance.data_read_at == wall[0]
        assert result.provenance.cache_reads[0].cached is True
        assert result.provenance.cache_reads[0].read_at == T0
        assert result.provenance.source.field_id == 1
        assert "Password" not in result.provenance.model_dump_json()

    asyncio.run(scenario())


def test_concurrent_targets_failure_and_cancellation_do_not_leak_context():
    reg = registry()
    runner = ReadToolRunner(reg, ToolAuditor(), clock=lambda: T0)

    async def operation(env, *, fail=False, cancel=False):
        record_cache_read("form_catalog", T0, cached=env == Environment.QA)
        await asyncio.sleep(0)
        if fail:
            raise RuntimeError("private failure")
        if cancel:
            raise asyncio.CancelledError()
        return FormsPayload(
            environment=env, forms=(), offset=0, limit=1, total=0
        )

    async def read(env, **kwargs):
        return await runner.execute(
            tool="list_forms",
            output_type=ListFormsOutput,
            environment=env,
            operation=lambda: operation(env, **kwargs),
        )

    async def scenario():
        dev, qa = await asyncio.gather(
            read(Environment.DEV), read(Environment.QA)
        )
        assert not dev.provenance.cached and qa.provenance.cached
        assert (
            len(dev.provenance.cache_reads)
            == len(qa.provenance.cache_reads)
            == 1
        )
        assert set(dev.provenance.policy_versions) == {Environment.DEV}
        assert set(qa.provenance.policy_versions) == {Environment.QA}
        assert dev.provenance.policy_versions != qa.provenance.policy_versions
        with capture_cache_reads() as outer:
            with pytest.raises(ToolExecutionError, match="INTERNAL_ERROR"):
                await read(Environment.DEV, fail=True)
            with pytest.raises(asyncio.CancelledError):
                await read(Environment.QA, cancel=True)
            assert outer == []
            record_cache_read("health", T0, cached=False)
            assert len(outer) == 1
        again = await read(Environment.DEV)
        assert len(again.provenance.cache_reads) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("include_disabled", [False, True])
def test_target_catalog_reports_only_the_returned_environments(
    include_disabled,
):
    reg = registry()
    adapter = TargetToolAdapter(reg)
    result = asyncio.run(
        ReadToolRunner(reg, ToolAuditor()).execute(
            tool="list_targets",
            output_type=ListTargetsOutput,
            operation=lambda: adapter.list_targets(
                include_disabled=include_disabled
            ),
        )
    )
    assert result.provenance.environment is None
    assert result.provenance.source.backend == "gateway"
    assert set(result.provenance.policy_versions) == {
        t.environment for t in result.targets
    }
    assert (
        Environment.PROD in result.provenance.policy_versions
    ) is include_disabled
    for value in ("PRIVATE_REF", "46000", "127.0.0.1", "allow_all_forms"):
        assert value not in result.provenance.model_dump_json()


def test_health_force_refresh_replaces_acquisition_timestamp(monkeypatch):
    wall = [T0]
    monkeypatch.setattr(
        "helix_mcp.services.health.service.utc_now", lambda: wall[0]
    )
    reg = registry()
    bridge = Bridge()

    async def tcp(*args):
        pass

    health = HealthToolAdapter(
        HealthCheckService(
            TargetResolver(reg),
            bridge,
            cache_ttl_seconds=60,
            clock=lambda: 100,
            tcp_probe=tcp,
        )
    )
    runner = ReadToolRunner(reg, ToolAuditor(), clock=lambda: wall[0])

    async def read(refresh=False):
        return await runner.execute(
            tool="health_check",
            output_type=HealthCheckOutput,
            environment=Environment.DEV,
            operation=lambda: health.health_check(
                environment=Environment.DEV, force_refresh=refresh
            ),
        )

    async def scenario():
        first = await read()
        wall[0] += timedelta(seconds=3)
        refreshed = await read(True)
        assert (
            refreshed.provenance.data_read_at > first.provenance.data_read_at
        )
        assert refreshed.provenance.cached is False
        assert (
            await read()
        ).provenance.data_read_at == refreshed.provenance.data_read_at
        assert bridge.calls == 2

    asyncio.run(scenario())


def test_simultaneous_cache_misses_share_data_not_request_context():
    reg = registry()
    bridge = Bridge()
    resolver = TargetResolver(reg)
    forms = FormToolAdapter(
        FormQueryService(resolver, bridge),
        FormCatalogService(resolver, bridge, metadata_cache_ttl_seconds=60),
    )
    runner = ReadToolRunner(reg, ToolAuditor())

    async def read():
        return await runner.execute(
            tool="list_forms",
            output_type=ListFormsOutput,
            environment=Environment.DEV,
            operation=lambda: forms.list_forms(
                environment=Environment.DEV, limit=5
            ),
        )

    async def scenario():
        first, second = await asyncio.gather(read(), read())
        assert first.provenance.cached is False
        assert second.provenance.cached is True
        assert first.provenance.data_read_at == second.provenance.data_read_at
        assert (
            len(first.provenance.cache_reads)
            == len(second.provenance.cache_reads)
            == 1
        )
        assert bridge.calls == 1

    asyncio.run(scenario())


def test_mismatched_result_environment_is_rejected():
    async def wrong_environment():
        return FormsPayload(
            environment=Environment.QA, forms=(), offset=0, limit=1, total=0
        )

    with pytest.raises(ToolExecutionError, match="INTERNAL_ERROR"):
        asyncio.run(
            ReadToolRunner(registry(), ToolAuditor()).execute(
                tool="list_forms",
                output_type=ListFormsOutput,
                environment=Environment.DEV,
                operation=wrong_environment,
            )
        )
