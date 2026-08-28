"""Regression tests for safe opt-in live record selection."""

from __future__ import annotations

import pytest

from tests.support.live_validation import live_query_qualification


def test_explicit_live_qualification_is_preserved() -> None:
    qualification = "'Status' = \"Enabled\""

    assert (
        live_query_qualification(
            entry_id="",
            qualification=qualification,
            id_field="Request ID",
        )
        == qualification
    )


def test_live_entry_id_becomes_an_exact_escaped_qualification() -> None:
    assert (
        live_query_qualification(
            entry_id='record\\part"one',
            qualification=None,
            id_field="Request' ID",
        )
        == "'Request\\' ID' = \"record\\\\part\\\"one\""
    )


def test_live_validation_rejects_an_unqualified_record_read() -> None:
    with pytest.raises(
        AssertionError,
        match="HELIX_LIVE_ENTRY_ID or HELIX_LIVE_QUALIFICATION",
    ):
        live_query_qualification(
            entry_id="",
            qualification=None,
            id_field="Request ID",
        )


def test_live_entry_id_rejects_control_characters() -> None:
    with pytest.raises(AssertionError, match="control characters"):
        live_query_qualification(
            entry_id="record\nother",
            qualification=None,
            id_field="Request ID",
        )
