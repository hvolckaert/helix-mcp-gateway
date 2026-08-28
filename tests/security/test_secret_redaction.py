"""Security regression tests for secret redaction."""

from __future__ import annotations

import copy
import pickle

import pytest

from helix_mcp.config import SecretProviderKind, SecretRef
from helix_mcp.secrets import (
    SecretNotFoundError,
    SecretValue,
    secret_reference_id,
)


def test_secret_value_never_prints_its_fields() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.KEYRING,
        key="internal/service-account",
    )
    secret = SecretValue(
        reference,
        {
            "username": "sensitive-user",
            "password": "sensitive-password",
        },
    )

    rendered = f"{secret!r} {secret}"

    assert "sensitive-user" not in rendered
    assert "sensitive-password" not in rendered
    assert "internal/service-account" not in rendered


def test_secret_errors_use_a_reference_fingerprint() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.VAULT,
        key="sensitive/path/to/credential",
        version="3",
    )

    error = SecretNotFoundError(reference, "secret was not found")
    rendered = str(error)

    assert "sensitive/path/to/credential" not in rendered
    assert secret_reference_id(reference) in rendered
    assert secret_reference_id(reference) == secret_reference_id(reference)


def test_secret_value_cannot_be_copied_or_pickled() -> None:
    reference = SecretRef(
        provider=SecretProviderKind.ENVIRONMENT,
        key="HELIX_TEST_CREDENTIAL",
    )
    secret = SecretValue(reference, {"password": "sensitive-password"})

    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(secret)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.deepcopy(secret)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(secret)
