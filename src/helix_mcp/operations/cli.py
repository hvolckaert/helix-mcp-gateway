"""Command-line entry point for operational readiness checks."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from helix_mcp.config import Environment
from helix_mcp.operations.preflight import check_readiness


def main(argv: Sequence[str] | None = None) -> int:
    """Run preflight and return a process-friendly status code."""

    parser = argparse.ArgumentParser(
        prog="helix-mcp-check",
        description=(
            "Validate Helix MCP startup requirements without exposing secrets."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env"),
        help="path to the local dotenv file (default: .env)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="also probe configured local backends",
    )
    parser.add_argument(
        "--environment",
        action="append",
        choices=tuple(environment.value for environment in Environment),
        help="environment to probe; repeat as needed (default: all)",
    )
    arguments = parser.parse_args(argv)
    if arguments.environment and not arguments.live:
        parser.error("--environment requires --live")

    environments = (
        tuple(Environment(value) for value in arguments.environment)
        if arguments.environment
        else None
    )
    report = asyncio.run(
        check_readiness(
            arguments.dotenv,
            live=arguments.live,
            environments=environments,
        )
    )
    print(report.model_dump_json())
    return 0 if report.ready else 1


def entrypoint() -> None:
    """Console-script wrapper."""

    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
