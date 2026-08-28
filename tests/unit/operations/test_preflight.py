"""Tests for sanitized operational preflight reports."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from helix_mcp.config import (
    ArapiBackendConfig,
    Environment,
    SecretProviderKind,
    SecretRef,
    TargetConfig,
)
from helix_mcp.operations import CheckStatus, PreflightStatus, check_readiness
from helix_mcp.secrets import SecretValue
from helix_mcp.services.health import (
    HealthCheckResult,
    HealthComponent,
    HealthComponentResult,
    HealthStatus,
)


def run(coroutine):
    return asyncio.run(coroutine)


class CodedFailure(RuntimeError):
    code = "SAFE_OPERATIONAL_FAILURE"


class FakeBridge:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def check_startup_requirements(self) -> None:
        if self.error is not None:
            raise self.error


class FakeSecrets:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def resolve(self, reference, *, required_fields):
        if self.error is not None:
            raise self.error
        return SecretValue(
            reference,
            {"username": "private-user", "password": "private-password"},
        )


class FakeHealth:
    def __init__(self, result: HealthCheckResult) -> None:
        self.result = result

    async def check(self, *, environment, force_refresh):
        return self.result


class FakeApplication:
    def __init__(
        self,
        *,
        bridge_error: Exception | None = None,
        secret_error: Exception | None = None,
        health: HealthCheckResult | None = None,
    ) -> None:
        credential = SecretRef(
            provider=SecretProviderKind.ENVIRONMENT,
            key="HELIX_CREDENTIAL_DEV",
        )
        target = TargetConfig(
            environment=Environment.DEV,
            display_name="Helix DEV",
            policy_ref="locked",
            arapi=ArapiBackendConfig(
                bridge_base_url="http://127.0.0.1:8090",
                gateway_port=46_000,
                credentials=credential,
            ),
        )
        self.runtime = SimpleNamespace(
            config=SimpleNamespace(targets=(target,)),
            secrets=FakeSecrets(secret_error),
        )
        self.arapi_bridge = FakeBridge(bridge_error)
        self.health_checks = FakeHealth(
            health
            or HealthCheckResult(
                environment=Environment.DEV,
                status=HealthStatus.HEALTHY,
                cached=False,
                checks=(
                    HealthComponentResult(
                        component=HealthComponent.ARAPI_BRIDGE,
                        status=HealthStatus.HEALTHY,
                        latency_ms=1,
                    ),
                ),
            )
        )
        self.started = False
        self.closed = False

    async def astart(self) -> None:
        self.started = True

    async def aclose(self) -> None:
        self.closed = True


def test_configuration_preflight_validates_dependencies_and_credentials(
    monkeypatch,
) -> None:
    application = FakeApplication()
    monkeypatch.setattr(
        "helix_mcp.operations.preflight.load_application",
        lambda *args, **kwargs: application,
    )

    report = run(check_readiness())

    assert report.status is PreflightStatus.READY
    assert [check.name for check in report.checks] == [
        "configuration",
        "arapi_bridge_startup",
        "credential.dev.arapi",
    ]
    assert all(check.status is CheckStatus.PASSED for check in report.checks)
    assert application.started is False
    assert application.closed is True


def test_failures_are_reduced_to_stable_codes(monkeypatch) -> None:
    leaked = "private-password at private-path"
    application = FakeApplication(
        bridge_error=CodedFailure(leaked),
        secret_error=CodedFailure(leaked),
    )
    monkeypatch.setattr(
        "helix_mcp.operations.preflight.load_application",
        lambda *args, **kwargs: application,
    )

    report = run(check_readiness())
    serialized = report.model_dump_json()

    assert report.status is PreflightStatus.NOT_READY
    assert [check.error_code for check in report.checks] == [
        None,
        "SAFE_OPERATIONAL_FAILURE",
        "SAFE_OPERATIONAL_FAILURE",
    ]
    assert leaked not in serialized
    assert application.closed is True


def test_live_preflight_reports_each_component(monkeypatch) -> None:
    application = FakeApplication(
        health=HealthCheckResult(
            environment=Environment.DEV,
            status=HealthStatus.UNHEALTHY,
            cached=False,
            checks=(
                HealthComponentResult(
                    component=HealthComponent.ARAPI_BRIDGE,
                    status=HealthStatus.HEALTHY,
                    latency_ms=1,
                ),
                HealthComponentResult(
                    component=HealthComponent.KAAZING,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=2,
                    error_code="HEALTH_CHECK_UNREACHABLE",
                ),
            ),
        )
    )
    monkeypatch.setattr(
        "helix_mcp.operations.preflight.load_application",
        lambda *args, **kwargs: application,
    )

    report = run(
        check_readiness(
            live=True,
            environments=(Environment.DEV,),
        )
    )

    by_name = {check.name: check for check in report.checks}
    assert application.started is True
    assert application.closed is True
    assert by_name["arapi_bridge_lifecycle"].status is CheckStatus.PASSED
    assert by_name["live.dev.arapi_bridge"].status is CheckStatus.PASSED
    assert by_name["live.dev.kaazing"].error_code == (
        "HEALTH_CHECK_UNREACHABLE"
    )
    assert report.status is PreflightStatus.NOT_READY
