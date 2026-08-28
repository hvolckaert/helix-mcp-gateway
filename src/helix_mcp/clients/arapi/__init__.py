"""Client for the local ARAPI bridge."""

from helix_mcp.clients.arapi.client import ArapiBridgeClient
from helix_mcp.clients.arapi.errors import (
    ArapiAdminRequiredError,
    ArapiBridgeClosedError,
    ArapiBridgeConfigurationError,
    ArapiBridgeConflictError,
    ArapiBridgeError,
    ArapiBridgeProtocolError,
    ArapiBridgeTransportError,
    ArapiFieldAmbiguousError,
    ArapiFieldNotQueryableError,
    ArapiFormNotFoundError,
)
from helix_mcp.clients.arapi.models import (
    ArapiEntry,
    ArapiEntryResult,
    ArapiField,
    ArapiPreparedUpdate,
    ArapiQueryPage,
    ArapiScalar,
    ArapiSqlResult,
    ArapiSqlValue,
)
from helix_mcp.clients.arapi.pool import ArapiBridgeClientPool
from helix_mcp.clients.arapi.process import (
    ArapiBridgeProcess,
    ArapiBridgeProcessError,
    ArapiLibraries,
    ArapiRuntimeInvalidError,
    ArapiRuntimeMissingError,
    ArapiRuntimeVersionError,
    validate_arapi_libraries,
)

__all__ = [
    "ArapiAdminRequiredError",
    "ArapiBridgeClient",
    "ArapiBridgeClientPool",
    "ArapiBridgeClosedError",
    "ArapiBridgeConfigurationError",
    "ArapiBridgeConflictError",
    "ArapiBridgeError",
    "ArapiBridgeProcess",
    "ArapiBridgeProcessError",
    "ArapiBridgeProtocolError",
    "ArapiBridgeTransportError",
    "ArapiEntry",
    "ArapiEntryResult",
    "ArapiField",
    "ArapiFieldAmbiguousError",
    "ArapiFieldNotQueryableError",
    "ArapiFormNotFoundError",
    "ArapiLibraries",
    "ArapiPreparedUpdate",
    "ArapiQueryPage",
    "ArapiRuntimeInvalidError",
    "ArapiRuntimeMissingError",
    "ArapiRuntimeVersionError",
    "ArapiScalar",
    "ArapiSqlResult",
    "ArapiSqlValue",
    "validate_arapi_libraries",
]
