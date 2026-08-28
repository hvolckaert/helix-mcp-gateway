"""Explicit provider registry with no cross-provider fallback."""

from __future__ import annotations

from collections.abc import Iterable

from helix_mcp.config import SecretProviderKind, SecretRef
from helix_mcp.secrets.errors import SecretProviderNotRegisteredError
from helix_mcp.secrets.provider import SecretProvider
from helix_mcp.secrets.value import SecretValue


class SecretResolver:
    """Route a reference only to its explicitly selected provider."""

    def __init__(self, providers: Iterable[SecretProvider]) -> None:
        provider_by_kind: dict[SecretProviderKind, SecretProvider] = {}
        for provider in providers:
            if provider.kind in provider_by_kind:
                raise ValueError(
                    f"duplicate secret provider: {provider.kind.value}"
                )
            provider_by_kind[provider.kind] = provider
        self._providers = provider_by_kind

    @property
    def registered_kinds(self) -> frozenset[SecretProviderKind]:
        return frozenset(self._providers)

    async def resolve(
        self,
        reference: SecretRef,
        *,
        required_fields: Iterable[str] = (),
    ) -> SecretValue:
        provider = self._providers.get(reference.provider)
        if provider is None:
            raise SecretProviderNotRegisteredError(
                reference,
                "secret provider is not registered",
            )

        secret = await provider.resolve(reference)
        try:
            for field in required_fields:
                secret.reveal(field)
        except Exception:
            secret.close()
            raise
        return secret
