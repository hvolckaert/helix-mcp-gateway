"""Lexical handling for AR System qualification field references."""

from __future__ import annotations


class QualificationSyntaxError(ValueError):
    """The qualification contains an unterminated quoted token."""


def referenced_fields(qualification: str) -> tuple[str, ...]:
    """Return single-quoted AR field references without string literals.

    AR System delimits field names with single quotes and character values
    with double quotes. A delimiter embedded in either token is represented by
    doubling it. Scanning both token classes prevents apostrophes inside a
    character value from being mistaken for field references.
    """

    fields: list[str] = []
    index = 0
    while index < len(qualification):
        delimiter = qualification[index]
        if delimiter not in {"'", '"'}:
            index += 1
            continue
        value, index = _read_quoted_token(
            qualification,
            start=index,
            delimiter=delimiter,
        )
        if delimiter == "'":
            if not value:
                raise QualificationSyntaxError(
                    "qualification contains an empty field reference"
                )
            fields.append(value)
    return tuple(fields)


def _read_quoted_token(
    qualification: str,
    *,
    start: int,
    delimiter: str,
) -> tuple[str, int]:
    value: list[str] = []
    index = start + 1
    while index < len(qualification):
        character = qualification[index]
        if character != delimiter:
            value.append(character)
            index += 1
            continue
        if (
            index + 1 < len(qualification)
            and qualification[index + 1] == delimiter
        ):
            value.append(delimiter)
            index += 2
            continue
        return "".join(value), index + 1
    raise QualificationSyntaxError(
        "qualification contains an unterminated quoted token"
    )
