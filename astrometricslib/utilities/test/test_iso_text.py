"""Tests for how ISO and gain text is written and compared.

The rule: an ISO from the header's ISOSPEED is written without a needless
decimal ("800", not "800.0"), a GAIN keeps its own text, and settings are
compared as numbers.
"""

import pytest

from astrometricslib.utilities.iso_text import (
    canonical_iso_text,
    iso_or_gain_text,
    iso_or_gain_values_match,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (800, "800"),
        (800.0, "800"),
        ("800.0", "800"),
        ("800", "800"),
        (" 100.0 ", "100"),
        (0.0, "0"),
        (12.5, "12.5"),
        ("Auto", "Auto"),
    ],
)
def test_an_iso_is_written_without_a_needless_decimal(value: object, expected: str) -> None:
    """Check whole numbers, decimals that stay, and non-numeric text."""
    assert canonical_iso_text(value) == expected


def test_an_isospeed_is_written_without_a_decimal() -> None:
    """Check the case seen in real D5300 headers: ISOSPEED written as 100.0."""
    assert iso_or_gain_text({"ISOSPEED": 100.0}) == "100"
    assert iso_or_gain_text({"ISOSPEED": 800}) == "800"


def test_a_gain_keeps_the_text_of_the_header() -> None:
    """Check that the ASI533's gain stays 0.0, so session ids do not change."""
    assert iso_or_gain_text({"GAIN": 0.0}) == "0.0"
    assert iso_or_gain_text({"GAIN": 121.0}) == "121.0"


def test_isospeed_is_used_before_gain_and_a_header_with_neither_gives_none() -> None:
    """Check the order, and the missing case."""
    assert iso_or_gain_text({"ISOSPEED": 400, "GAIN": 0.0}) == "400"
    assert iso_or_gain_text({"EXPTIME": 30}) is None


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("800", "800.0", True),
        (800, "800.0", True),
        ("0", "0.0", True),
        ("100", "200", False),
        ("Auto", "Auto", True),
        ("Auto", "800", False),
    ],
)
def test_settings_are_compared_by_number(first: object, second: object, expected: bool) -> None:
    """Check numeric equality, and text equality for non-numbers."""
    assert iso_or_gain_values_match(first, second) is expected
