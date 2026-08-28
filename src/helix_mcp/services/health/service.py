"""Concurrent, cached health checks for configured target components."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from helix_mcp.config import BackendKind, Environment, TargetKey
from helix_mcp.services.health.models import (
    HealthCheckResult,
    HealthComponent,
    HealthComponentResult,
    HealthStatus,
)
from helix_mcp.targeting import ResolvedTarget, TargetResolver


class _ArapiHealthClient(Protocol):
    async def probe_bridge(self) -> None: ...


class _ArapiClientProvider(Protocol):
    def get(self, target: ResolvedTarget) -> _ArapiHealthClient: ...


TcpProbe = Callable[[str, int, float], Awaitable[None]]
Clock = Callable[[], float]


class HealthCheckService:
    """Check configured target components without returning internal details."""

    __slots__ = (
        "_arapi_clients",
        "_cache",
        "_cache_ttl",
        "_clock",
        "_locks",
        "_resolver",
        "_tcp_probe",
    )

    def __init__(
        self,
        resolver: TargetResolver,
        arapi_clients: _ArapiClientProvider,
        *,
        cache_ttl_seconds: int,
        clock: Clock = time.monotonic,
        tcp_probe: TcpProbe | None = None,
    ) -> None:
        self._resolver = resolver
        self._arapi_clients = arapi_clients
        self._cache_ttl = cache_ttl_seconds
        self._clock = clock
        self._tcp_probe = tcp_probe or _open_tcp_connection
        self._cache: dict[TargetKey, tuple[float, HealthCheckResult]] = {}
        self._locks: dict[TargetKey, asyncio.Lock] = {}

    async def check(
        self,
        *,
        environment: str | Environment,
        force_refresh: bool = False,
    ) -> HealthCheckResult:
        target = self._resolver.resolve(
            environment=environment,
        )
        cached = self._cached(target.key, force_refresh=force_refresh)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(target.key, asyncio.Lock())
        async with lock:
            cached = self._cached(target.key, force_refresh=force_refresh)
            if cached is not None:
                return cached
            result = await self._check_target(target)
            if self._cache_ttl > 0:
                self._cache[target.key] = (
                    self._clock() + self._cache_ttl,
                    result,
                )
            return result

    def _cached(
        self,
        key: TargetKey,
        *,
        force_refresh: bool,
    ) -> HealthCheckResult | None:
        if force_refresh:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, result = entry
        if expires_at <= self._clock():
            self._cache.pop(key, None)
            return None
        return result.model_copy(update={"cached": True})

    async def _check_target(
        self,
        target: ResolvedTarget,
    ) -> HealthCheckResult:
        probes: list[
            tuple[HealthComponent, Callable[[], Awaitable[None]]]
        ] = []
        config = target.config

        arapi_config = config.arapi
        if arapi_config is not None:
            arapi_target = self._resolver.resolve(
                environment=config.environment,
                backend=BackendKind.ARAPI,
            )
            arapi_client = self._arapi_clients.get(arapi_target)
            probes.append(
                (HealthComponent.ARAPI_BRIDGE, arapi_client.probe_bridge)
            )
            timeout = min(
                float(arapi_config.request_timeout_seconds),
                5.0,
            )
            gateway_host = arapi_config.gateway_host
            gateway_port = arapi_config.gateway_port
            probes.append(
                (
                    HealthComponent.KAAZING,
                    lambda: self._tcp_probe(
                        gateway_host,
                        gateway_port,
                        timeout,
                    ),
                )
            )

        results = list(
            await asyncio.gather(
                *(
                    self._run_probe(component, probe)
                    for component, probe in probes
                )
            )
        )
        status = (
            HealthStatus.HEALTHY
            if results
            and all(
                result.status is HealthStatus.HEALTHY for result in results
            )
            else HealthStatus.UNHEALTHY
        )
        return HealthCheckResult(
            environment=config.environment,
            status=status,
            cached=False,
            checks=tuple(results),
        )

    async def _run_probe(
        self,
        component: HealthComponent,
        probe: Callable[[], Awaitable[None]],
    ) -> HealthComponentResult:
        started_at = self._clock()
        try:
            await probe()
        except Exception as exc:
            return HealthComponentResult(
                component=component,
                status=HealthStatus.UNHEALTHY,
                latency_ms=_latency_ms(started_at, self._clock()),
                error_code=_error_code(exc),
            )
        return HealthComponentResult(
            component=component,
            status=HealthStatus.HEALTHY,
            latency_ms=_latency_ms(started_at, self._clock()),
        )


async def _open_tcp_connection(host: str, port: int, timeout: float) -> None:
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(timeout):
            _, writer = await asyncio.open_connection(host, port)
    finally:
        if writer is not None:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


def _latency_ms(started_at: float, finished_at: float) -> int:
    return max(0, round((finished_at - started_at) * 1_000))


def _error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(error, TimeoutError):
        return "HEALTH_CHECK_TIMEOUT"
    if isinstance(error, OSError):
        return "HEALTH_CHECK_UNREACHABLE"
    return "HEALTH_CHECK_FAILED"
