"""Secure resolution of credentials from opaque references."""

from helix_mcp.secrets.environment import EnvironmentSecretProvider
from helix_mcp.secrets.errors import (
    SecretAccessError,
    SecretError,
    SecretFieldMissingError,
    SecretFormatError,
    SecretNotFoundError,
    SecretProviderNotRegisteredError,
    secret_reference_id,
)
from helix_mcp.secrets.keyring import KeyringBackend, KeyringSecretProvider
from helix_mcp.secrets.provider import SecretProvider
from helix_mcp.secrets.resolver import SecretResolver
from helix_mcp.secrets.value import SecretValue
from helix_mcp.secrets.vault import VaultKvV2Reader, VaultSecretProvider

__all__ = [
    "EnvironmentSecretProvider",
    "KeyringBackend",
    "KeyringSecretProvider",
    "SecretAccessError",
    "SecretError",
    "SecretFieldMissingError",
    "SecretFormatError",
    "SecretNotFoundError",
    "SecretProvider",
    "SecretProviderNotRegisteredError",
    "SecretResolver",
    "SecretValue",
    "VaultKvV2Reader",
    "VaultSecretProvider",
    "secret_reference_id",
]
