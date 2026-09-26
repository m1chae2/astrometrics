"""Tests for camera name normalisation.

Checks that the different spellings of one camera compare as equal.
"""

import pytest

from astrometricslib.utilities.camera_names import normalize_camera_name


@pytest.mark.parametrize(
    "spelling",
    ["ZWO ASI533MM Pro", "ZWO ASI 533MM Pro", "zwo-asi533mm-pro", "  ZWO  ASI533MM   Pro "],
)
def test_spellings_that_differ_only_in_case_spaces_or_dashes_match(spelling: str) -> None:
    """Check that case, spaces and dashes do not change the result."""
    assert normalize_camera_name(spelling) == "zwoasi533mmpro"


def test_genuinely_different_names_do_not_match() -> None:
    """Check that a header spelling with an extra word stays different."""
    assert normalize_camera_name("ZWO CCD ASI533MM Pro") != normalize_camera_name("ZWO ASI533MM Pro")


def test_an_empty_name_gives_an_empty_result() -> None:
    """Check that an empty name is handled without an error."""
    assert normalize_camera_name("") == ""
