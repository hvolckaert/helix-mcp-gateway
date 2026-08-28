"""Vault KV v2 provider using an injected authenticated reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from helix_mcp.config import SecretProviderKind, SecretRef
from helix_mcp.secrets.errors import (
    SecretAccessError,
    SecretFormatError,
    SecretNotFoundError,
)
from helix_mcp.secrets.value import SecretValue


class VaultKvV2Reader(Protocol):
    """Authenticated Vault client boundary supplied by deployment code."""

    async def read_secret(
        self,
        path: str,
        version: int | None,
    ) -> Mapping[str, object] | None:
        """Read the data object from one KV v2 secret version."""


class VaultSecretProvider:
    """Resolve versioned records from a Vault KV v2 reader."""

    kind = SecretProviderKind.VAULT

    def __init__(self, reader: VaultKvV2Reader) -> None:
        self._reader = reader

    async def resolve(self, reference: SecretRef) -> SecretValue:
        if reference.provider is not self.kind:
            raise SecretFormatError(reference, "provider kind does not match")

        version = _parse_version(reference)
        try:
            values = await self._reader.read_secret(reference.key, version)
        except Exception:
            raise SecretAccessError(reference, "Vault access failed") from None

        if values is None:
            raise SecretNotFoundError(reference, "secret was not found")
        if not values:
            raise SecretFormatError(reference, "stored secret object is empty")
        if any(not isinstance(key, str) or not key for key in values):
            raise SecretFormatError(
                reference,
                "stored secret field names must be non-empty strings",
            )
        if any(not isinstance(value, str) for value in values.values()):
            raise SecretFormatError(
                reference,
                "stored secret fields must contain string values",
            )
        return SecretValue(reference, cast(Mapping[str, str], values))


def _parse_version(reference: SecretRef) -> int | None:
    if reference.version is None:
        return None
    try:
        version = int(reference.version)
    except ValueError:
        raise SecretFormatError(
            reference,
            "Vault secret version must be a positive integer",
        ) from None
    if version < 1:
        raise SecretFormatError(
            reference,
            "Vault secret version must be a positive integer",
        )
    return version
