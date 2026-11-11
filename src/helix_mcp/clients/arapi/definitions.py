"""Bounded metadata contracts shared by the bridge and public read tools."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from helix_mcp.config.models import FrozenModel

MetadataName = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]
MetadataText = Annotated[str, StringConstraints(max_length=8192)]
MetadataScalar = MetadataText | int | float | bool | None


class DefinitionModel(FrozenModel):
    """Reject unexpected keys and coercions at the bridge boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        str_strip_whitespace=False,
    )


class MetadataValue(DefinitionModel):
    """A typed value; unsupported values are never stringified implicitly."""

    status: Literal["present", "unset", "unsupported"]
    datatype: MetadataName | None
    value: MetadataScalar = Field(repr=False)

    @model_validator(mode="after")
    def validate_presence(self) -> Self:
        if (self.status == "present") != (self.value is not None):
            raise ValueError("metadata value presence is inconsistent")
        if self.status == "present" and self.datatype is None:
            raise ValueError("a present value requires a datatype")
        return self


class DefinitionCode(DefinitionModel):
    """Exact AR API numeric code and its known symbolic name."""

    code: int = Field(ge=-2_147_483_648, le=2_147_483_647)
    name: MetadataName | None


class DeclaredPermission(DefinitionModel):
    """Declared group permission, not the caller's effective authorization."""

    group_id: int = Field(ge=-2_147_483_648, le=2_147_483_647)
    permission: DefinitionCode


class FieldLimitDefinition(DefinitionModel):
    """Named, typed constraints with explicit coverage for each datatype."""

    status: Literal["supported", "partial", "unsupported", "not_applicable"]
    properties: dict[MetadataName, MetadataValue] = Field(
        max_length=32, repr=False
    )


class FieldDefinition(DefinitionModel):
    """Stored field definition; view and workflow behavior are separate."""

    id: int = Field(ge=1, le=2_147_483_647)
    name: MetadataName
    datatype: MetadataName
    field_type: DefinitionCode
    option: DefinitionCode
    create_mode: DefinitionCode
    set_field_options: int
    audit_option: int
    default_value: MetadataValue = Field(repr=False)
    limits: FieldLimitDefinition = Field(repr=False)
    permissions: tuple[DeclaredPermission, ...] = Field(
        max_length=4096, repr=False
    )
