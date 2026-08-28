"""Safety helpers for opt-in live validation."""

from __future__ import annotations


def live_query_qualification(
    *,
    entry_id: str,
    qualification: str | None,
    id_field: str,
) -> str:
    """Return an approved qualification or an exact entry-ID selector."""

    if qualification is not None:
        return qualification
    if not entry_id:
        raise AssertionError(
            "set HELIX_LIVE_ENTRY_ID or HELIX_LIVE_QUALIFICATION "
            "to select an approved live record"
        )
    if not id_field or _contains_control_character(id_field):
        raise AssertionError("the live form core ID field is invalid")
    if _contains_control_character(entry_id):
        raise AssertionError("HELIX_LIVE_ENTRY_ID contains control characters")

    escaped_field = id_field.replace("\\", "\\\\").replace("'", "\\'")
    escaped_entry_id = entry_id.replace("\\", "\\\\").replace('"', '\\"')
    return f"'{escaped_field}' = \"{escaped_entry_id}\""


def _contains_control_character(value: str) -> bool:
    return any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    )
