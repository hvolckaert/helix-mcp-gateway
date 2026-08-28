"""Policy-enforced reads from BMC Helix forms."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from difflib import SequenceMatcher
from time import monotonic
from typing import NoReturn, Protocol

from helix_mcp.clients.arapi import (
    ArapiBridgeClient,
    ArapiBridgeClientPool,
    ArapiFormNotFoundError,
)
from helix_mcp.config import BackendKind, Environment, TargetKey
from helix_mcp.services.forms.errors import (
    FormFieldNotAllowedError,
    FormNotAllowedError,
    FormNotFoundError,
    FormQueryLimitError,
    FormRateLimitError,
    FormReadDisabledError,
)
from helix_mcp.services.forms.models import (
    FormEntry,
    FormEntryQuery,
    FormEntryResult,
    FormFieldMetadata,
    FormFieldsQuery,
    FormFieldsResult,
    FormQuery,
    FormQueryResult,
)
from helix_mcp.targeting import ResolvedTarget, TargetResolver

_MAX_METADATA_CACHE_ENTRIES = 256


class ArapiClientProvider(Protocol):
    """Target-scoped ARAPI bridge client source used by the service."""

    def get(self, target: ResolvedTarget) -> ArapiBridgeClient:
        """Return a reusable client for the selected target."""


class FormQueryService:
    """Execute bounded reads after all target-policy checks pass."""

    __slots__ = (
        "_clients",
        "_limiter",
        "_metadata_cache",
        "_metadata_cache_lock",
        "_metadata_cache_ttl",
        "_targets",
        "_time",
    )

    def __init__(
        self,
        targets: TargetResolver,
        clients: ArapiClientProvider | ArapiBridgeClientPool,
        *,
        metadata_cache_ttl_seconds: int = 0,
        time_source: Callable[[], float] = monotonic,
    ) -> None:
        if metadata_cache_ttl_seconds < 0:
            raise ValueError("metadata cache TTL cannot be negative")
        self._targets = targets
        self._clients = clients
        self._limiter = _SlidingWindowRateLimiter(time_source=time_source)
        self._metadata_cache: dict[
            tuple[TargetKey, str],
            tuple[float, tuple[FormFieldMetadata, ...]],
        ] = {}
        self._metadata_cache_lock = asyncio.Lock()
        self._metadata_cache_ttl = metadata_cache_ttl_seconds
        self._time = time_source

    async def list_fields(
        self,
        *,
        environment: str | Environment,
        query: FormFieldsQuery,
    ) -> FormFieldsResult:
        """Return safe, bounded field metadata for one form."""

        target = self._targets.resolve(
            environment=environment,
        )
        _enforce_form_access(target, query.form)
        if query.limit > target.policy.max_rows:
            raise FormQueryLimitError(
                target.key,
                "field metadata limit exceeds the target policy",
            )
        await self._limiter.check(
            target.key,
            target.policy.rate_limit_per_minute,
        )
        target = self._targets.resolve(
            environment=environment,
            backend=BackendKind.ARAPI,
        )

        fields = list(await self._field_metadata(target, query.form))
        if query.name_contains is not None:
            name_filter = query.name_contains.casefold()
            fields = [
                field
                for field in fields
                if name_filter in field.name.casefold()
            ]
        return FormFieldsResult(
            fields=tuple(fields[query.offset : query.offset + query.limit]),
            offset=query.offset,
            limit=query.limit,
            total=len(fields),
        )

    async def _field_metadata(
        self,
        target: ResolvedTarget,
        form: str,
    ) -> tuple[FormFieldMetadata, ...]:
        if self._metadata_cache_ttl == 0:
            return await self._load_field_metadata(target, form)
        key = (target.key, form)
        cached = self._cached_field_metadata(key)
        if cached is not None:
            return cached
        async with self._metadata_cache_lock:
            cached = self._cached_field_metadata(key)
            if cached is not None:
                return cached
            fields = await self._load_field_metadata(target, form)
            if len(self._metadata_cache) >= _MAX_METADATA_CACHE_ENTRIES:
                oldest = min(
                    self._metadata_cache,
                    key=lambda item: self._metadata_cache[item][0],
                )
                self._metadata_cache.pop(oldest, None)
            self._metadata_cache[key] = (
                self._time() + self._metadata_cache_ttl,
                fields,
            )
            return fields

    async def _load_field_metadata(
        self,
        target: ResolvedTarget,
        form: str,
    ) -> tuple[FormFieldMetadata, ...]:
        try:
            raw_fields = await self._clients.get(target).list_fields(form)
        except ArapiFormNotFoundError:
            await self._raise_form_not_found(target, form)
        allowed = (
            None
            if target.policy.allow_all_fields
            else {
                field.casefold()
                for field in target.policy.allowed_fields_by_form.get(
                    form,
                    (),
                )
            }
        )
        fields: list[FormFieldMetadata] = []
        for field in raw_fields:
            if _is_sensitive_field(field.name, target):
                continue
            if allowed is not None and field.name.casefold() not in allowed:
                continue
            fields.append(
                FormFieldMetadata(
                    id=field.id,
                    name=field.name,
                    datatype=field.datatype,
                )
            )
        return tuple(fields)

    def _cached_field_metadata(
        self,
        key: tuple[TargetKey, str],
    ) -> tuple[FormFieldMetadata, ...] | None:
        cached = self._metadata_cache.get(key)
        if cached is None:
            return None
        expires_at, fields = cached
        if expires_at <= self._time():
            self._metadata_cache.pop(key, None)
            return None
        return fields

    async def search(
        self,
        *,
        environment: str | Environment,
        query: FormQuery,
    ) -> FormQueryResult:
        """Search one form in one explicit environment."""

        target = self._targets.resolve(
            environment=environment,
        )
        _enforce_query_policy(target, query)
        await self._limiter.check(
            target.key,
            target.policy.rate_limit_per_minute,
        )
        target = self._targets.resolve(
            environment=environment,
            backend=BackendKind.ARAPI,
        )

        try:
            page = await self._clients.get(target).query_entries(
                form=query.form,
                fields=query.fields,
                qualification=query.qualification,
                sort=tuple(
                    (item.field, item.direction.value) for item in query.sort
                ),
                offset=query.offset,
                limit=query.limit,
                include_total=query.include_total,
            )
        except ArapiFormNotFoundError:
            await self._raise_form_not_found(target, query.form)
        return FormQueryResult(
            entries=tuple(
                _entry_from_arapi(target, query.fields, entry.values)
                for entry in page.entries
            ),
            offset=page.offset,
            limit=page.limit,
            total=page.total,
        )

    async def get_entry(
        self,
        *,
        environment: str | Environment,
        query: FormEntryQuery,
    ) -> FormEntryResult:
        """Read one entry by ID after the same field-policy checks."""

        target = self._targets.resolve(
            environment=environment,
        )
        _enforce_field_access(target, query.form, query.fields)
        await self._limiter.check(
            target.key,
            target.policy.rate_limit_per_minute,
        )
        target = self._targets.resolve(
            environment=environment,
            backend=BackendKind.ARAPI,
        )

        try:
            result = await self._clients.get(target).get_entry(
                form=query.form,
                entry_id=query.entry_id,
                fields=query.fields,
            )
        except ArapiFormNotFoundError:
            await self._raise_form_not_found(target, query.form)
        return FormEntryResult(
            entry_id=result.entry_id,
            entry=_entry_from_arapi(
                target,
                query.fields,
                result.entry.values,
            ),
        )

    async def _raise_form_not_found(
        self,
        target: ResolvedTarget,
        form: str,
    ) -> NoReturn:
        """Raise a safe error with hints limited by the target policy."""

        try:
            names = await self._clients.get(target).list_forms()
        except Exception:
            names = ()
        if target.policy.allow_all_forms:
            visible = names
        else:
            allowed = {item.casefold() for item in target.policy.allowed_forms}
            visible = tuple(
                name for name in names if name.casefold() in allowed
            )
        raise FormNotFoundError(
            target.key,
            suggestions=_suggest_form_names(form, visible),
        ) from None


class _SlidingWindowRateLimiter:
    """Per-process, per-target sliding window limiter."""

    __slots__ = ("_events", "_lock", "_time")

    def __init__(self, *, time_source: Callable[[], float]) -> None:
        self._events: dict[TargetKey, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._time = time_source

    async def check(self, target: TargetKey, limit: int) -> None:
        async with self._lock:
            now = self._time()
            oldest_allowed = now - 60.0
            events = self._events.setdefault(target, deque())
            while events and events[0] <= oldest_allowed:
                events.popleft()
            if len(events) >= limit:
                raise FormRateLimitError(
                    target,
                    "form query rate limit was reached",
                )
            events.append(now)


def _enforce_query_policy(
    target: ResolvedTarget,
    query: FormQuery,
) -> None:
    _enforce_field_access(target, query.form, query.fields)
    policy = target.policy

    if query.limit > policy.max_rows:
        raise FormQueryLimitError(
            target.key,
            "query row limit exceeds the target policy",
        )


def _enforce_field_access(
    target: ResolvedTarget,
    form: str,
    fields: tuple[str, ...],
) -> None:
    _enforce_form_access(target, form)
    policy = target.policy
    requested = {field.casefold() for field in fields}
    if any(_is_sensitive_field(field, target) for field in fields):
        raise FormFieldNotAllowedError(
            target.key,
            "query requests a sensitive field",
        )
    if not policy.allow_all_fields:
        allowed_fields = policy.allowed_fields_by_form.get(form, ())
        allowed = {field.casefold() for field in allowed_fields}
        if not requested.issubset(allowed):
            raise FormFieldNotAllowedError(
                target.key,
                "query requests a field outside the target allowlist",
            )


def _enforce_form_access(target: ResolvedTarget, form: str) -> None:
    policy = target.policy
    if not policy.allow_form_reads:
        raise FormReadDisabledError(
            target.key,
            "form reads are disabled by target policy",
        )
    if not policy.allow_all_forms and form not in policy.allowed_forms:
        raise FormNotAllowedError(
            target.key,
            "form is not included in the target allowlist",
        )


def _entry_from_arapi(
    target: ResolvedTarget,
    fields: tuple[str, ...],
    raw_values: dict[str, object],
) -> FormEntry:
    requested = {field.casefold() for field in fields}
    values = {
        field: value
        for field, value in raw_values.items()
        if field.casefold() in requested
        and not _is_sensitive_field(field, target)
    }
    return FormEntry(values=values)


def _is_sensitive_field(field: str, target: ResolvedTarget) -> bool:
    folded = field.casefold()
    policy = target.policy
    if folded in {
        configured.casefold() for configured in policy.sensitive_fields
    }:
        return True
    return any(
        marker.casefold() in folded
        for marker in policy.sensitive_field_markers
    )


def _suggest_form_names(
    requested: str,
    candidates: tuple[str, ...],
) -> tuple[str, ...]:
    """Return at most three close, policy-visible form names."""

    requested_folded = requested.casefold()
    distinctive = requested_folded.rsplit(":", maxsplit=1)[-1]
    matching = tuple(
        candidate
        for candidate in candidates
        if distinctive in candidate.casefold()
    )
    pool = matching or candidates
    ranked = sorted(
        (
            (
                SequenceMatcher(
                    None,
                    requested_folded,
                    candidate.casefold(),
                ).ratio(),
                candidate,
            )
            for candidate in pool
            if candidate.casefold() != requested_folded
        ),
        key=lambda item: (-item[0], item[1].casefold()),
    )
    return tuple(candidate for score, candidate in ranked[:3] if score >= 0.5)
