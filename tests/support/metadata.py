"""Fictional detailed metadata used across transport and policy tests."""

import json

from helix_mcp.clients.arapi.definitions import (
    FieldDefinition,
    ViewDefinition,
    ViewSummary,
)


def field_definition_payload(
    field_id=1, name="Incident Number", datatype="CHAR"
):
    return {
        "id": field_id,
        "name": name,
        "datatype": datatype,
        "field_type": {"code": 1, "name": "AR_FIELD_TYPE_DATA"},
        "option": {"code": 1, "name": "AR_FIELD_OPTION_REQUIRED"},
        "create_mode": {"code": 1, "name": None},
        "set_field_options": 0,
        "audit_option": 0,
        "default_value": {
            "status": "present",
            "datatype": "CHAR",
            "value": " DEFAULT-SENTINEL ",
        },
        "limits": {
            "status": "supported",
            "properties": {
                "max_length": {
                    "status": "present",
                    "datatype": "INTEGER",
                    "value": 255,
                },
                "length_units": {
                    "status": "present",
                    "datatype": "INTEGER",
                    "value": 1,
                },
            },
        },
        "permissions": [
            {
                "group_id": 1,
                "permission": {"code": 2, "name": "AR_PERMISSIONS_CHANGE"},
            }
        ],
    }


def field_definition(**kwargs):
    return FieldDefinition.model_validate_json(
        json.dumps(field_definition_payload(**kwargs))
    )


def view_summary_payload(view_id=100):
    return {
        "id": view_id,
        "name": "Standard" if view_id == 100 else "Progressive",
        "locale": "en_US",
        "view_type": {
            "code": 1 if view_id == 100 else 9,
            "name": "AR_VUI_TYPE_STANDARD"
            if view_id == 100
            else "AR_VUI_TYPE_PROGRESSIVE",
        },
    }


def view_summary(view_id=100):
    return ViewSummary.model_validate_json(
        json.dumps(view_summary_payload(view_id))
    )


def view_definition_payload(view_id=100, fields=None):
    fields = (
        fields
        if fields is not None
        else [{"id": 1, "name": "Incident Number", "datatype": "CHAR"}]
    )
    return view_summary_payload(view_id) | {
        "properties": [],
        "fields": [
            item
            | {
                "present_in_view": True,
                "shared_properties": [],
                "view_properties": [
                    {
                        "id": 5,
                        "name": "AR_DPROP_ENABLE",
                        "value": {
                            "status": "present",
                            "datatype": "INTEGER",
                            "value": 1,
                        },
                        "value_symbol": "AR_DVAL_ENABLE_READ_ONLY",
                    },
                    {
                        "id": 20,
                        "name": "AR_DPROP_LABEL",
                        "value": {
                            "status": "present",
                            "datatype": "CHAR",
                            "value": "PRIVATE-LABEL-SENTINEL",
                        },
                        "value_symbol": None,
                    },
                ],
            }
            for item in fields
        ],
    }


def view_definition(**kwargs):
    return ViewDefinition.model_validate_json(
        json.dumps(view_definition_payload(**kwargs))
    )
