"""Async client for the loopback-only ARAPI bridge."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, TypeGuard

import httpx

from helix_mcp.clients.arapi.errors import (
    ArapiAdminRequiredError,
    ArapiBridgeClosedError,
    ArapiBridgeConflictError,
    ArapiBridgeProtocolError,
    ArapiBridgeTransportError,
    ArapiFieldAmbiguousError,
    ArapiFieldNotQueryableError,
    ArapiFormNotFoundError,
)
from helix_mcp.clients.arapi.models import (
    ArapiEntry,
    ArapiEntryResult,
    ArapiField,
    ArapiPreparedUpdate,
    ArapiQueryPage,
    ArapiScalar,
    ArapiSqlResult,
)
from helix_mcp.config import ArapiBackendConfig, TargetKey
from helix_mcp.secrets import SecretResolver

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_FORMS = 100_000
_MAX_FIELDS = 100_000
_MAX_SQL_COLUMNS = 128
_MAX_SQL_ROWS = 100_000
_ARERR_FIELD_NOT_QUERYABLE = 286
_ARERR_FORM_NOT_FOUND = 303


class ArapiBridgeClient:
    """Send bounded read operations to one local bridge."""

    __slots__ = ("_client", "_closed", "_config", "_secrets", "_target")

    def __init__(
        self,
        *,
        target: TargetKey,
        config: ArapiBackendConfig,
        secrets: SecretResolver,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._target = target
        self._config = config
        self._secrets = secrets
        self._closed = False
        self._client = http_client or httpx.AsyncClient(
            base_url=str(config.bridge_base_url),
            timeout=httpx.Timeout(config.request_timeout_seconds),
            limits=httpx.Limits(
                max_connections=config.pool_size,
                max_keepalive_connections=config.pool_size,
            ),
            trust_env=False,
        )

    async def list_forms(self) -> tuple[str, ...]:
        """Return all forms visible to the configured ARAPI account."""

        payload, status_code = await self._post("/v1/forms", {})
        return _parse_forms(self._target, payload, status_code)

    async def list_fields(self, form: str) -> tuple[ArapiField, ...]:
        """Return all field definitions visible on one form."""

        payload, status_code = await self._post(
            "/v1/fields",
            {"form": form},
        )
        return _parse_fields(self._target, payload, status_code)

    async def query_entries(
        self,
        *,
        form: str,
        fields: tuple[str, ...],
        qualification: str | None,
        sort: tuple[tuple[str, str], ...],
        offset: int,
        limit: int,
        include_total: bool,
    ) -> ArapiQueryPage:
        """Execute one bounded AR qualification through the bridge."""

        data = {
            "form": form,
            "fields": ",".join(fields),
            "offset": str(offset),
            "limit": str(limit),
            "include_total": str(include_total).lower(),
        }
        if qualification is not None:
            data["qualification"] = qualification
        if sort:
            data["sort"] = ",".join(
                f"{field}.{direction}" for field, direction in sort
            )
        payload, status_code = await self._post(
            "/v1/entries/query",
            data,
        )
        return _parse_query_page(
            self._target,
            payload,
            status_code,
            fields=fields,
            offset=offset,
            limit=limit,
            include_total=include_total,
        )

    async def get_entry(
        self,
        *,
        form: str,
        entry_id: str,
        fields: tuple[str, ...],
    ) -> ArapiEntryResult:
        """Return one entry by its AR System entry ID."""

        payload, status_code = await self._post(
            "/v1/entries/get",
            {
                "form": form,
                "entry_id": entry_id,
                "fields": ",".join(fields),
            },
        )
        return _parse_entry_result(
            self._target,
            payload,
            status_code,
            entry_id=entry_id,
            fields=fields,
        )

    async def prepare_update(
        self,
        *,
        form: str,
        entry_id: str,
        fields: tuple[str, ...],
    ) -> ArapiPreparedUpdate:
        """Read current values with a server-side concurrency token."""

        payload, status_code = await self._post(
            "/v1/entries/prepare-update",
            {
                "form": form,
                "entry_id": entry_id,
                "fields": ",".join(fields),
            },
        )
        return _parse_prepared_update(
            self._target,
            payload,
            status_code,
            entry_id=entry_id,
            fields=fields,
        )

    async def create_entry(
        self,
        *,
        form: str,
        values: Mapping[str, ArapiScalar],
    ) -> str | None:
        """Create one entry and return its ID when AR System supplies one."""

        data = {"form": form}
        data.update(_encode_write_values(values))
        payload, status_code = await self._post(
            "/v1/entries/create",
            data,
        )
        return _parse_create_result(self._target, payload, status_code)

    async def update_entry(
        self,
        *,
        form: str,
        entry_id: str,
        values: Mapping[str, ArapiScalar],
        precondition: str,
    ) -> None:
        """Conditionally update one entry using an AR server timestamp."""

        data = {
            "form": form,
            "entry_id": entry_id,
            "precondition": precondition,
        }
        data.update(_encode_write_values(values))
        payload, status_code = await self._post(
            "/v1/entries/update",
            data,
        )
        returned_id = _parse_write_result(
            self._target,
            payload,
            status_code,
        )
        if returned_id != entry_id:
            raise _protocol_error(self._target, status_code)

    async def query_sql(
        self,
        *,
        sql: str,
        column_count: int,
        limit: int,
        timeout_seconds: int,
    ) -> ArapiSqlResult:
        """Execute one bounded administrator-only ``ARGetListSQL`` call."""

        if not 1 <= column_count <= _MAX_SQL_COLUMNS:
            raise ValueError("ARAPI SQL column count is invalid")
        if not 1 <= limit <= _MAX_SQL_ROWS:
            raise ValueError("ARAPI SQL row limit is invalid")
        payload, status_code = await self._post(
            "/v1/sql/query",
            {
                "sql": sql,
                "column_count": str(column_count),
                "limit": str(limit),
            },
            timeout_seconds=timeout_seconds,
        )
        return _parse_sql_result(
            self._target,
            payload,
            status_code,
            column_count=column_count,
            limit=limit,
        )

    async def probe_bridge(self) -> None:
        """Validate the local bridge liveness endpoint without credentials."""

        self._ensure_open()
        try:
            response = await self._client.get("/health")
        except httpx.RequestError:
            raise ArapiBridgeTransportError(
                self._target,
                "local ARAPI bridge health request failed",
            ) from None
        if response.status_code != 200:
            raise ArapiBridgeProtocolError(
                self._target,
                "local ARAPI bridge health check failed",
                status_code=response.status_code,
            )
        try:
            payload: Any = response.json()
        except (UnicodeDecodeError, ValueError):
            raise ArapiBridgeProtocolError(
                self._target,
                "local ARAPI bridge returned invalid health data",
                status_code=response.status_code,
            ) from None
        if payload != {"status": "ok"}:
            raise ArapiBridgeProtocolError(
                self._target,
                "local ARAPI bridge returned invalid health data",
                status_code=response.status_code,
            )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ArapiBridgeClosedError(
                self._target,
                "local ARAPI bridge client is closed",
            )

    async def _post(
        self,
        path: str,
        operation_data: dict[str, str],
        *,
        timeout_seconds: int | None = None,
    ) -> tuple[Any, int]:
        self._ensure_open()
        secret = await self._secrets.resolve(
            self._config.credentials,
            required_fields=("username", "password"),
        )
        form: dict[str, str] = {}
        try:
            with secret:
                form.update(
                    {
                        "host": self._config.gateway_host,
                        "port": str(self._config.gateway_port),
                        "username": secret.reveal("username"),
                        "password": secret.reveal("password"),
                    }
                )
                authentication_field = next(
                    (
                        name
                        for name in (
                            "authentication",
                            "authString",
                            "domain",
                        )
                        if name in secret.field_names
                    ),
                    None,
                )
                if authentication_field is not None:
                    form["authentication"] = secret.reveal(
                        authentication_field
                    )
                form.update(operation_data)
                try:
                    if timeout_seconds is None:
                        response = await self._client.post(path, data=form)
                    else:
                        response = await self._client.post(
                            path,
                            data=form,
                            timeout=timeout_seconds,
                        )
                except httpx.RequestError:
                    raise ArapiBridgeTransportError(
                        self._target,
                        "local ARAPI bridge request failed",
                    ) from None
        finally:
            form.clear()

        if response.status_code == 409:
            raise ArapiBridgeConflictError(
                self._target,
                "entry changed after it was read",
                status_code=response.status_code,
            )
        if response.status_code == 403 and _contains_bridge_code(
            response, "ARAPI_ADMIN_REQUIRED"
        ):
            raise ArapiAdminRequiredError(
                self._target,
                "AR System administrator permission is required for SQL",
                status_code=response.status_code,
            )
        if response.status_code == 400 and _contains_bridge_code(
            response, "FORM_FIELD_AMBIGUOUS"
        ):
            raise ArapiFieldAmbiguousError(
                self._target,
                "requested field name identifies more than one field ID",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            if _contains_arapi_error(response, _ARERR_FIELD_NOT_QUERYABLE):
                raise ArapiFieldNotQueryableError(
                    self._target,
                    "form query references a display-only field",
                    status_code=response.status_code,
                )
            if _contains_arapi_error(response, _ARERR_FORM_NOT_FOUND):
                raise ArapiFormNotFoundError(
                    self._target,
                    "form does not exist on the AR System server",
                    status_code=response.status_code,
                )
            raise ArapiBridgeProtocolError(
                self._target,
                "local ARAPI bridge rejected the request",
                status_code=response.status_code,
            )
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ArapiBridgeProtocolError(
                self._target,
                "local ARAPI bridge response exceeds the safety limit",
                status_code=response.status_code,
            )
        try:
            payload: Any = response.json()
        except (UnicodeDecodeError, ValueError):
            raise ArapiBridgeProtocolError(
                self._target,
                "local ARAPI bridge returned invalid JSON",
                status_code=response.status_code,
            ) from None
        return payload, response.status_code

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"<ArapiBridgeClient target={self._target} state={state}>"


def _parse_forms(
    target: TargetKey,
    payload: Any,
    status_code: int,
) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        raise _protocol_error(target, status_code)
    raw_forms = payload.get("forms")
    total = payload.get("total")
    if (
        not isinstance(raw_forms, list)
        or len(raw_forms) > _MAX_FORMS
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total != len(raw_forms)
    ):
        raise _protocol_error(target, status_code)

    forms: list[str] = []
    folded: set[str] = set()
    for raw_form in raw_forms:
        if (
            not isinstance(raw_form, str)
            or not 1 <= len(raw_form.strip()) <= 255
            or _contains_control_characters(raw_form)
        ):
            raise _protocol_error(target, status_code)
        name = raw_form.strip()
        canonical = name.casefold()
        if canonical in folded:
            raise _protocol_error(target, status_code)
        folded.add(canonical)
        forms.append(name)
    return tuple(forms)


def _parse_sql_result(
    target: TargetKey,
    payload: Any,
    status_code: int,
    *,
    column_count: int,
    limit: int,
) -> ArapiSqlResult:
    if not isinstance(payload, dict) or set(payload) != {"rows", "truncated"}:
        raise _protocol_error(target, status_code)
    raw_rows = payload["rows"]
    truncated = payload["truncated"]
    if (
        not isinstance(raw_rows, list)
        or len(raw_rows) > limit
        or not isinstance(truncated, bool)
    ):
        raise _protocol_error(target, status_code)
    rows: list[tuple[str | int | float | bool | None, ...]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, list) or len(raw_row) != column_count:
            raise _protocol_error(target, status_code)
        normalized: list[str | int | float | bool | None] = []
        for value in raw_row:
            if (
                value is None
                or isinstance(value, (str, bool, int))
                or (isinstance(value, float) and math.isfinite(value))
            ):
                normalized.append(value)
            else:
                raise _protocol_error(target, status_code)
        rows.append(tuple(normalized))
    return ArapiSqlResult(rows=tuple(rows), truncated=truncated)


def _parse_fields(
    target: TargetKey,
    payload: Any,
    status_code: int,
) -> tuple[ArapiField, ...]:
    if not isinstance(payload, dict):
        raise _protocol_error(target, status_code)
    raw_fields = payload.get("fields")
    total = payload.get("total")
    if (
        not isinstance(raw_fields, list)
        or len(raw_fields) > _MAX_FIELDS
        or not _is_integer(total)
        or total != len(raw_fields)
    ):
        raise _protocol_error(target, status_code)
    fields: list[ArapiField] = []
    ids: set[int] = set()
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            raise _protocol_error(target, status_code)
        field_id = raw_field.get("id")
        name = raw_field.get("name")
        datatype = raw_field.get("datatype")
        if (
            not _is_integer(field_id)
            or field_id < 1
            or not _is_normalizable_text(name, 255)
            or not _is_safe_text(datatype, 64)
            or field_id in ids
        ):
            raise _protocol_error(target, status_code)
        ids.add(field_id)
        fields.append(
            ArapiField(
                id=field_id,
                name=name.strip(),
                datatype=datatype,
            )
        )
    return tuple(fields)


def _parse_query_page(
    target: TargetKey,
    payload: Any,
    status_code: int,
    *,
    fields: tuple[str, ...],
    offset: int,
    limit: int,
    include_total: bool,
) -> ArapiQueryPage:
    if not isinstance(payload, dict):
        raise _protocol_error(target, status_code)
    raw_entries = payload.get("entries")
    raw_offset = payload.get("offset")
    raw_limit = payload.get("limit")
    raw_total = payload.get("total")
    if (
        not isinstance(raw_entries, list)
        or len(raw_entries) > limit
        or raw_offset != offset
        or raw_limit != limit
        or not _is_integer(raw_offset)
        or not _is_integer(raw_limit)
    ):
        raise _protocol_error(target, status_code)
    if include_total:
        if not _is_integer(raw_total) or raw_total < 0:
            raise _protocol_error(target, status_code)
        total: int | None = raw_total
    else:
        if "total" in payload:
            raise _protocol_error(target, status_code)
        total = None
    entries = tuple(
        _parse_entry(target, item, status_code, fields) for item in raw_entries
    )
    return ArapiQueryPage(
        entries=entries,
        offset=offset,
        limit=limit,
        total=total,
    )


def _parse_entry_result(
    target: TargetKey,
    payload: Any,
    status_code: int,
    *,
    entry_id: str,
    fields: tuple[str, ...],
) -> ArapiEntryResult:
    if (
        not isinstance(payload, dict)
        or payload.get("entry_id") != entry_id
        or "entry" not in payload
    ):
        raise _protocol_error(target, status_code)
    return ArapiEntryResult(
        entry_id=entry_id,
        entry=_parse_entry(target, payload["entry"], status_code, fields),
    )


def _parse_prepared_update(
    target: TargetKey,
    payload: Any,
    status_code: int,
    *,
    entry_id: str,
    fields: tuple[str, ...],
) -> ArapiPreparedUpdate:
    if (
        not isinstance(payload, dict)
        or payload.get("entry_id") != entry_id
        or "entry" not in payload
    ):
        raise _protocol_error(target, status_code)
    precondition = payload.get("precondition")
    if (
        not isinstance(precondition, str)
        or not precondition.isascii()
        or not precondition.isdecimal()
        or not 1 <= int(precondition) <= 253_402_300_799
    ):
        raise _protocol_error(target, status_code)
    return ArapiPreparedUpdate(
        entry_id=entry_id,
        entry=_parse_entry(target, payload["entry"], status_code, fields),
        precondition=precondition,
    )


def _parse_write_result(
    target: TargetKey,
    payload: Any,
    status_code: int,
) -> str:
    if not isinstance(payload, dict) or set(payload) != {"entry_id"}:
        raise _protocol_error(target, status_code)
    entry_id = payload["entry_id"]
    if not _is_safe_text(entry_id, 255):
        raise _protocol_error(target, status_code)
    return entry_id


def _parse_create_result(
    target: TargetKey,
    payload: Any,
    status_code: int,
) -> str | None:
    if not isinstance(payload, dict) or set(payload) != {"entry_id"}:
        raise _protocol_error(target, status_code)
    entry_id = payload["entry_id"]
    if entry_id is None:
        return None
    if not _is_safe_text(entry_id, 255):
        raise _protocol_error(target, status_code)
    return entry_id


def _encode_write_values(
    values: Mapping[str, ArapiScalar],
) -> dict[str, str]:
    if not 1 <= len(values) <= 32:
        raise ValueError("ARAPI write values must contain 1 to 32 fields")
    data = {"value_count": str(len(values))}
    folded: set[str] = set()
    for index, (field, value) in enumerate(values.items()):
        canonical = field.casefold()
        if (
            not _is_safe_text(field, 255)
            or "," in field
            or canonical in folded
        ):
            raise ValueError("ARAPI write field is invalid")
        folded.add(canonical)
        data[f"field_{index}"] = field
        if isinstance(value, bool):
            data[f"value_type_{index}"] = "boolean"
            data[f"value_{index}"] = str(value).lower()
        elif isinstance(value, int):
            data[f"value_type_{index}"] = "integer"
            data[f"value_{index}"] = str(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("ARAPI write number must be finite")
            data[f"value_type_{index}"] = "number"
            data[f"value_{index}"] = repr(value)
        elif isinstance(value, str):
            if len(value) > 8_192:
                raise ValueError("ARAPI write string is too large")
            data[f"value_type_{index}"] = "string"
            data[f"value_{index}"] = value
        else:
            raise TypeError("ARAPI write value type is unsupported")
    return data


def _parse_entry(
    target: TargetKey,
    payload: Any,
    status_code: int,
    fields: tuple[str, ...],
) -> ArapiEntry:
    if not isinstance(payload, dict):
        raise _protocol_error(target, status_code)
    values = payload.get("values")
    if not isinstance(values, dict) or len(values) != len(fields):
        raise _protocol_error(target, status_code)
    expected = {field.casefold() for field in fields}
    actual: set[str] = set()
    for name in values:
        if (
            not _is_safe_text(name, 255)
            or name.casefold() not in expected
            or name.casefold() in actual
        ):
            raise _protocol_error(target, status_code)
        actual.add(name.casefold())
    if actual != expected:
        raise _protocol_error(target, status_code)
    return ArapiEntry(values=dict(values))


def _is_integer(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_safe_text(value: Any, maximum: int) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 1 <= len(value.strip()) <= maximum
        and value == value.strip()
        and not _contains_control_characters(value)
    )


def _is_normalizable_text(value: Any, maximum: int) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 1 <= len(value.strip()) <= maximum
        and not _contains_control_characters(value)
    )


def _contains_control_characters(value: str) -> bool:
    return any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    )


def _contains_arapi_error(response: httpx.Response, code: int) -> bool:
    """Recognize one bounded bridge error without exposing its body."""

    if len(response.content) > _MAX_RESPONSE_BYTES:
        return False
    try:
        payload: Any = response.json()
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    codes = payload.get("codes")
    return (
        payload.get("error") == "ARAPI operation failed"
        and isinstance(codes, list)
        and 1 <= len(codes) <= 32
        and all(_is_integer(item) for item in codes)
        and code in codes
    )


def _contains_bridge_code(response: httpx.Response, code: str) -> bool:
    if len(response.content) > _MAX_RESPONSE_BYTES:
        return False
    try:
        payload: Any = response.json()
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("code") == code


def _protocol_error(
    target: TargetKey,
    status_code: int,
) -> ArapiBridgeProtocolError:
    return ArapiBridgeProtocolError(
        target,
        "local ARAPI bridge returned an invalid form catalog",
        status_code=status_code,
    )
