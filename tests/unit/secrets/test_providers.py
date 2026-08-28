"""Tests for explicit secret provider resolution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from helix_mcp.config import SecretProviderKind, SecretRef
from helix_mcp.secrets import (
    EnvironmentSecretProvider,
    KeyringSecretProvider,
    SecretAccessError,
    SecretFieldMissingError,
    SecretFormatError,
    SecretNotFoundError,
    SecretProviderNotRegisteredError,
    SecretResolver,
    VaultSecretProvider,
)


def run(coroutine):
    return asyncio.run(coroutine)


class FakeKeyring:
    def __init__(
        self,
        value: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.value = value
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def get_password(self, service_name: str, username: str) -> str | None:
        self.calls.append((service_name, username))
        if self.error is not None:
            raise self.error
        return self.value


class FakeVaultReader:
    def __init__(self, value: Mapping[str, object] | None) -> None:
        self.value = value
        self.calls: list[tuple[str, int | None]] = []

    async def read_secret(
        self,
        path: str,
        version: int | None,
    ) -> Mapping[str, object] | None:
        self.calls.append((path, version))
        return self.value


def test_environment_provider_resolves_json_fields() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.ENVIRONMENT,
        key="HELIX_TEST_CREDENTIAL",
    )
    provider = EnvironmentSecretProvider(
        {
            "HELIX_TEST_CREDENTIAL": (
                '{"username":"service-user","password":"private-value"}'
            )
        }
    )

    secret = run(provider.resolve(reference))

    with secret:
        assert secret.reveal("username") == "service-user"
        assert secret.reveal("password") == "private-value"
    with pytest.raises(RuntimeError, match="closed"):
        secret.reveal("password")


def test_environment_provider_rejects_versions_and_missing_values() -> None:
    versioned = SecretRef(
        provider=SecretProviderKind.ENVIRONMENT,
        key="HELIX_TEST_CREDENTIAL",
        version="1",
    )
    missing = versioned.model_copy(update={"version": None})
    provider = EnvironmentSecretProvider({})

    with pytest.raises(SecretFormatError, match="do not support versions"):
        run(provider.resolve(versioned))
    with pytest.raises(SecretNotFoundError):
        run(provider.resolve(missing))


def test_keyring_provider_splits_service_and_account() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.KEYRING,
        key="helix-mcp-gateway/core-dev/arapi-service",
    )
    backend = FakeKeyring("private-password")
    provider = KeyringSecretProvider(backend)

    secret = run(provider.resolve(reference))

    assert backend.calls == [("helix-mcp-gateway/core-dev", "arapi-service")]
    with secret:
        assert secret.reveal("username") == "arapi-service"
        assert secret.reveal("password") == "private-password"


def test_keyring_errors_are_sanitized() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.KEYRING,
        key="helix-mcp-gateway/core-dev/arapi-service",
    )
    provider = KeyringSecretProvider(
        FakeKeyring(error=RuntimeError("backend leaked private-password"))
    )

    with pytest.raises(SecretAccessError) as exc_info:
        run(provider.resolve(reference))

    assert "private-password" not in str(exc_info.value)
    assert exc_info.value.__suppress_context__ is True


def test_vault_provider_passes_numeric_version() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.VAULT,
        key="helix/core/dev/arapi",
        version="4",
    )
    reader = FakeVaultReader(
        {"username": "service-user", "password": "private-value"}
    )
    provider = VaultSecretProvider(reader)

    secret = run(provider.resolve(reference))

    assert reader.calls == [("helix/core/dev/arapi", 4)]
    with secret:
        assert secret.reveal("username") == "service-user"


def test_vault_provider_rejects_invalid_versions_and_value_types() -> None:
    invalid_version = SecretRef(
        provider=SecretProviderKind.VAULT,
        key="helix/core/dev/arapi",
        version="latest",
    )
    invalid_record = invalid_version.model_copy(update={"version": None})

    with pytest.raises(SecretFormatError, match="positive integer"):
        run(VaultSecretProvider(FakeVaultReader({})).resolve(invalid_version))
    with pytest.raises(SecretFormatError, match="string values"):
        run(
            VaultSecretProvider(FakeVaultReader({"password": 123})).resolve(
                invalid_record
            )
        )


def test_resolver_never_falls_back_to_another_provider() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.VAULT,
        key="helix/core/dev/arapi",
    )
    resolver = SecretResolver(
        [EnvironmentSecretProvider({"helix/core/dev/arapi": "private-value"})]
    )

    with pytest.raises(SecretProviderNotRegisteredError):
        run(resolver.resolve(reference))


def test_required_fields_are_checked_before_returning_secret() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.ENVIRONMENT,
        key="HELIX_TEST_TOKEN",
    )
    resolver = SecretResolver(
        [EnvironmentSecretProvider({"HELIX_TEST_TOKEN": "private-token"})]
    )

    with pytest.raises(SecretFieldMissingError, match="'username'"):
        run(resolver.resolve(reference, required_fields=("username",)))
