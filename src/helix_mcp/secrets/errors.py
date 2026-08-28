"""Sanitized errors raised while resolving secrets."""

from __future__ import annotations

from hashlib import sha256

from helix_mcp.config import SecretRef


def secret_reference_id(reference: SecretRef) -> str:
    """Return a stable identifier that does not expose the secret path."""

    material = f"{reference.provider.value}\0{reference.key}\0{reference.version or ''}"
    return sha256(material.encode("utf-8")).hexdigest()[:12]


class SecretError(RuntimeError):
    """Base class for secret errors safe to expose in operational logs."""

    code = "SECRET_ERROR"

    def __init__(self, reference: SecretRef, message: str) -> None:
        self.provider = reference.provider
        self.reference_id = secret_reference_id(reference)
        super().__init__(
            f"{message} (provider={self.provider.value}, "
            f"reference={self.reference_id})"
        )


class SecretProviderNotRegisteredError(SecretError):
    """The requested provider was not explicitly registered."""

    code = "SECRET_PROVIDER_NOT_REGISTERED"


class SecretNotFoundError(SecretError):
    """No value exists for the supplied reference."""

    code = "SECRET_NOT_FOUND"


class SecretFormatError(SecretError):
    """The stored secret or its provider-specific reference is malformed."""

    code = "SECRET_FORMAT_INVALID"


class SecretAccessError(SecretError):
    """The provider failed before it could return a secret."""

    code = "SECRET_ACCESS_FAILED"


class SecretFieldMissingError(SecretError):
    """A consumer-required field is absent from a resolved secret."""

    code = "SECRET_FIELD_MISSING"

    def __init__(self, reference: SecretRef, field: str) -> None:
        self.field = field
        super().__init__(
            reference, f"required secret field {field!r} is missing"
        )
