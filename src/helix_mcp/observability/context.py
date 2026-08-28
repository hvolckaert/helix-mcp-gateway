"""Operation correlation propagated through asynchronous logging contexts."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_CURRENT_OPERATION_ID: ContextVar[str | None] = ContextVar(
    "helix_mcp_operation_id",
    default=None,
)


@contextmanager
def operation_context(operation_id: str) -> Iterator[None]:
    """Bind one safe operation identifier for nested log records."""

    token: Token[str | None] = _CURRENT_OPERATION_ID.set(operation_id)
    try:
        yield
    finally:
        _CURRENT_OPERATION_ID.reset(token)


class OperationContextFilter(logging.Filter):
    """Attach the current operation ID without replacing explicit values."""

    def filter(self, record: logging.LogRecord) -> bool:
        operation_id = _CURRENT_OPERATION_ID.get()
        if operation_id is not None and not hasattr(record, "operation_id"):
            record.operation_id = operation_id
        return True
