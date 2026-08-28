"""Validated write requests and redacted plan/apply results."""

from __future__ import annotations

import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from helix_mcp.config import Environment
from helix_mcp.config.models import FrozenModel
from helix_mcp.services.forms.models import FieldName, FormName

JsonScalar = str | int | float | bool
EntryId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, strip_whitespace=True),
]
Reason = Annotated[
    str,
    StringConstraints(min_length=10, max_length=512, strip_whitespace=True),
]
PlanId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
PlanDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class WriteOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"


class WritePlanStatus(StrEnum):
    PENDING = "pending"
    APPLYING = "applying"
    APPLIED = "applied"
    CANCELLED = "cancelled"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class WriteValuesRequest(FrozenModel):
    """Bounded scalar values accepted by generic entry writes."""

    values: dict[FieldName, JsonScalar] = Field(
        min_length=1,
        max_length=32,
        repr=False,
    )
    reason: Reason = Field(repr=False)

    @field_validator("values")
    @classmethod
    def validate_values(
        cls,
        values: dict[str, JsonScalar],
    ) -> dict[str, JsonScalar]:
        folded = [field.casefold() for field in values]
        if len(folded) != len(set(folded)):
            raise ValueError("write fields must be unique")
        for field, value in values.items():
            _validate_field(field)
            if isinstance(value, str) and len(value) > 8_192:
                raise ValueError("write string value is too large")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("write numbers must be finite")
        encoded = json.dumps(
            values,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 32_768:
            raise ValueError("write values exceed the size limit")
        return values


class UpdateValuesRequest(WriteValuesRequest):
    entry_id: EntryId

    @field_validator("entry_id")
    @classmethod
    def validate_entry_id(cls, value: str) -> str:
        _reject_controls(value, "entry ID")
        return value


class ApplyWriteRequest(FrozenModel):
    plan_id: PlanId
    plan_digest: PlanDigest


class PlanLookupRequest(FrozenModel):
    plan_id: PlanId


class WritePlanResult(FrozenModel):
    """Reviewable plan whose repr never includes business values."""

    plan_id: PlanId
    plan_digest: PlanDigest
    operation: WriteOperation
    environment: Environment
    form: FormName
    entry_id: EntryId | None = None
    current_values: dict[str, JsonScalar] | None = Field(
        default=None,
        repr=False,
    )
    proposed_values: dict[str, JsonScalar] = Field(repr=False)
    reason: str = Field(repr=False)
    expires_at: datetime
    status: WritePlanStatus

    def __repr__(self) -> str:
        return (
            f"<WritePlanResult id={self.plan_id} "
            f"operation={self.operation.value} "
            f"environment={self.environment.value} "
            f"form={self.form!r} fields={len(self.proposed_values)} "
            "values=redacted>"
        )


class ApplyWriteResult(FrozenModel):
    """Terminal result without request payloads or endpoint details."""

    plan_id: PlanId
    operation: WriteOperation
    environment: Environment
    form: FormName
    status: WritePlanStatus = WritePlanStatus.APPLIED
    entry_id: EntryId | None = None
    reused_result: bool = False


def _validate_field(value: str) -> None:
    _reject_controls(value, "field")
    if any(character in value for character in ",()"):
        raise ValueError("field names cannot contain commas or parentheses")


def _reject_controls(value: str, label: str) -> None:
    if any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    ):
        raise ValueError(f"{label} cannot contain control characters")
