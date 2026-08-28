"""Reusable target-scoped clients for the local ARAPI bridge."""

from __future__ import annotations

from helix_mcp.clients.arapi.client import ArapiBridgeClient
from helix_mcp.clients.arapi.errors import (
    ArapiBridgeClosedError,
    ArapiBridgeConfigurationError,
)
from helix_mcp.config import BackendKind, TargetKey
from helix_mcp.secrets import SecretResolver
from helix_mcp.targeting import ResolvedTarget


class ArapiBridgeClientPool:
    """Create at most one bridge client per ARAPI target."""

    __slots__ = ("_clients", "_closed", "_secrets")

    def __init__(self, secrets: SecretResolver) -> None:
        self._secrets = secrets
        self._clients: dict[TargetKey, ArapiBridgeClient] = {}
        self._closed = False

    def get(self, target: ResolvedTarget) -> ArapiBridgeClient:
        if self._closed:
            raise ArapiBridgeClosedError(
                target.key,
                "ARAPI bridge client pool is closed",
            )
        if (
            target.backend is not BackendKind.ARAPI
            or target.config.arapi is None
        ):
            raise ArapiBridgeConfigurationError(
                target.key,
                "resolved target does not select the ARAPI backend",
            )
        client = self._clients.get(target.key)
        if client is None:
            client = ArapiBridgeClient(
                target=target.key,
                config=target.config.arapi,
                secrets=self._secrets,
            )
            self._clients[target.key] = client
        return client

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    def __len__(self) -> int:
        return len(self._clients)
