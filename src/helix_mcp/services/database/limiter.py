"""Shared rate limiting for ARAPI SQL reads and metadata discovery."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from helix_mcp.config import TargetKey
from helix_mcp.services.database.errors import DatabaseRateLimitError
from helix_mcp.services.rate_limit import (
    RateLimitPersistenceError,
    SlidingWindowCounter,
)


class DatabaseRateLimiter:
    __slots__ = ("_counter",)

    def __init__(
        self,
        time_source: Callable[[], float] | None = None,
        *,
        database_path: Path | None = None,
    ) -> None:
        self._counter = SlidingWindowCounter(
            "database_read",
            database_path=database_path,
            time_source=time_source,
        )

    async def check(self, target: TargetKey, limit: int) -> None:
        try:
            allowed = await self._counter.acquire(target, limit)
        except RateLimitPersistenceError:
            raise DatabaseRateLimitError(
                "database query rate limit is unavailable"
            ) from None
        if not allowed:
            raise DatabaseRateLimitError(
                "database query rate limit was reached"
            )
