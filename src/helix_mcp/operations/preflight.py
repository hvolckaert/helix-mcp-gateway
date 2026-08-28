"""Sanitized configuration and availability checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from helix_mcp.bootstrap import ApplicationContext, load_application
from helix_mcp.config import Environment, SecretRef
from helix_mcp.observability import public_error_code
from helix_mcp.services.health import HealthStatus


class CheckStatus(StrEnum):
    """Outcome of one bounded operational check."""

    PASSED = "passed"
    FAILED = "failed"


class PreflightStatus(StrEnum):
    """Aggregate readiness state."""

    READY = "ready"
    NOT_READY = "not_ready"


class PreflightCheck(BaseModel):
    """One safe check result without connection or secret details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: CheckStatus
    error_code: str | None = None


class PreflightReport(BaseModel):
    """Machine-readable readiness report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PreflightStatus
    checks: tuple[PreflightCheck, ...]

    @property
    def ready(self) -> bool:
        return self.status is PreflightStatus.READY


async def check_readiness(
    dotenv_path: str | Path = ".env",
    *,
    environ: Mapping[str, str] | None = None,
    live: bool = False,
    environments: Sequence[Environment] | None = None,
) -> PreflightReport:
    """Validate startup requirements and optionally probe target backends."""

    checks: list[PreflightCheck] = []
    application: ApplicationContext | None = None
    try:
        try:
            application = load_application(
                dotenv_path,
                environ=environ,
                recover_write_plans=False,
            )
        except Exception as exc:
            checks.append(_failed("configuration", exc))
            return _report(checks)

        checks.append(_passed("configuration"))
        try:
            await application.arapi_bridge.check_startup_requirements()
        except Exception as exc:
            checks.append(_failed("arapi_bridge_startup", exc))
        else:
            checks.append(_passed("arapi_bridge_startup"))

        for target in application.runtime.config.targets:
            if not target.enabled:
                continue
            checks.append(
                await _check_secret(
                    application,
                    target.arapi.credentials,
                    name=(f"credential.{target.environment.value}.arapi"),
                )
            )

        if live:
            await _append_live_checks(
                application,
                checks,
                environments=(
                    tuple(environments)
                    if environments is not None
                    else tuple(Environment)
                ),
            )
    finally:
        if application is not None:
            try:
                await application.aclose()
            except Exception as exc:
                checks.append(_failed("shutdown", exc))

    return _report(checks)


async def _check_secret(
    application: ApplicationContext,
    reference: SecretRef,
    *,
    name: str,
) -> PreflightCheck:
    try:
        secret = await application.runtime.secrets.resolve(
            reference,
            required_fields=("username", "password"),
        )
    except Exception as exc:
        return _failed(name, exc)
    secret.close()
    return _passed(name)


async def _append_live_checks(
    application: ApplicationContext,
    checks: list[PreflightCheck],
    *,
    environments: tuple[Environment, ...],
) -> None:
    try:
        await application.astart()
    except Exception as exc:
        checks.append(_failed("arapi_bridge_lifecycle", exc))
        return

    checks.append(_passed("arapi_bridge_lifecycle"))
    for environment in environments:
        try:
            result = await application.health_checks.check(
                environment=environment,
                force_refresh=True,
            )
        except Exception as exc:
            checks.append(_failed(f"live.{environment.value}", exc))
            continue
        for component in result.checks:
            name = f"live.{environment.value}.{component.component.value}"
            if component.status is HealthStatus.HEALTHY:
                checks.append(_passed(name))
            else:
                checks.append(
                    PreflightCheck(
                        name=name,
                        status=CheckStatus.FAILED,
                        error_code=(
                            component.error_code or "HEALTH_CHECK_FAILED"
                        ),
                    )
                )


def _passed(name: str) -> PreflightCheck:
    return PreflightCheck(name=name, status=CheckStatus.PASSED)


def _failed(name: str, error: Exception) -> PreflightCheck:
    return PreflightCheck(
        name=name,
        status=CheckStatus.FAILED,
        error_code=public_error_code(error),
    )


def _report(checks: list[PreflightCheck]) -> PreflightReport:
    status = (
        PreflightStatus.READY
        if checks
        and all(check.status is CheckStatus.PASSED for check in checks)
        else PreflightStatus.NOT_READY
    )
    return PreflightReport(status=status, checks=tuple(checks))
