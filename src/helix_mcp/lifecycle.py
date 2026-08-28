"""Lifecycle coordination for shared application resources."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from mcp.server.fastmcp import FastMCP

from helix_mcp.bootstrap import ApplicationContext
from helix_mcp.observability import public_error_code

Lifespan = Callable[
    [FastMCP],
    AbstractAsyncContextManager[ApplicationContext],
]


def application_lifespan(application: ApplicationContext) -> Lifespan:
    """Return a lifespan that always closes shared backend clients."""

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[ApplicationContext]:
        logger = logging.getLogger("helix_mcp.lifecycle")
        logger.info(
            "application starting",
            extra={"event": "application_starting"},
        )
        try:
            try:
                await application.astart()
            except Exception as exc:
                logger.error(
                    "application startup failed",
                    extra={
                        "event": "application_startup_failed",
                        "error_code": public_error_code(exc),
                    },
                )
                raise
            logger.info(
                "application ready",
                extra={"event": "application_ready"},
            )
            yield application
        finally:
            logger.info(
                "application stopping",
                extra={"event": "application_stopping"},
            )
            try:
                await application.aclose()
            except Exception as exc:
                logger.error(
                    "application shutdown failed",
                    extra={
                        "event": "application_shutdown_failed",
                        "error_code": public_error_code(exc),
                    },
                )
                raise
            logger.info(
                "application stopped",
                extra={"event": "application_stopped"},
            )

    return lifespan
