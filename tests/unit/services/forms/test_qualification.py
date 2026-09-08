"""Tests for policy-safe AR qualification tokenization."""

from __future__ import annotations

import pytest

from helix_mcp.services.forms.qualification import (
    QualificationSyntaxError,
    referenced_fields,
)


@pytest.mark.parametrize(
    ("qualification", "expected"),
    (
        ("'Status' = \"Assigned\"", ("Status",)),
        (
            "('Assigned To' = \"O'Brien\") AND 'Status' != $NULL$",
            ("Assigned To", "Status"),
        ),
        ("'Doug''s Requests' = \"Open\"", ("Doug's Requests",)),
        (
            '\'Status\' = "AR ""System"" User"',
            ("Status",),
        ),
        (
            "'Status History.Fixed.TIME' < \"07/01/99\"",
            ("Status History.Fixed.TIME",),
        ),
    ),
)
def test_referenced_fields_obeys_ar_quoting(
    qualification: str,
    expected: tuple[str, ...],
) -> None:
    assert referenced_fields(qualification) == expected


@pytest.mark.parametrize(
    "qualification",
    (
        '\'Status = "Assigned"',
        "'Status' = \"Assigned",
        "'' = \"Assigned\"",
    ),
)
def test_referenced_fields_rejects_malformed_quotes(
    qualification: str,
) -> None:
    with pytest.raises(QualificationSyntaxError):
        referenced_fields(qualification)
