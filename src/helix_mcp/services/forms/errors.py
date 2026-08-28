"""Policy and protocol errors for form read services."""

from __future__ import annotations

from helix_mcp.config import TargetKey


class FormServiceError(RuntimeError):
    """Base form-service error safe for operational logs."""

    code = "FORM_SERVICE_ERROR"

    def __init__(self, target: TargetKey, message: str) -> None:
        self.target = target
        super().__init__(f"{message} (target={target})")


class FormReadDisabledError(FormServiceError):
    code = "FORM_READ_DISABLED"


class FormNotAllowedError(FormServiceError):
    code = "FORM_NOT_ALLOWED"


class FormNotFoundError(FormServiceError):
    """Requested form is absent, with policy-visible replacement hints."""

    code = "FORM_NOT_FOUND"

    def __init__(
        self,
        target: TargetKey,
        *,
        suggestions: tuple[str, ...] = (),
    ) -> None:
        self.suggestions = suggestions
        super().__init__(target, "form does not exist")


class FormFieldNotAllowedError(FormServiceError):
    code = "FORM_FIELD_NOT_ALLOWED"


class FormQueryLimitError(FormServiceError):
    code = "FORM_QUERY_LIMIT_EXCEEDED"


class FormRateLimitError(FormServiceError):
    code = "FORM_RATE_LIMIT_EXCEEDED"


class FormResponseError(FormServiceError):
    code = "FORM_RESPONSE_INVALID"
