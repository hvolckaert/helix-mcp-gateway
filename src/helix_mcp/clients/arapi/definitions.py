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


class MetadataCoordinate(DefinitionModel):
    """One coordinate in the original AR API units."""

    x: int = Field(ge=-2_147_483_648, le=2_147_483_647)
    y: int = Field(ge=-2_147_483_648, le=2_147_483_647)


class MetadataValue(DefinitionModel):
    """A typed value; unsupported values are never stringified implicitly."""

    status: Literal["present", "unset", "unsupported", "redacted"]
    datatype: MetadataName | None
    value: (
        MetadataScalar
        | Annotated[tuple[MetadataCoordinate, ...], Field(max_length=16)]
    ) = Field(repr=False)

    @model_validator(mode="after")
    def validate_presence(self) -> Self:
        if (self.status == "present") != (self.value is not None):
            raise ValueError("metadata value presence is inconsistent")
        if self.status == "present" and self.datatype is None:
            raise ValueError("a present value requires a datatype")
        if isinstance(self.value, tuple) and self.datatype != "COORDS":
            raise ValueError("coordinate values require the COORDS datatype")
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


class ViewSummary(DefinitionModel):
    """Identity of one AR System VUI, including its declared view type."""

    id: int = Field(ge=1, le=2_147_483_647)
    name: MetadataName
    locale: str | None = Field(max_length=64, pattern=r"^[^\x00-\x1f\x7f]*$")
    view_type: DefinitionCode


class ViewCatalog(DefinitionModel):
    """Bounded bridge catalog; public pagination follows authorization."""

    views: tuple[ViewSummary, ...] = Field(max_length=4096)

    @model_validator(mode="after")
    def unique_views(self) -> Self:
        if len({view.id for view in self.views}) != len(self.views):
            raise ValueError("duplicate view IDs")
        return self


class DisplayProperty(DefinitionModel):
    """Configured display property with an exact ID and explicit coverage."""

    id: int = Field(ge=0, le=2_147_483_647)
    name: MetadataName | None
    value: MetadataValue = Field(repr=False)
    value_symbol: MetadataName | None


DisplayProperties = Annotated[
    tuple[DisplayProperty, ...], Field(max_length=512)
]


class FieldViewDefinition(DefinitionModel):
    """A policy-visible field's shared and view-specific display properties."""

    id: int = Field(ge=1, le=2_147_483_647)
    name: MetadataName
    datatype: MetadataName
    present_in_view: bool
    shared_properties: DisplayProperties = Field(repr=False)
    view_properties: DisplayProperties = Field(repr=False)

    @model_validator(mode="after")
    def validate_properties(self) -> Self:
        if not self.present_in_view and self.view_properties:
            raise ValueError("absent fields cannot have view properties")
        for props in (self.shared_properties, self.view_properties):
            if len({p.id for p in props}) != len(props):
                raise ValueError("duplicate display property IDs")
        return self


class ViewDefinition(ViewSummary):
    """Declared view properties and one bounded set of authorized fields."""

    properties: DisplayProperties = Field(repr=False)
    fields: tuple[FieldViewDefinition, ...] = Field(max_length=128, repr=False)

    @model_validator(mode="after")
    def validate_uniqueness(self) -> Self:
        if len({p.id for p in self.properties}) != len(self.properties):
            raise ValueError("duplicate view property IDs")
        if len({f.id for f in self.fields}) != len(self.fields):
            raise ValueError("duplicate field IDs")
        return self
