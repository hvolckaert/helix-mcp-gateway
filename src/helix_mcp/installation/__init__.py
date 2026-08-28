"""Local installation helpers for Helix MCP Gateway."""

from helix_mcp.installation.bridge import (
    BridgeBuildError,
    BridgeBuildResult,
    build_bridge,
)
from helix_mcp.installation.setup import (
    InstallPaths,
    SetupError,
    SetupResult,
    default_install_paths,
    discover_arapi_lib_dir,
    setup_installation,
)

__all__ = [
    "BridgeBuildError",
    "BridgeBuildResult",
    "InstallPaths",
    "SetupError",
    "SetupResult",
    "build_bridge",
    "default_install_paths",
    "discover_arapi_lib_dir",
    "setup_installation",
]
