"""Stable, sanitized failures for form write planning and application."""


class FormWriteError(RuntimeError):
    code = "FORM_WRITE_ERROR"


class FormWriteDisabledError(FormWriteError):
    code = "FORM_WRITE_DISABLED"


class FormWriteFormNotAllowedError(FormWriteError):
    code = "FORM_WRITE_FORM_NOT_ALLOWED"


class FormWriteFieldNotAllowedError(FormWriteError):
    code = "FORM_WRITE_FIELD_NOT_ALLOWED"


class FormWriteReasonRequiredError(FormWriteError):
    code = "FORM_WRITE_REASON_REQUIRED"


class FormWritePreconditionUnavailableError(FormWriteError):
    code = "FORM_WRITE_PRECONDITION_UNAVAILABLE"


class FormWriteConflictError(FormWriteError):
    code = "FORM_WRITE_CONFLICT"


class FormWriteResponseError(FormWriteError):
    code = "FORM_WRITE_RESPONSE_INVALID"


class WritePlanCapacityError(FormWriteError):
    code = "WRITE_PLAN_CAPACITY_EXCEEDED"


class WritePlanNotFoundError(FormWriteError):
    code = "WRITE_PLAN_NOT_FOUND"


class WritePlanExpiredError(FormWriteError):
    code = "WRITE_PLAN_EXPIRED"


class WritePlanMismatchError(FormWriteError):
    code = "WRITE_PLAN_MISMATCH"


class WritePlanStateError(FormWriteError):
    code = "WRITE_PLAN_STATE_INVALID"


class WritePlanPersistenceError(FormWriteError):
    code = "WRITE_PLAN_PERSISTENCE_ERROR"


class WriteOutcomeUnknownError(FormWriteError):
    code = "WRITE_OUTCOME_UNKNOWN"


class FormWriteRateLimitError(FormWriteError):
    code = "FORM_WRITE_RATE_LIMIT_EXCEEDED"
