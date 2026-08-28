"""Tests for sanitized ARAPI and Kaazing health aggregation."""

from __future__ import annotations

import asyncio

from helix_mcp.config import (
    ArapiBackendConfig,
    BackendKind,
    HelixConfig,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
    TargetPolicyConfig,
)
from helix_mcp.services.health import (
    HealthCheckService,
    HealthComponent,
    HealthStatus,
)
from helix_mcp.targeting import ResolvedTarget, TargetRegistry, TargetResolver


def run(coroutine):
    return asyncio.run(coroutine)


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def probe(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error

    async def probe_bridge(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class FakeProvider:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.backends: list[BackendKind | None] = []

    def get(self, target: ResolvedTarget) -> FakeClient:
        self.backends.append(target.backend)
        return self.client


class CodedFailure(RuntimeError):
    code = "SAFE_COMPONENT_FAILURE"


def build_service(
    *,
    arapi_error: Exception | None = None,
    tcp_error: Exception | None = None,
    cache_ttl: int = 15,
):
    credential = SecretRef(
        provider=SecretProviderKind.ENVIRONMENT,
        key="HELIX_CREDENTIAL_DEV",
    )
    policy = TargetPolicyConfig(name="read")
    target = TargetConfig(
        environment="dev",
        display_name="Helix DEV",
        policy_ref=policy.name,
        arapi=ArapiBackendConfig(
            bridge_base_url="http://127.0.0.1:8090",
            gateway_host="127.0.0.1",
            gateway_port=46_000,
            credentials=credential,
        ),
    )
    resolver = TargetResolver(
        TargetRegistry(HelixConfig(policies=(policy,), targets=(target,)))
    )
    arapi_client = FakeClient(arapi_error)
    arapi_provider = FakeProvider(arapi_client)
    tcp_calls: list[tuple[str, int, float]] = []
    now = [100.0]

    async def tcp_probe(host: str, port: int, timeout: float) -> None:
        tcp_calls.append((host, port, timeout))
        if tcp_error is not None:
            raise tcp_error

    service = HealthCheckService(
        resolver,
        arapi_provider,
        cache_ttl_seconds=cache_ttl,
        clock=lambda: now[0],
        tcp_probe=tcp_probe,
    )
    return service, arapi_client, arapi_provider, tcp_calls, now


def test_health_check_aggregates_components_and_uses_cache() -> None:
    service, arapi_client, arapi_provider, tcp_calls, now = build_service()

    first = run(service.check(environment="dev"))
    second = run(service.check(environment="dev"))

    assert first.status is HealthStatus.HEALTHY
    assert first.cached is False
    assert second.cached is True
    assert [check.component for check in first.checks] == [
        HealthComponent.ARAPI_BRIDGE,
        HealthComponent.KAAZING,
    ]
    assert all(check.status is HealthStatus.HEALTHY for check in first.checks)
    assert arapi_client.calls == 1
    assert arapi_provider.backends == [BackendKind.ARAPI]
    assert tcp_calls == [("127.0.0.1", 46_000, 5.0)]

    now[0] += 16
    refreshed = run(service.check(environment="dev"))
    assert refreshed.cached is False
    assert arapi_client.calls == 2


def test_force_refresh_bypasses_a_valid_cache_entry() -> None:
    service, arapi, _, tcp_calls, _ = build_service()

    run(service.check(environment="dev"))
    result = run(
        service.check(
            environment="dev",
            force_refresh=True,
        )
    )

    assert result.cached is False
    assert arapi.calls == 2
    assert len(tcp_calls) == 2


def test_health_failures_expose_only_stable_codes() -> None:
    leaked = "private host and password detail"
    service, *_ = build_service(
        arapi_error=CodedFailure(leaked),
        tcp_error=OSError(leaked),
        cache_ttl=0,
    )

    result = run(service.check(environment="dev"))
    serialized = result.model_dump_json()

    assert result.status is HealthStatus.UNHEALTHY
    assert [check.error_code for check in result.checks] == [
        "SAFE_COMPONENT_FAILURE",
        "HEALTH_CHECK_UNREACHABLE",
    ]
    assert leaked not in serialized
