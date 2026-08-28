"""Console entry points for local installation and bridge builds."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from helix_mcp.installation.bridge import build_bridge
from helix_mcp.installation.setup import (
    default_install_paths,
    discover_arapi_lib_dir,
    setup_installation,
)
from helix_mcp.observability import public_error_code


def setup_main(argv: Sequence[str] | None = None) -> int:
    """Initialize a non-destructive per-user installation."""

    parser = argparse.ArgumentParser(prog="helix-mcp-setup")
    parser.add_argument("--arapi-lib-dir", type=Path)
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate packaged resources and show target paths only",
    )
    arguments = parser.parse_args(argv)
    try:
        result = setup_installation(
            arapi_lib_dir=arguments.arapi_lib_dir,
            config_dir=arguments.config_dir,
            data_dir=arguments.data_dir,
            state_dir=arguments.state_dir,
            dry_run=arguments.dry_run,
        )
    except Exception as exc:
        _print_json({"status": "failed", "error_code": public_error_code(exc)})
        return 1
    payload = _paths_to_strings(asdict(result))
    payload["status"] = "ready_for_configuration"
    payload["codex_desktop"] = {
        "command": result.server_command,
        "args": ["--dotenv", str(result.dotenv_path)],
    }
    _print_json(payload)
    return 0


def bridge_main(argv: Sequence[str] | None = None) -> int:
    """Compile and atomically install only the local Java bridge."""

    defaults = default_install_paths()
    parser = argparse.ArgumentParser(prog="helix-mcp-build-bridge")
    parser.add_argument("--arapi-lib-dir", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=defaults.data_dir / "bridge" / "helix-arapi-bridge.jar",
    )
    arguments = parser.parse_args(argv)
    try:
        libraries = discover_arapi_lib_dir(arguments.arapi_lib_dir)
        result = build_bridge(libraries, arguments.output)
    except Exception as exc:
        _print_json({"status": "failed", "error_code": public_error_code(exc)})
        return 1
    _print_json(
        {
            "status": "built",
            "output_path": str(result.output_path),
            "package_version": result.package_version,
            "source_sha256": result.source_sha256,
        }
    )
    return 0


def setup_entrypoint() -> None:
    raise SystemExit(setup_main())


def bridge_entrypoint() -> None:
    raise SystemExit(bridge_main())


def _paths_to_strings(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _paths_to_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_paths_to_strings(item) for item in value]
    return value


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
