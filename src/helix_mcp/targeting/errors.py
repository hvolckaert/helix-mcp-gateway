"""Normalized errors for explicit target selection."""

from __future__ import annotations

from helix_mcp.config import BackendKind, Environment, TargetKey


class TargetingError(ValueError):
    """Base error with a stable code suitable for MCP tool responses."""

    code = "TARGETING_ERROR"


class TargetSelectionRequiredError(TargetingError):
    """Environment was omitted from an operation."""

    code = "TARGET_SELECTION_REQUIRED"

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"explicit target field {field!r} is required")


class InvalidEnvironmentError(TargetingError):
    """The supplied environment is not supported."""

    code = "INVALID_ENVIRONMENT"

    def __init__(self, environment: object) -> None:
        self.environment = environment
        allowed = ", ".join(item.value for item in Environment)
        super().__init__(
            f"invalid environment {environment!r}; expected one of: {allowed}"
        )


class InvalidBackendError(TargetingError):
    """The requested backend name is not supported."""

    code = "INVALID_BACKEND"

    def __init__(self, backend: object) -> None:
        self.backend = backend
        allowed = ", ".join(item.value for item in BackendKind)
        super().__init__(
            f"invalid backend {backend!r}; expected one of: {allowed}"
        )


class TargetNotFoundError(TargetingError):
    """No target is configured for the explicit key."""

    code = "TARGET_NOT_FOUND"

    def __init__(self, key: TargetKey) -> None:
        self.key = key
        super().__init__(f"target {key} was not found")


class TargetDisabledError(TargetingError):
    """The target exists but is administratively disabled."""

    code = "TARGET_DISABLED"

    def __init__(self, key: TargetKey) -> None:
        self.key = key
        super().__init__(f"target {key} is disabled")


class BackendUnavailableError(TargetingError):
    """The requested backend is not configured for a target."""

    code = "BACKEND_UNAVAILABLE"

    def __init__(self, key: TargetKey, backend: BackendKind) -> None:
        self.key = key
        self.backend = backend
        super().__init__(
            f"backend {backend.value} is unavailable for target {key}"
        )
