"""Purpose: Tests of `parse_coordinate_text`.

Description: Checks that coordinate text becomes degrees and that bad text
or an impossible declination is refused. A table of inputs the app can send
(colons, spaces, unit marks, signs, bare numbers, blanks, junk) pins both the
answer and the error, and a second test checks that the answers match the
library's own `parse_coordinate_string`. The backend keeps its own copy
because it may not import the library's private modules.
"""

from typing import cast

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
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


READABLE = [
    # (text, is_right_ascension, degrees)
    ("12:30:00", True, 187.5),
    ("12 30 00", True, 187.5),
    ("12h 30m 00s", True, 187.5),
    ("12h30m00s", True, 187.5),
    ("12:30", True, 187.5),
    ("12 30", True, 187.5),
    ("12h", True, 180.0),
    ("12.5", True, 187.5),
    ("12.5h", True, 187.5),
    ("12:30:00.5", True, 187.50208333),
    ("+0 30 00", True, 7.5),
    ("-0:30:00", True, -7.5),
    ("0", True, 0.0),
    ("0h 0m 0s", True, 0.0),
    ("24:00:00", True, 360.0),
    ("30.5", True, 457.5),
    ("12:30:00", False, 12.5),
    ("12 30 00", False, 12.5),
    ("-45:10:00", False, -45.16666667),
    ("+45 10 00", False, 45.16666667),
    ("-45d 10m 00s", False, -45.16666667),
    ("\u221245:10:00", False, -45.16666667),
    ("12.5d", False, 12.5),
    ("30.5", False, 30.5),
    ("90", False, 90.0),
    ("-90", False, -90.0),
    ("0", False, 0.0),
    ("0deg 0' 0\u2032\u2032", False, 0.0),
]
"""Texts that read, with the degrees they give.

Notes
-----
A right ascension is not limited to 0 to 360 degrees: ``"30.5"`` gives 457.5.
Only a declination is range-checked. This is what the code did before the
table was written down, so the table keeps it.
"""

UNREADABLE = [
    ("", True),
    ("   ", False),
    ("junk", True),
    ("junk", False),
    ("-45:10:00", True),
    ("+45 10 00", True),
    ("95", False),
    ("95d 00m 00s", False),
    ("-95:00:00", False),
    ("25:00:00", True),
    ("1e1", True),
    ("1 2 3 4", False),
    ("12h 30m 00s", False),
    ("12.5h", False),
    ("nan", True),
    ("inf", False),
]
"""Texts that are refused: (text, is_right_ascension)."""


@pytest.mark.parametrize(("text", "is_right_ascension", "degrees"), READABLE)
def test_readable_text_gives_the_pinned_degrees(text: str, is_right_ascension: bool, degrees: float) -> None:
    """Each readable text gives its pinned angle."""
    assert parse_coordinate_text(text, is_right_ascension) == pytest.approx(degrees, abs=1e-7)


@pytest.mark.parametrize(("text", "is_right_ascension"), UNREADABLE)
def test_refused_text_stays_refused(text: str, is_right_ascension: bool) -> None:
    """Each refused text raises `InvalidArgumentError`."""
    with pytest.raises(InvalidArgumentError):
        parse_coordinate_text(text, is_right_ascension)


@pytest.mark.parametrize(
    ("text", "is_right_ascension"),
    [(text, ra) for text, ra, _degrees in READABLE] + UNREADABLE,
)
def test_the_answer_matches_the_library_parser(text: str, is_right_ascension: bool) -> None:
    """The backend and the library read every table text the same way."""
    try:
        expected = parse_coordinate_string(text, is_ra=is_right_ascension)
    except InvalidArgumentError:
        with pytest.raises(InvalidArgumentError):
            parse_coordinate_text(text, is_right_ascension)
    else:
        assert parse_coordinate_text(text, is_right_ascension) == expected


@pytest.mark.parametrize("not_text", [None, 5])
def test_something_that_is_not_text_is_refused(not_text: object) -> None:
    """A missing or numeric value raises `InvalidArgumentError`."""
    with pytest.raises(InvalidArgumentError):
        parse_coordinate_text(cast("str", not_text), is_right_ascension=True)
