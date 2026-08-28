"""Sanitized failures raised by the local ARAPI bridge client."""

from __future__ import annotations

from helix_mcp.config import TargetKey


class ArapiBridgeError(RuntimeError):
    """Base error that never includes credentials or response bodies."""

    code = "ARAPI_BRIDGE_ERROR"

    def __init__(
        self,
        target: TargetKey,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        self.target = target
        self.status_code = status_code
        status = "" if status_code is None else f", status={status_code}"
        super().__init__(f"{message} (target={target}{status})")


class ArapiBridgeConfigurationError(ArapiBridgeError):
    code = "ARAPI_BRIDGE_CONFIGURATION_ERROR"


class ArapiBridgeTransportError(ArapiBridgeError):
    code = "ARAPI_BRIDGE_TRANSPORT_ERROR"


class ArapiBridgeProtocolError(ArapiBridgeError):
    code = "ARAPI_BRIDGE_PROTOCOL_ERROR"


class ArapiFormNotFoundError(ArapiBridgeError):
    """AR System reported that the requested form does not exist."""

    code = "FORM_NOT_FOUND"


class ArapiFieldAmbiguousError(ArapiBridgeError):
    """A requested field name identifies more than one field ID."""

    code = "FORM_FIELD_AMBIGUOUS"


class ArapiFieldNotQueryableError(ArapiBridgeError):
    """A form query references a display-only AR System field."""

    code = "FORM_FIELD_NOT_QUERYABLE"


class ArapiBridgeConflictError(ArapiBridgeError):
    code = "ARAPI_BRIDGE_CONFLICT"


class ArapiBridgeClosedError(ArapiBridgeError):
    code = "ARAPI_BRIDGE_CLOSED"


class ArapiAdminRequiredError(ArapiBridgeError):
    """The ARAPI account is not an AR System administrator."""

    code = "ARAPI_ADMIN_REQUIRED"
