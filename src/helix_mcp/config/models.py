"""Strict, immutable models for non-sensitive application configuration."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)

Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
        strip_whitespace=True,
    ),
]
NonEmptyString = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512, strip_whitespace=True),
]

_DEFAULT_SENSITIVE_FIELDS = (
    "Password",
    "Authentication String",
    "Access Token",
    "Refresh Token",
    "API Key",
    "Private Key",
)
_DEFAULT_SENSITIVE_FIELD_MARKERS = (
    "password",
    "passwd",
    "token",
    "secret",
    "api key",
    "private key",
    "credential",
    "authentication string",
)


class FrozenModel(BaseModel):
    """Base model that rejects unknown fields and runtime mutation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class Environment(StrEnum):
    """Supported deployment environments."""

    DEV = "dev"
    QA = "qa"
    PROD = "prod"


class Transport(StrEnum):
    """Supported MCP transports."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class BackendKind(StrEnum):
    """Supported BMC Helix access mechanisms."""

    ARAPI = "arapi"


class SecretProviderKind(StrEnum):
    """Secret provider implementations known by configuration."""

    ENVIRONMENT = "environment"
    KEYRING = "keyring"
    VAULT = "vault"


class AccessMode(StrEnum):
    """Maximum form-access capability granted by one target policy."""

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class TargetKey(FrozenModel):
    """Stable identity of one fixed Helix environment."""

    instance: Literal["helix"] = "helix"
    environment: Environment

    def __str__(self) -> str:
        return f"{self.instance}.{self.environment.value}"


class SecretRef(FrozenModel):
    """Opaque reference to a secret; never the secret value itself."""

    provider: SecretProviderKind
    key: NonEmptyString
    version: NonEmptyString | None = None


class HttpServerConfig(FrozenModel):
    """Listener settings used only by Streamable HTTP."""

    host: NonEmptyString = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65_535)


class ServerSettings(FrozenModel):
    """Process-level settings independent from any Helix target."""

    transport: Transport = Transport.STDIO
    http: HttpServerConfig | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    metadata_cache_ttl_seconds: int = Field(default=1_800, ge=0, le=86_400)
    health_cache_ttl_seconds: int = Field(default=15, ge=0, le=300)
    write_plan_ttl_seconds: int = Field(default=600, ge=60, le=3_600)
    max_pending_write_plans: int = Field(default=100, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_transport_settings(self) -> Self:
        if self.transport is Transport.STREAMABLE_HTTP and self.http is None:
            raise ValueError("http settings are required for streamable_http")
        if self.transport is Transport.STDIO and self.http is not None:
            raise ValueError("http settings are not valid for stdio")
        if self.http is not None and self.http.host not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise ValueError(
                "streamable_http must use loopback until MCP authentication "
                "is configured"
            )
        return self


class ArapiBackendConfig(FrozenModel):
    """Settings for the local Java ARAPI bridge and Client Gateway port."""

    bridge_base_url: HttpUrl
    gateway_host: NonEmptyString = "127.0.0.1"
    gateway_port: int = Field(ge=1, le=65_535)
    credentials: SecretRef
    request_timeout_seconds: int = Field(default=30, ge=1, le=300)
    pool_size: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="after")
    def require_loopback_bridge(self) -> Self:
        if self.bridge_base_url.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("ARAPI bridge must use a loopback host")
        if self.gateway_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("ARAPI gateway must use a loopback host")
        return self


class TargetConfig(FrozenModel):
    """All connection mechanisms available for one Helix environment."""

    instance: Literal["helix"] = "helix"
    environment: Environment
    display_name: NonEmptyString
    policy_ref: Identifier
    enabled: bool = True
    arapi: ArapiBackendConfig

    @property
    def key(self) -> TargetKey:
        return TargetKey(instance=self.instance, environment=self.environment)

    @property
    def enabled_backends(self) -> frozenset[BackendKind]:
        return frozenset((BackendKind.ARAPI,))


class TargetPolicyConfig(FrozenModel):
    """Security and resource limits referenced by one or more targets."""

    name: Identifier
    allow_all_forms: bool = False
    allow_all_fields: bool = False
    allowed_forms: tuple[NonEmptyString, ...] = ()
    allowed_fields_by_form: dict[
        NonEmptyString,
        tuple[NonEmptyString, ...],
    ] = Field(default_factory=dict)
    allow_all_writable_forms: bool = False
    writable_forms: tuple[NonEmptyString, ...] = ()
    allow_all_creatable_fields: bool = False
    allow_all_creatable_fields_for_forms: tuple[NonEmptyString, ...] = ()
    creatable_fields_by_form: dict[
        NonEmptyString,
        tuple[NonEmptyString, ...],
    ] = Field(default_factory=dict)
    allow_all_updatable_fields: bool = False
    allow_all_updatable_fields_for_forms: tuple[NonEmptyString, ...] = ()
    updatable_fields_by_form: dict[
        NonEmptyString,
        tuple[NonEmptyString, ...],
    ] = Field(default_factory=dict)
    allow_all_sql_objects: bool = False
    allowed_sql_objects: tuple[NonEmptyString, ...] = ()
    allow_form_reads: bool = True
    allow_sql: bool = False
    access_mode: AccessMode = AccessMode.READ_ONLY
    require_human_approval: bool = True
    require_write_reason: bool = True
    max_rows: int = Field(default=1_000, ge=1, le=100_000)
    query_timeout_seconds: int = Field(default=30, ge=1, le=300)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    write_rate_limit_per_minute: int = Field(default=10, ge=1, le=1_000)
    sensitive_fields: tuple[
        NonEmptyString,
        ...,
    ] = _DEFAULT_SENSITIVE_FIELDS
    sensitive_field_markers: tuple[
        NonEmptyString,
        ...,
    ] = _DEFAULT_SENSITIVE_FIELD_MARKERS

    @model_validator(mode="after")
    def validate_allowlists(self) -> Self:
        writes_enabled = self.access_mode is AccessMode.READ_WRITE
        if self.allow_sql and not (
            self.allow_all_sql_objects or self.allowed_sql_objects
        ):
            raise ValueError(
                "allow_all_sql_objects or allowed_sql_objects is required "
                "when SQL is enabled"
            )
        if self.allow_all_sql_objects and self.allowed_sql_objects:
            raise ValueError(
                "allowed_sql_objects must be empty when "
                "allow_all_sql_objects is enabled"
            )
        if self.allow_all_sql_objects and not self.allow_sql:
            raise ValueError(
                "allow_all_sql_objects requires SQL to be enabled"
            )
        if writes_enabled and not (
            self.allow_all_writable_forms or self.writable_forms
        ):
            raise ValueError(
                "allow_all_writable_forms or writable_forms is required "
                "when writes are enabled"
            )
        if self.allow_all_writable_forms and self.writable_forms:
            raise ValueError(
                "writable_forms must be empty when "
                "allow_all_writable_forms is enabled"
            )
        if (
            writes_enabled
            and self.allow_all_writable_forms
            and not (self.allow_all_forms or self.allowed_forms)
        ):
            raise ValueError(
                "allow_all_writable_forms requires a non-empty form scope"
            )
        if writes_enabled and not self.require_human_approval:
            raise ValueError(
                "human approval is required when writes are enabled"
            )
        if writes_enabled and not self.require_write_reason:
            raise ValueError(
                "a write reason is required when writes are enabled"
            )
        write_mappings = (
            (
                "creatable_fields_by_form",
                self.creatable_fields_by_form,
                self.allow_all_creatable_fields,
                "allow_all_creatable_fields_for_forms",
                self.allow_all_creatable_fields_for_forms,
            ),
            (
                "updatable_fields_by_form",
                self.updatable_fields_by_form,
                self.allow_all_updatable_fields,
                "allow_all_updatable_fields_for_forms",
                self.allow_all_updatable_fields_for_forms,
            ),
        )
        effective_writable_forms = (
            self.allowed_forms
            if self.allow_all_writable_forms and not self.allow_all_forms
            else self.writable_forms
        )
        for (
            label,
            mapping,
            allow_all_fields,
            per_form_label,
            per_form_allow_all,
        ) in write_mappings:
            if allow_all_fields and mapping:
                raise ValueError(
                    f"{label} must be empty when its allow-all mode is enabled"
                )
            if allow_all_fields and per_form_allow_all:
                raise ValueError(
                    f"{per_form_label} must be empty when the global "
                    "allow-all mode is enabled"
                )
            if writes_enabled and self.allow_all_writable_forms:
                if not allow_all_fields:
                    raise ValueError(
                        "allow_all_writable_forms requires both global "
                        "non-sensitive write-field scopes"
                    )
            elif writes_enabled:
                if allow_all_fields:
                    raise ValueError(
                        "global non-sensitive write-field scopes require "
                        "allow_all_writable_forms"
                    )
                per_form_set = set(per_form_allow_all)
                if len(per_form_set) != len(per_form_allow_all):
                    raise ValueError(f"{per_form_label} must be unique")
                unknown_allow_all_forms = per_form_set - set(
                    effective_writable_forms
                )
                if unknown_allow_all_forms:
                    raise ValueError(
                        f"{per_form_label} must contain only writable forms"
                    )
                if per_form_set & set(mapping):
                    raise ValueError(
                        f"{label} must omit forms listed in {per_form_label}"
                    )
                if set(mapping) | per_form_set != set(
                    effective_writable_forms
                ):
                    raise ValueError(
                        f"{label} and {per_form_label} must define every "
                        "writable form"
                    )
            if not writes_enabled and (
                mapping or allow_all_fields or per_form_allow_all
            ):
                raise ValueError(f"{label} requires access_mode read_write")
        if not writes_enabled and (
            self.writable_forms or self.allow_all_writable_forms
        ):
            raise ValueError(
                "write form scope requires access_mode read_write"
            )
        if (
            self.writable_forms
            and not self.allow_all_forms
            and not set(self.writable_forms).issubset(self.allowed_forms)
        ):
            raise ValueError(
                "writable_forms must be a subset of allowed_forms"
            )
        if self.allow_all_forms and self.allowed_forms:
            raise ValueError(
                "allowed_forms must be empty when allow_all_forms is enabled"
            )
        if self.allow_all_fields and self.allowed_fields_by_form:
            raise ValueError(
                "allowed_fields_by_form must be empty when "
                "allow_all_fields is enabled"
            )
        unknown_forms = (
            set()
            if self.allow_all_forms
            else set(self.allowed_fields_by_form) - set(self.allowed_forms)
        )
        if unknown_forms:
            raise ValueError(
                "allowed_fields_by_form keys must be included in allowed_forms"
            )
        sensitive = {field.casefold() for field in self.sensitive_fields}
        markers = tuple(
            marker.casefold() for marker in self.sensitive_field_markers
        )
        if len(set(markers)) != len(markers):
            raise ValueError("sensitive_field_markers must be unique")
        for form, fields in self.allowed_fields_by_form.items():
            folded_fields = [field.casefold() for field in fields]
            if not fields:
                raise ValueError(
                    f"allowed fields for form {form!r} cannot be empty"
                )
            if len(set(folded_fields)) != len(folded_fields):
                raise ValueError(
                    f"allowed fields for form {form!r} must be unique"
                )
            if sensitive & set(folded_fields) or any(
                marker in field
                for marker in markers
                for field in folded_fields
            ):
                raise ValueError(
                    f"allowed fields for form {form!r} contain sensitive fields"
                )
        for (
            label,
            mapping,
            _allow_all_fields,
            _per_form_label,
            _per_form,
        ) in write_mappings:
            for form, fields in mapping.items():
                folded_fields = [field.casefold() for field in fields]
                if not fields:
                    raise ValueError(
                        f"{label} for form {form!r} cannot be empty"
                    )
                if len(set(folded_fields)) != len(folded_fields):
                    raise ValueError(
                        f"{label} for form {form!r} must be unique"
                    )
                if sensitive & set(folded_fields) or any(
                    marker in field
                    for marker in markers
                    for field in folded_fields
                ):
                    raise ValueError(
                        f"{label} for form {form!r} contain sensitive fields"
                    )
        return self


class HelixConfig(FrozenModel):
    """Root configuration for one logical Helix installation."""

    schema_version: Literal[2] = 2
    server: ServerSettings = Field(default_factory=ServerSettings)
    policies: tuple[TargetPolicyConfig, ...]
    targets: tuple[TargetConfig, ...]

    @model_validator(mode="after")
    def validate_references_and_uniqueness(self) -> Self:
        if not self.policies:
            raise ValueError("at least one policy is required")
        if not self.targets:
            raise ValueError("at least one target is required")

        policy_by_name = {policy.name: policy for policy in self.policies}
        if len(policy_by_name) != len(self.policies):
            raise ValueError("policy names must be unique")

        target_environments = {target.environment for target in self.targets}
        if len(target_environments) != len(self.targets):
            raise ValueError("target environments must be unique")

        for target in self.targets:
            policy = policy_by_name.get(target.policy_ref)
            if policy is None:
                raise ValueError(
                    f"target {target.key} references unknown policy "
                    f"{target.policy_ref!r}"
                )
        return self
