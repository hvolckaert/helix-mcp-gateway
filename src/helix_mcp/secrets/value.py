"""In-memory secret container with deliberately redacted representations."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Never

from helix_mcp.config import SecretRef
from helix_mcp.secrets.errors import SecretFieldMissingError


class SecretValue:
    """Short-lived secret fields that cannot be printed accidentally."""

    __slots__ = ("_closed", "_reference", "_values")

    def __init__(
        self, reference: SecretRef, values: Mapping[str, str]
    ) -> None:
        if not values:
            raise ValueError("secret value must contain at least one field")
        if any(not isinstance(key, str) or not key for key in values):
            raise ValueError("secret field names must be non-empty strings")
        if any(not isinstance(value, str) for value in values.values()):
            raise TypeError("secret fields must contain string values")

        self._reference = reference
        self._values = dict(values)
        self._closed = False

    @property
    def field_names(self) -> frozenset[str]:
        """Return field names only, never values."""

        self._ensure_open()
        return frozenset(self._values)

    def reveal(self, field: str) -> str:
        """Return one field to an authorized client adapter."""

        self._ensure_open()
        try:
            return self._values[field]
        except KeyError:
            raise SecretFieldMissingError(self._reference, field) from None

    def close(self) -> None:
        """Release references to secret strings as early as Python permits."""

        if self._closed:
            return
        for key in self._values:
            self._values[key] = ""
        self._values.clear()
        self._closed = True

    def __enter__(self) -> SecretValue:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "closed" if self._closed else "redacted"
        return f"<SecretValue {state}>"

    def __str__(self) -> str:
        return "<redacted>"

    def __copy__(self) -> None:
        raise TypeError("secret values cannot be copied")

    def __deepcopy__(self, memo: object) -> None:
        raise TypeError("secret values cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("secret values cannot be serialized")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("secret value is closed")
