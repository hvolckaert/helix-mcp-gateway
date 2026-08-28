"""Public failures correlated with sanitized audit events."""

from __future__ import annotations

import json
from collections.abc import Mapping

PublicDetail = str | int | bool


class ToolExecutionError(RuntimeError):
    """Sanitized tool failure containing only a code and operation ID."""

    code = "TOOL_EXECUTION_FAILED"

    def __init__(
        self,
        *,
        error_code: str,
        operation_id: str,
        suggestions: tuple[str, ...] = (),
        details: Mapping[str, PublicDetail] | None = None,
    ) -> None:
        self.error_code = error_code
        self.operation_id = operation_id
        self.suggestions = suggestions
        self.details = dict(details or {})
        suffix_parts = []
        if suggestions:
            suffix_parts.append(
                f"suggestions={json.dumps(suggestions, ensure_ascii=False)}"
            )
        if self.details:
            suffix_parts.append(
                "details="
                + json.dumps(
                    self.details,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        suffix = f", {', '.join(suffix_parts)}" if suffix_parts else ""
        super().__init__(
            "tool execution failed "
            f"(code={error_code}, operation_id={operation_id}{suffix})"
        )
