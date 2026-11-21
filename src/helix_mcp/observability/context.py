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
_CREATE_ID_FIELDS: ContextVar[dict[str, str | bool] | None] = ContextVar(
    "helix_mcp_create_id_fields", default=None
)


@contextmanager
def operation_context(operation_id: str) -> Iterator[dict[str, str | bool]]:
    """Bind one safe operation identifier for nested log records."""

    token: Token[str | None] = _CURRENT_OPERATION_ID.set(operation_id)
    fields: dict[str, str | bool] = {}
    fields_token = _CREATE_ID_FIELDS.set(fields)
    try:
        yield fields
    finally:
        _CREATE_ID_FIELDS.reset(fields_token)
        _CURRENT_OPERATION_ID.reset(token)


def record_create_identity(
    category: object, *, bridge_has_id: bool, client_has_id: bool
) -> None:
    """Record closed, value-free diagnostics for this call only."""
    fields = _CREATE_ID_FIELDS.get()
    if fields is None:
        return
    categories = (
        "accepted",
        "absent",
        "blank",
        "too_long",
        "control_character",
    )
    fields.update(
        native_id_category=(
            category
            if isinstance(category, str) and category in categories
            else "unreported"
        ),
        bridge_has_entry_id=bridge_has_id,
        client_has_entry_id=client_has_id,
    )


class OperationContextFilter(logging.Filter):
    """Attach the current operation ID without replacing explicit values."""

    def filter(self, record: logging.LogRecord) -> bool:
        operation_id = _CURRENT_OPERATION_ID.get()
        if operation_id is not None and not hasattr(record, "operation_id"):
            record.operation_id = operation_id
        return True
