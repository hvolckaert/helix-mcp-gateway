"""Development-only secret provider backed by environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping

from helix_mcp.config import SecretProviderKind, SecretRef
from helix_mcp.secrets.errors import SecretFormatError, SecretNotFoundError
from helix_mcp.secrets.provider import decode_secret_text
from helix_mcp.secrets.value import SecretValue


class EnvironmentSecretProvider:
    """Resolve an explicitly named environment variable."""

    kind = SecretProviderKind.ENVIRONMENT

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    async def resolve(self, reference: SecretRef) -> SecretValue:
        if reference.provider is not self.kind:
            raise SecretFormatError(reference, "provider kind does not match")
        if reference.version is not None:
            raise SecretFormatError(
                reference,
                "environment secrets do not support versions",
            )

        raw_value = self._environ.get(reference.key)
        if raw_value is None:
            raise SecretNotFoundError(reference, "secret was not found")
        return decode_secret_text(reference, raw_value)
