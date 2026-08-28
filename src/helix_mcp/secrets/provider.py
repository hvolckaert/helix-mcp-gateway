"""Provider protocol and shared decoding helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from helix_mcp.config import SecretProviderKind, SecretRef
from helix_mcp.secrets.errors import SecretFormatError
from helix_mcp.secrets.value import SecretValue


@runtime_checkable
class SecretProvider(Protocol):
    """Resolve one explicit provider-specific secret reference."""

    kind: SecretProviderKind

    async def resolve(self, reference: SecretRef) -> SecretValue:
        """Resolve a reference without caching the returned secret."""


def decode_secret_text(
    reference: SecretRef,
    raw_value: str,
    *,
    default_username: str | None = None,
) -> SecretValue:
    """Decode a JSON object or a provider-supported scalar secret."""

    if not raw_value:
        raise SecretFormatError(reference, "stored secret is empty")

    try:
        decoded = json.loads(raw_value)
    except json.JSONDecodeError:
        decoded = None

    if isinstance(decoded, Mapping):
        if not decoded:
            raise SecretFormatError(reference, "stored secret object is empty")
        if any(not isinstance(key, str) or not key for key in decoded):
            raise SecretFormatError(
                reference,
                "stored secret field names must be non-empty strings",
            )
        if any(not isinstance(value, str) for value in decoded.values()):
            raise SecretFormatError(
                reference,
                "stored secret fields must contain string values",
            )
        return SecretValue(reference, decoded)

    if default_username is not None:
        return SecretValue(
            reference,
            {"username": default_username, "password": raw_value},
        )
    return SecretValue(reference, {"value": raw_value})
