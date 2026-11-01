"""Purpose: Tests of `parse_coordinate_text`.

Description: Checks that coordinate text becomes degrees and that bad text
or an impossible declination is refused.
"""

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from backend.services.coordinate_text import parse_coordinate_text


def test_a_right_ascension_in_hours_becomes_degrees() -> None:
    """Six hours is ninety degrees."""
    assert parse_coordinate_text("6h 00m 00s", is_right_ascension=True) == pytest.approx(90.0)


def test_a_declination_with_a_sign_becomes_degrees() -> None:
    """A south declination stays negative."""
    assert parse_coordinate_text("-45d 30m 00s", is_right_ascension=False) == pytest.approx(-45.5)


@pytest.mark.parametrize("text", ["", "   ", "not an angle"])
def test_unreadable_text_is_refused(text: str) -> None:
    """Empty or unreadable text raises `InvalidArgumentError`."""
    with pytest.raises(InvalidArgumentError):
        parse_coordinate_text(text, is_right_ascension=True)


def test_a_declination_beyond_the_pole_is_refused() -> None:
    """Declination must be between -90 and 90 degrees."""
    with pytest.raises(InvalidArgumentError, match="out of range"):
        parse_coordinate_text("95d 00m 00s", is_right_ascension=False)
