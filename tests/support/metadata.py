"""Fictional detailed metadata used across transport and policy tests."""

import json

from helix_mcp.clients.arapi.definitions import FieldDefinition


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
