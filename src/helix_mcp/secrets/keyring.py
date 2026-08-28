"""Local secret provider backed by the operating-system keyring."""

from __future__ import annotations

from typing import Protocol, cast

from helix_mcp.config import SecretProviderKind, SecretRef
from helix_mcp.secrets.errors import (
    SecretAccessError,
    SecretFormatError,
    SecretNotFoundError,
)
from helix_mcp.secrets.provider import decode_secret_text
from helix_mcp.secrets.value import SecretValue


class KeyringBackend(Protocol):
    """Subset of the external keyring API used by this provider."""

    def get_password(self, service_name: str, username: str) -> str | None:
        """Return a stored password or ``None``."""


class KeyringSecretProvider:
    """Resolve ``<service>/<account>`` references from the system keyring."""

    kind = SecretProviderKind.KEYRING

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        self._backend = (
            backend if backend is not None else _load_keyring_backend()
        )

    async def resolve(self, reference: SecretRef) -> SecretValue:
        if reference.provider is not self.kind:
            raise SecretFormatError(reference, "provider kind does not match")
        if reference.version is not None:
            raise SecretFormatError(
                reference, "keyring does not support versions"
            )

        service, separator, account = reference.key.rpartition("/")
        if not separator or not service or not account:
            raise SecretFormatError(
                reference,
                "keyring reference must use <service>/<account>",
            )

        try:
            raw_value = self._backend.get_password(service, account)
        except Exception:
            raise SecretAccessError(
                reference, "keyring access failed"
            ) from None

        if raw_value is None:
            raise SecretNotFoundError(reference, "secret was not found")
        return decode_secret_text(
            reference,
            raw_value,
            default_username=account,
        )


def _load_keyring_backend() -> KeyringBackend:
    try:
        import keyring as system_keyring
    except ImportError as exc:
        raise RuntimeError(
            "keyring support is not installed; install the 'keyring' extra"
        ) from exc
    return cast(KeyringBackend, system_keyring)
