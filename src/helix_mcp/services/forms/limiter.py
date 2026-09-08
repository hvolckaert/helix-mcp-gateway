"""Shared per-process rate limiting for Helix form reads."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from time import monotonic

from helix_mcp.config import TargetKey
from helix_mcp.services.forms.errors import FormRateLimitError


class FormRateLimiter:
    """Per-process, per-target sliding-window limiter."""

    __slots__ = ("_events", "_lock", "_time")

    def __init__(
        self,
        time_source: Callable[[], float] = monotonic,
    ) -> None:
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
                    "form read rate limit was reached",
                )
            events.append(now)
