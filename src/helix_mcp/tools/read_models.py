"""Additive MCP read contracts; service and mutation models stay separate."""

from helix_mcp.config.models import FrozenModel
from helix_mcp.provenance import ReadProvenance
from helix_mcp.tools import models as payloads


class ProvenancedOutput(FrozenModel):
    provenance: ReadProvenance


class ListTargetsOutput(payloads.ListTargetsOutput, ProvenancedOutput):
    pass


class HealthCheckOutput(payloads.HealthCheckOutput, ProvenancedOutput):
    pass


class ListFormsOutput(payloads.ListFormsOutput, ProvenancedOutput):
    pass


class ListFormFieldsOutput(payloads.ListFormFieldsOutput, ProvenancedOutput):
    pass


class ListFormViewsOutput(payloads.ListFormViewsOutput, ProvenancedOutput):
    pass


class GetFormViewDefinitionOutput(
    payloads.GetFormViewDefinitionOutput, ProvenancedOutput
):
    pass


class GetFormFieldDefinitionOutput(
    payloads.GetFormFieldDefinitionOutput, ProvenancedOutput
):
    pass


class GetFormFieldSelectionValuesOutput(
    payloads.GetFormFieldSelectionValuesOutput, ProvenancedOutput
):
    pass


class GetFormFieldMenuMetadataOutput(
    payloads.GetFormFieldMenuMetadataOutput, ProvenancedOutput
):
    pass


class ResolveFormFieldMenuValuesOutput(
    payloads.ResolveFormFieldMenuValuesOutput, ProvenancedOutput
):
    pass


class QueryFormOutput(payloads.QueryFormOutput, ProvenancedOutput):
    pass


class GetEntryOutput(payloads.GetEntryOutput, ProvenancedOutput):
    pass


class ListDatabaseObjectsOutput(
    payloads.ListDatabaseObjectsOutput, ProvenancedOutput
):
    pass


class ListDatabaseColumnsOutput(
    payloads.ListDatabaseColumnsOutput, ProvenancedOutput
):
    pass


class DescribeDatabaseObjectOutput(
    payloads.DescribeDatabaseObjectOutput, ProvenancedOutput
):
    pass


class ExecuteSqlQueryOutput(payloads.ExecuteSqlQueryOutput, ProvenancedOutput):
    pass


class GetSqlQueryPlanOutput(payloads.SqlQueryPlanOutput, ProvenancedOutput):
    pass


class GetWritePlanOutput(payloads.WritePlanOutput, ProvenancedOutput):
    pass
