"""Sanitized policy and validation errors for ARAPI SQL reads."""


class DatabaseServiceError(RuntimeError):
    code = "DATABASE_SERVICE_ERROR"


class DatabaseReadDisabledError(DatabaseServiceError):
    code = "DATABASE_READ_DISABLED"


class DatabaseQueryInvalidError(DatabaseServiceError):
    code = "DATABASE_QUERY_INVALID"


class DatabaseQueryAliasRequiredError(DatabaseQueryInvalidError):
    code = "DATABASE_QUERY_ALIAS_REQUIRED"


class DatabaseQueryAliasInvalidError(DatabaseQueryInvalidError):
    code = "DATABASE_QUERY_ALIAS_INVALID"


class DatabaseQueryWildcardNotAllowedError(DatabaseQueryInvalidError):
    code = "DATABASE_QUERY_WILDCARD_NOT_ALLOWED"


class DatabaseObjectNotAllowedError(DatabaseServiceError):
    code = "DATABASE_OBJECT_NOT_ALLOWED"


class DatabaseObjectNotFoundError(DatabaseServiceError):
    code = "DATABASE_OBJECT_NOT_FOUND"


class DatabaseMetadataResponseError(DatabaseServiceError):
    code = "DATABASE_METADATA_RESPONSE_INVALID"


class DatabaseQueryLimitError(DatabaseServiceError):
    code = "DATABASE_QUERY_LIMIT_EXCEEDED"


class DatabaseRateLimitError(DatabaseServiceError):
    code = "DATABASE_RATE_LIMIT_EXCEEDED"


class SqlQueryPlanCapacityError(DatabaseServiceError):
    code = "SQL_QUERY_PLAN_CAPACITY_REACHED"


class SqlQueryPlanNotFoundError(DatabaseServiceError):
    code = "SQL_QUERY_PLAN_NOT_FOUND"


class SqlQueryPlanExpiredError(DatabaseServiceError):
    code = "SQL_QUERY_PLAN_EXPIRED"


class SqlQueryPlanMismatchError(DatabaseServiceError):
    code = "SQL_QUERY_PLAN_MISMATCH"


class SqlQueryPlanStateError(DatabaseServiceError):
    code = "SQL_QUERY_PLAN_STATE_INVALID"


class SqlQueryPlanPersistenceError(DatabaseServiceError):
    code = "SQL_QUERY_PLAN_PERSISTENCE_ERROR"
