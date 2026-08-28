"""MCP-independent adapter for form query inputs and outputs."""

from __future__ import annotations

from pydantic import ValidationError

from helix_mcp.config import Environment
from helix_mcp.services.forms import (
    FormCatalogQuery,
    FormCatalogService,
    FormEntryQuery,
    FormFieldsQuery,
    FormQuery,
    FormQueryService,
    FormSort,
)
from helix_mcp.tools.errors import ToolInputError
from helix_mcp.tools.models import (
    GetEntryOutput,
    ListFormFieldsOutput,
    ListFormsOutput,
    QueryFormOutput,
)


class FormToolAdapter:
    """Translate a tool call into the service's validated query model."""

    __slots__ = ("_catalog", "_service")

    def __init__(
        self,
        service: FormQueryService,
        catalog: FormCatalogService,
    ) -> None:
        self._service = service
        self._catalog = catalog

    async def list_forms(
        self,
        *,
        environment: Environment,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ListFormsOutput:
        try:
            query = FormCatalogQuery(
                name_contains=name_contains,
                offset=offset,
                limit=limit,
            )
        except ValidationError:
            raise ToolInputError("list_forms input is invalid") from None

        result = await self._catalog.list_forms(
            environment=environment,
            query=query,
        )
        return ListFormsOutput(
            environment=environment,
            forms=result.forms,
            offset=result.offset,
            limit=result.limit,
            total=result.total,
        )

    async def list_form_fields(
        self,
        *,
        environment: Environment,
        form: str,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ListFormFieldsOutput:
        try:
            query = FormFieldsQuery(
                form=form,
                name_contains=name_contains,
                offset=offset,
                limit=limit,
            )
        except ValidationError:
            raise ToolInputError("list_form_fields input is invalid") from None

        result = await self._service.list_fields(
            environment=environment,
            query=query,
        )
        return ListFormFieldsOutput(
            environment=environment,
            form=query.form,
            fields=result.fields,
            offset=result.offset,
            limit=result.limit,
            total=result.total,
        )

    async def query_form(
        self,
        *,
        environment: Environment,
        form: str,
        fields: tuple[str, ...],
        qualification: str | None = None,
        sort: tuple[FormSort, ...] = (),
        offset: int = 0,
        limit: int = 100,
        include_total: bool = False,
    ) -> QueryFormOutput:
        try:
            query = FormQuery(
                form=form,
                fields=fields,
                qualification=qualification,
                sort=sort,
                offset=offset,
                limit=limit,
                include_total=include_total,
            )
        except ValidationError:
            raise ToolInputError("query_form input is invalid") from None

        result = await self._service.search(
            environment=environment,
            query=query,
        )
        return QueryFormOutput(
            environment=environment,
            form=query.form,
            entries=result.entries,
            offset=result.offset,
            limit=result.limit,
            total=result.total,
        )

    async def get_entry(
        self,
        *,
        environment: Environment,
        form: str,
        entry_id: str,
        fields: tuple[str, ...],
    ) -> GetEntryOutput:
        try:
            query = FormEntryQuery(
                form=form,
                entry_id=entry_id,
                fields=fields,
            )
        except ValidationError:
            raise ToolInputError("get_entry input is invalid") from None

        result = await self._service.get_entry(
            environment=environment,
            query=query,
        )
        return GetEntryOutput(
            environment=environment,
            form=query.form,
            entry_id=result.entry_id,
            entry=result.entry,
        )
