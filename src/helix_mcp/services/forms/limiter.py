"""Shared per-process rate limiting for Helix form reads."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from helix_mcp.config import TargetKey
from helix_mcp.services.forms.errors import FormRateLimitError
from helix_mcp.services.rate_limit import (
    RateLimitPersistenceError,
    SlidingWindowCounter,
)


class FormRateLimiter:
    """Per-target sliding-window limiter with optional shared persistence."""

    __slots__ = ("_counter",)

    def __init__(
        self,
        time_source: Callable[[], float] | None = None,
        *,
        database_path: Path | None = None,
    ) -> None:
        self._counter = SlidingWindowCounter(
            "form_read",
            database_path=database_path,
            time_source=time_source,
        )

    async def check(self, target: TargetKey, limit: int) -> None:
        try:
            allowed = await self._counter.acquire(target, limit)
        except RateLimitPersistenceError:
            raise FormRateLimitError(
                target,
                "form read rate limit is unavailable",
            ) from None
        if not allowed:
            raise FormRateLimitError(
                target,
                "form read rate limit was reached",
            )
