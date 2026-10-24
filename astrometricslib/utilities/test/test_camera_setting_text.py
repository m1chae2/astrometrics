"""Tests for the camera setting text helpers.

Checks that gain, offset and binning are written one standard way. The
calibration library has no binning rule of its own: it uses this module's.
"""

import pytest

from astrometricslib.drivers import calibration_library
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "1x1"),
        ("", "1x1"),
        ("1x1", "1x1"),
        ("2x2", "2x2"),
        ("2X2", "2x2"),
        ("3x1", "3x1"),
        (1, "1x1"),
        (2, "2x2"),
        (2.0, "2x2"),
        ("junk", "1x1"),
        (0, "1x1"),
        (-1, "1x1"),
        ("0x2", "1x2"),
    ],
)
def test_binning_is_written_one_way(value: object, expected: str) -> None:
    """Text and numbers give the standard form; anything else is unbinned."""
    assert canonical_binning_setting(value) == expected


def test_the_calibration_library_uses_this_modules_binning_rule() -> None:
    """The library names this module's rule; it keeps no copy of its own."""
    assert calibration_library.normalize_binning("2X2") == canonical_binning_setting("2X2")
    assert calibration_library.UNBINNED == canonical_binning_setting(None)
    assert calibration_library.format_binning(2, 2) == "2x2"
