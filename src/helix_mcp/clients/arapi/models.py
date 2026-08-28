"""Typed, value-redacted results returned by the local ARAPI bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ArapiScalar = str | int | float | bool
type ArapiSqlValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ArapiField:
    """One safe field definition."""

    id: int
    name: str
    datatype: str


@dataclass(frozen=True, slots=True)
class ArapiEntry:
    """One entry whose representation never contains field values."""

    values: dict[str, Any]

    def __repr__(self) -> str:
        return f"<ArapiEntry fields={len(self.values)} values=redacted>"


@dataclass(frozen=True, slots=True)
class ArapiQueryPage:
    """One bounded ARAPI result page."""

    entries: tuple[ArapiEntry, ...]
    offset: int
    limit: int
    total: int | None

    def __repr__(self) -> str:
        return (
            f"<ArapiQueryPage entries={len(self.entries)} "
            f"offset={self.offset} limit={self.limit} total={self.total!r}>"
        )


@dataclass(frozen=True, slots=True)
class ArapiEntryResult:
    """One directly addressed ARAPI entry."""

    entry_id: str
    entry: ArapiEntry

    def __repr__(self) -> str:
        return (
            f"<ArapiEntryResult entry_id={self.entry_id!r} "
            f"fields={len(self.entry.values)} values=redacted>"
        )


@dataclass(frozen=True, slots=True)
class ArapiPreparedUpdate:
    """Current values and server-side optimistic concurrency token."""

    entry_id: str
    entry: ArapiEntry
    precondition: str

    def __repr__(self) -> str:
        return (
            f"<ArapiPreparedUpdate entry_id={self.entry_id!r} "
            f"fields={len(self.entry.values)} values=redacted>"
        )


@dataclass(frozen=True, slots=True)
class ArapiSqlResult:
    """Bounded positional rows returned by ``ARGetListSQL``."""

    rows: tuple[tuple[ArapiSqlValue, ...], ...]
    truncated: bool

    def __repr__(self) -> str:
        return (
            f"<ArapiSqlResult rows={len(self.rows)} "
            f"truncated={self.truncated} values=redacted>"
        )
