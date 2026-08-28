"""Validated inputs and redacted outputs for form queries."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from helix_mcp.config.models import FrozenModel

FormName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, strip_whitespace=True),
]
FieldName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, strip_whitespace=True),
]
EntryId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, strip_whitespace=True),
]


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class FormSort(FrozenModel):
    """One structured BMC sort expression."""

    field: FieldName
    direction: SortDirection = SortDirection.ASC

    @field_validator("field")
    @classmethod
    def validate_field_syntax(cls, value: str) -> str:
        _validate_field_name(value)
        return value


class FormQuery(FrozenModel):
    """Bounded read request for one allowlisted form."""

    form: FormName
    fields: tuple[FieldName, ...] = Field(min_length=1, max_length=128)
    qualification: str | None = Field(
        default=None,
        min_length=1,
        max_length=8_192,
        repr=False,
    )
    sort: tuple[FormSort, ...] = Field(default=(), max_length=8)
    offset: int = Field(default=0, ge=0, le=10_000_000)
    limit: int = Field(default=100, ge=1, le=100_000)
    include_total: bool = False

    @field_validator("form")
    @classmethod
    def validate_form_syntax(cls, value: str) -> str:
        _reject_control_characters(value, "form")
        return value

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, fields: tuple[str, ...]) -> tuple[str, ...]:
        for field in fields:
            _validate_field_name(field)
        folded = [field.casefold() for field in fields]
        if len(folded) != len(set(folded)):
            raise ValueError("query fields must be unique")
        return fields

    @field_validator("qualification")
    @classmethod
    def validate_qualification(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_control_characters(value, "qualification")
        return value

    @model_validator(mode="after")
    def validate_sort_uniqueness(self) -> Self:
        folded = [item.field.casefold() for item in self.sort]
        if len(folded) != len(set(folded)):
            raise ValueError("sort fields must be unique")
        return self

    def __repr__(self) -> str:
        qualifier = "present" if self.qualification is not None else "none"
        return (
            f"<FormQuery form={self.form!r} fields={len(self.fields)} "
            f"offset={self.offset} limit={self.limit} "
            f"qualification={qualifier}>"
        )


class FormEntryQuery(FrozenModel):
    """Direct read request for one entry and explicit fields."""

    form: FormName
    entry_id: EntryId
    fields: tuple[FieldName, ...] = Field(min_length=1, max_length=128)

    @field_validator("form")
    @classmethod
    def validate_form_syntax(cls, value: str) -> str:
        _reject_control_characters(value, "form")
        return value

    @field_validator("entry_id")
    @classmethod
    def validate_entry_id(cls, value: str) -> str:
        _reject_control_characters(value, "entry ID")
        return value

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, fields: tuple[str, ...]) -> tuple[str, ...]:
        for field in fields:
            _validate_field_name(field)
        folded = [field.casefold() for field in fields]
        if len(folded) != len(set(folded)):
            raise ValueError("entry fields must be unique")
        return fields

    def __repr__(self) -> str:
        return (
            f"<FormEntryQuery form={self.form!r} "
            f"entry_id={self.entry_id!r} fields={len(self.fields)}>"
        )


class FormFieldsQuery(FrozenModel):
    """Bounded metadata request for the fields of one form."""

    form: FormName
    name_contains: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=100, ge=1, le=1_000)

    @field_validator("form")
    @classmethod
    def validate_form_syntax(cls, value: str) -> str:
        _reject_control_characters(value, "form")
        return value

    @field_validator("name_contains")
    @classmethod
    def validate_name_filter(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_control_characters(value, "field name filter")
            value = value.strip()
            if not value:
                raise ValueError("field name filter cannot be blank")
        return value


class FormFieldMetadata(FrozenModel):
    """Safe subset of one AR System field definition."""

    id: int = Field(ge=1)
    name: FormName
    datatype: str = Field(min_length=1, max_length=64)


class FormFieldsResult(FrozenModel):
    """Filtered and paginated field metadata."""

    fields: tuple[FormFieldMetadata, ...]
    offset: int
    limit: int
    total: int


class FormCatalogQuery(FrozenModel):
    """Bounded request for accessible form names."""

    name_contains: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=100, ge=1, le=1_000)

    @field_validator("name_contains")
    @classmethod
    def validate_name_filter(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_control_characters(value, "form name filter")
            value = value.strip()
            if not value:
                raise ValueError("form name filter cannot be blank")
        return value


class FormMetadata(FrozenModel):
    """Safe form metadata exposed by the catalog."""

    name: FormName


class FormCatalogResult(FrozenModel):
    """Filtered and paginated form catalog."""

    forms: tuple[FormMetadata, ...]
    offset: int
    limit: int
    total: int


class FormEntry(FrozenModel):
    """One result whose values never appear in its representation."""

    values: dict[str, Any] = Field(repr=False)

    def __repr__(self) -> str:
        return f"<FormEntry fields={len(self.values)} values=redacted>"


class FormQueryResult(FrozenModel):
    """Bounded result set with redacted entry representations."""

    entries: tuple[FormEntry, ...]
    offset: int
    limit: int
    total: int | None = None

    def __repr__(self) -> str:
        return (
            f"<FormQueryResult entries={len(self.entries)} "
            f"offset={self.offset} limit={self.limit} total={self.total!r}>"
        )


class FormEntryResult(FrozenModel):
    """One directly addressed entry with a redacted representation."""

    entry_id: EntryId
    entry: FormEntry

    def __repr__(self) -> str:
        return (
            f"<FormEntryResult entry_id={self.entry_id!r} "
            f"fields={len(self.entry.values)} values=redacted>"
        )


def _validate_field_name(value: str) -> None:
    _reject_control_characters(value, "field")
    if any(character in value for character in ",()"):
        raise ValueError("field names cannot contain commas or parentheses")


def _reject_control_characters(value: str, label: str) -> None:
    if any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    ):
        raise ValueError(f"{label} cannot contain control characters")
