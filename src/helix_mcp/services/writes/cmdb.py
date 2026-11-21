"""Exact-key SDK reads and bounded identity checks for supported CMDB classes."""

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from helix_mcp.clients.arapi import ArapiBridgeClient
from helix_mcp.clients.arapi.models import ArapiScalar
from helix_mcp.config import Environment
from helix_mcp.config.models import FrozenModel
from helix_mcp.services.forms.service import enforce_field_read_access
from helix_mcp.services.writes.errors import WritePlanStateError
from helix_mcp.targeting import ResolvedTarget

DatasetId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,255}$")
]
InstanceId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")
]
Attribute = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_. ]{0,254}$")
]
Attributes = Annotated[list[Attribute], Field(min_length=1, max_length=32)]
ClassName = Literal["BMC_ComputerSystem", "BMC_Dependency"]
IDENTITY = ("DatasetId", "InstanceId", "ClassId")
ROLES = ("Source", "Destination")
CI_FORM = "BMC.CORE:BMC_ComputerSystem"
RELATION_FORM = "BMC.CORE:BMC_Dependency"


class InstanceReference(FrozenModel):
    namespace: Literal["BMC.CORE"]
    class_name: ClassName
    dataset_id: DatasetId
    instance_id: InstanceId = Field(repr=False)

    @property
    def form(self) -> str:
        return f"{self.namespace}:{self.class_name}"


class InstanceRead(FrozenModel):
    environment: Environment
    reference: InstanceReference
    attributes: dict[str, ArapiScalar | None] = Field(repr=False)


def endpoint_fields(role: str) -> tuple[str, ...]:
    return tuple(f"{role}.{key}" for key in IDENTITY)


async def read_key(
    target: ResolvedTarget,
    client: ArapiBridgeClient,
    reference: InstanceReference,
    fields: tuple[str, ...],
) -> dict[str, ArapiScalar | None]:
    enforce_field_read_access(target, reference.form, (*IDENTITY, *fields))
    return await client.get_cmdb_instance(
        form=reference.form,
        dataset_id=reference.dataset_id,
        instance_id=reference.instance_id,
        fields=fields,
    )


async def endpoint_values(
    target: ResolvedTarget,
    client: ArapiBridgeClient,
    role: str,
    reference: InstanceReference,
) -> dict[str, str]:
    if reference.form != CI_FORM:
        raise WritePlanStateError(
            "endpoint must reference a supported CI class"
        )
    row = await read_key(target, client, reference, IDENTITY)
    result = {}
    for key in IDENTITY:
        value = row[key]
        if not isinstance(value, str) or not value.strip():
            raise WritePlanStateError("incomplete CMDB endpoint identity")
        result[f"{role}.{key}"] = value
    if (
        row["DatasetId"] != reference.dataset_id
        or row["InstanceId"] != reference.instance_id
    ):
        raise WritePlanStateError("CMDB endpoint identity differs")
    return result


async def verify_endpoints(
    target: ResolvedTarget,
    client: ArapiBridgeClient,
    dataset: str,
    values: Mapping[str, ArapiScalar | None],
    roles: tuple[str, ...] = ROLES,
) -> None:
    folded = {k.casefold(): v for k, v in values.items()}
    for role in roles:
        stored = {
            key: folded.get(key.casefold()) for key in endpoint_fields(role)
        }
        instance = stored[f"{role}.InstanceId"]
        if stored[f"{role}.DatasetId"] != dataset or not isinstance(
            instance, str
        ):
            raise WritePlanStateError(
                "incomplete or out-of-dataset CMDB endpoint"
            )
        reference = InstanceReference(
            namespace="BMC.CORE",
            class_name="BMC_ComputerSystem",
            dataset_id=dataset,
            instance_id=instance,
        )
        if await endpoint_values(target, client, role, reference) != stored:
            raise WritePlanStateError("CMDB endpoint identity differs")


async def get_instance(
    target: ResolvedTarget,
    client: ArapiBridgeClient,
    reference: InstanceReference,
    attributes: Attributes,
) -> InstanceRead:
    folded = {name.casefold() for name in attributes}
    if len(folded) != len(attributes):
        raise WritePlanStateError(
            "CMDB projection requires distinct attributes"
        )
    roles = tuple(
        role
        for role in ROLES
        if reference.form == RELATION_FORM
        and any(name.startswith(role.casefold() + ".") for name in folded)
    )
    fields = tuple(
        dict.fromkeys(
            [
                *attributes,
                *(f for role in roles for f in endpoint_fields(role)),
            ]
        )
    )
    if len(fields) > 32:
        raise WritePlanStateError(
            "CMDB projection exceeds the supported limit"
        )
    row = await read_key(target, client, reference, fields)
    await verify_endpoints(target, client, reference.dataset_id, row, roles)
    return InstanceRead(
        environment=target.key.environment,
        reference=reference,
        attributes={field: row[field] for field in attributes},
    )
