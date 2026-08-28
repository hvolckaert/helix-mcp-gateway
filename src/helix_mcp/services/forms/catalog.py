"""Policy-enforced catalog of AR System forms."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from time import monotonic
from typing import Protocol

from helix_mcp.clients.arapi import ArapiBridgeClient
from helix_mcp.config import BackendKind, Environment, TargetKey
from helix_mcp.services.forms.errors import (
    FormQueryLimitError,
    FormRateLimitError,
    FormReadDisabledError,
)
from helix_mcp.services.forms.models import (
    FormCatalogQuery,
    FormCatalogResult,
    FormMetadata,
)
from helix_mcp.targeting import ResolvedTarget, TargetResolver

_MAX_METADATA_CACHE_ENTRIES = 256


class ArapiClientProvider(Protocol):
    """Target-scoped source of ARAPI bridge clients."""

    def get(self, target: ResolvedTarget) -> ArapiBridgeClient:
        """Return the client for one resolved ARAPI target."""


class FormCatalogService:
    """List accessible forms through ARAPI after policy checks."""

    __slots__ = (
        "_cache",
        "_cache_lock",
        "_cache_ttl",
        "_clients",
        "_events",
        "_lock",
        "_targets",
        "_time",
    )

    def __init__(
        self,
        targets: TargetResolver,
        clients: ArapiClientProvider,
        *,
        metadata_cache_ttl_seconds: int = 0,
        time_source: Callable[[], float] = monotonic,
    ) -> None:
        if metadata_cache_ttl_seconds < 0:
            raise ValueError("metadata cache TTL cannot be negative")
        self._targets = targets
        self._clients = clients
        self._events: dict[TargetKey, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._cache: dict[TargetKey, tuple[float, tuple[str, ...]]] = {}
        self._cache_lock = asyncio.Lock()
        self._cache_ttl = metadata_cache_ttl_seconds
        self._time = time_source

    async def list_forms(
        self,
        *,
        environment: str | Environment,
        query: FormCatalogQuery,
    ) -> FormCatalogResult:
        """Return a bounded catalog for one explicit target."""

        target = self._targets.resolve(
            environment=environment,
            backend=BackendKind.ARAPI,
        )
        if not target.policy.allow_form_reads:
            raise FormReadDisabledError(
                target.key,
                "form reads are disabled by target policy",
            )
        if query.limit > target.policy.max_rows:
            raise FormQueryLimitError(
                target.key,
                "form catalog limit exceeds the target policy",
            )
        await self._check_rate_limit(
            target.key,
            target.policy.rate_limit_per_minute,
        )

        names = await self._form_names(target)
        if target.policy.allow_all_forms:
            visible = list(names)
        else:
            allowed = {name.casefold() for name in target.policy.allowed_forms}
            visible = [name for name in names if name.casefold() in allowed]
        if query.name_contains is not None:
            name_filter = query.name_contains.casefold()
            visible = [
                name for name in visible if name_filter in name.casefold()
            ]
        visible.sort(key=str.casefold)
        total = len(visible)
        page = visible[query.offset : query.offset + query.limit]
        return FormCatalogResult(
            forms=tuple(FormMetadata(name=name) for name in page),
            offset=query.offset,
            limit=query.limit,
            total=total,
        )

    async def _form_names(
        self,
        target: ResolvedTarget,
    ) -> tuple[str, ...]:
        if self._cache_ttl == 0:
            return await self._clients.get(target).list_forms()
        cached = self._cached_names(target.key)
        if cached is not None:
            return cached
        async with self._cache_lock:
            cached = self._cached_names(target.key)
            if cached is not None:
                return cached
            names = tuple(await self._clients.get(target).list_forms())
            if len(self._cache) >= _MAX_METADATA_CACHE_ENTRIES:
                oldest = min(
                    self._cache,
                    key=lambda item: self._cache[item][0],
                )
                self._cache.pop(oldest, None)
            self._cache[target.key] = (
                self._time() + self._cache_ttl,
                names,
            )
            return names

    def _cached_names(self, key: TargetKey) -> tuple[str, ...] | None:
        cached = self._cache.get(key)
        if cached is None:
            return None
        expires_at, names = cached
        if expires_at <= self._time():
            self._cache.pop(key, None)
            return None
        return names

    async def _check_rate_limit(
        self,
        target: TargetKey,
        limit: int,
    ) -> None:
        async with self._lock:
            now = self._time()
            oldest_allowed = now - 60.0
            events = self._events.setdefault(target, deque())
            while events and events[0] <= oldest_allowed:
                events.popleft()
            if len(events) >= limit:
                raise FormRateLimitError(
                    target,
                    "form catalog rate limit was reached",
                )
            events.append(now)
