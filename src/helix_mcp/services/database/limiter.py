"""Shared rate limiting for ARAPI SQL reads and metadata discovery."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from time import monotonic

from helix_mcp.config import TargetKey
from helix_mcp.services.database.errors import DatabaseRateLimitError


class DatabaseRateLimiter:
    __slots__ = ("_events", "_lock", "_time")

    def __init__(
        self,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        self._events: dict[TargetKey, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._time = time_source or monotonic

    async def check(self, target: TargetKey, limit: int) -> None:
        async with self._lock:
            now = self._time()
            events = self._events.setdefault(target, deque())
            oldest_allowed = now - 60.0
            while events and events[0] <= oldest_allowed:
                events.popleft()
            if len(events) >= limit:
                raise DatabaseRateLimitError(
                    "database query rate limit was reached"
                )
            events.append(now)
