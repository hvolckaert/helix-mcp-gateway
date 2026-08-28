"""Safe conversion of Pydantic errors into application configuration errors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from helix_mcp.config.models import HelixConfig


@dataclass(frozen=True, slots=True)
class ConfigValidationIssue:
    """One sanitized configuration validation issue."""

    location: str
    message: str
    error_type: str


class ConfigValidationError(ValueError):
    """Raised when configuration cannot be converted into ``HelixConfig``."""

    def __init__(self, issues: tuple[ConfigValidationIssue, ...]) -> None:
        self.issues = issues
        summary = "; ".join(
            f"{issue.location}: {issue.message}" for issue in issues
        )
        super().__init__(f"invalid Helix configuration: {summary}")


def validate_config(data: Mapping[str, Any]) -> HelixConfig:
    """Validate untrusted configuration without exposing rejected input values."""

    try:
        return HelixConfig.model_validate(data)
    except ValidationError as exc:
        issues = tuple(
            ConfigValidationIssue(
                location=_format_location(error["loc"]),
                message=error["msg"],
                error_type=error["type"],
            )
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigValidationError(issues) from exc


def _format_location(location: tuple[int | str, ...]) -> str:
    if not location:
        return "<root>"

    result = ""
    for item in location:
        if isinstance(item, int):
            result = f"{result}[{item}]"
        else:
            separator = "." if result else ""
            result = f"{result}{separator}{item}"
    return result
