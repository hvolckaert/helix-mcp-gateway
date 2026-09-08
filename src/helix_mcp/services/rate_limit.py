"""Process-safe sliding-window counters backed by the private plan database."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from helix_mcp.config import TargetKey

_SQLITE_TIMEOUT_SECONDS = 5
_WINDOW_SECONDS = 60.0
ResultT = TypeVar("ResultT")


class RateLimitPersistenceError(RuntimeError):
    """A shared rate-limit decision could not be recorded safely."""


class SlidingWindowCounter:
    """Record one scoped target event atomically across local processes."""

    __slots__ = (
        "_database_path",
        "_events",
        "_lock",
        "_scope",
        "_time",
    )

    def __init__(
        self,
        scope: str,
        *,
        database_path: Path | None = None,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        if not scope or len(scope) > 64:
            raise ValueError("rate-limit scope is invalid")
        self._scope = scope
        self._database_path = (
            _prepare_database_path(database_path)
            if database_path is not None
            else None
        )
        self._time = time_source or time.time
        self._events: dict[TargetKey, deque[float]] = {}
        self._lock = asyncio.Lock()
        if self._database_path is not None:
            self._initialize_persistent()

    async def acquire(self, target: TargetKey, limit: int) -> bool:
        """Return whether one event fits inside the current minute."""

        async with self._lock:
            now = self._time()
            if self._database_path is not None:
                return await asyncio.to_thread(
                    self._acquire_persistent,
                    str(target),
                    limit,
                    now,
                )
            events = self._events.setdefault(target, deque())
            oldest_allowed = now - _WINDOW_SECONDS
            while events and events[0] <= oldest_allowed:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True

    def _initialize_persistent(self) -> None:
        self._transaction(
            lambda connection: (
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS rate_limit_events ("
                    "scope TEXT NOT NULL, target TEXT NOT NULL, "
                    "occurred_at REAL NOT NULL)"
                ),
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS "
                    "rate_limit_events_lookup_idx ON rate_limit_events "
                    "(scope, target, occurred_at)"
                ),
            )
        )

    def _acquire_persistent(
        self,
        target: str,
        limit: int,
        now: float,
    ) -> bool:
        def acquire(connection: sqlite3.Connection) -> bool:
            oldest_allowed = now - _WINDOW_SECONDS
            connection.execute(
                "DELETE FROM rate_limit_events WHERE occurred_at <= ?",
                (oldest_allowed,),
            )
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM rate_limit_events "
                    "WHERE scope = ? AND target = ?",
                    (self._scope, target),
                ).fetchone()[0]
            )
            if count >= limit:
                return False
            connection.execute(
                "INSERT INTO rate_limit_events "
                "(scope, target, occurred_at) VALUES (?, ?, ?)",
                (self._scope, target, now),
            )
            return True

        return self._transaction(acquire)

    def _transaction(
        self,
        operation: Callable[[sqlite3.Connection], ResultT],
    ) -> ResultT:
        connection: sqlite3.Connection | None = None
        assert self._database_path is not None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=_SQLITE_TIMEOUT_SECONDS,
                isolation_level=None,
            )
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.commit()
            return result
        except (OSError, sqlite3.Error):
            if connection is not None:
                connection.rollback()
            raise RateLimitPersistenceError(
                "shared rate-limit persistence is unavailable"
            ) from None
        finally:
            if connection is not None:
                connection.close()


def _prepare_database_path(path: Path) -> Path:
    absolute = path.absolute()
    try:
        if absolute.parent.is_symlink():
            raise RateLimitPersistenceError(
                "shared rate-limit directory cannot be a link"
            )
        absolute.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            absolute.parent.chmod(0o700)
        if absolute.exists():
            if absolute.is_symlink() or not absolute.is_file():
                raise RateLimitPersistenceError(
                    "shared rate-limit database path is invalid"
                )
            if os.name != "nt" and absolute.stat().st_mode & 0o077:
                raise RateLimitPersistenceError(
                    "shared rate-limit database permissions are too broad"
                )
            return absolute
        descriptor = os.open(
            absolute,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)
        return absolute
    except RateLimitPersistenceError:
        raise
    except OSError:
        raise RateLimitPersistenceError(
            "shared rate-limit database cannot be initialized"
        ) from None
