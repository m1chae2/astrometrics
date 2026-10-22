"""Tests for the camera setting text helpers.

Checks that gain, offset and binning are written one standard way, and that
the binning rule agrees with the calibration library's.
"""

import pytest

from astrometricslib.drivers.calibration_library import format_binning, normalize_binning
from astrometricslib.utilities.camera_setting_text import (
    canonical_binning_setting,
    canonical_gain_setting,
    canonical_offset_setting,
)


@pytest.mark.parametrize(
    ("value", "expected"), [(0, "0"), (0.0, "0"), ("0.0", "0"), ("800", "800"), ("Auto", "Auto")]
)
def test_gain_settings_are_written_without_a_needless_decimal(value: object, expected: str) -> None:
    """A whole-number gain loses its decimal point."""
    assert canonical_gain_setting(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "0"), ("", "0"), (10, "10"), ("10.0", "10"), (12.5, "12.5"), ("high", "high")],
)
def test_offsets_are_written_one_way_and_missing_means_zero(value: object, expected: str) -> None:
    """A missing offset counts as 0, as the calibration library does."""
    assert canonical_offset_setting(value) == expected


@pytest.mark.parametrize("value", [None, "", "1x1", "2x2", "2X2", "3x1", 1, 2, 2.0, "junk", 0, -1])
def test_binning_agrees_with_the_calibration_library(value: object) -> None:
    """The helper and `normalize_binning` give the same text."""
    assert canonical_binning_setting(value) == normalize_binning(value)


def test_binning_from_two_header_cards_matches_format_binning() -> None:
    """A single factor gives the same text as `format_binning`."""
    assert canonical_binning_setting(2) == format_binning(2, 2)
