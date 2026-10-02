"""Purpose: Unit tests for reading Siril's registration and stacking counts.

Description: How many frames Siril managed to register decides whether a
spectral stack is retried with a different star detection, and it used to
appear only in a log line. These tests check the two parsers against lines
copied from real runs (the Vega session, 2026-08-25 and 2026-09-20).
"""

import pytest

from astrometricslib.drivers.siril_output_parsing import parse_registration_totals, parse_stacked_image_count


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("2026-08-25 17:55:53,747 - INFO - log: Total: 0 failed, 140 registered.", (0, 140)),
        ("2026-09-20 06:43:35,012 - INFO - log: Total: 46 failed, 94 registered.", (46, 94)),
        ("log: Total: 15 failed, 20 registered.", (15, 20)),
        ("Total:  3 failed,  7 registered", (3, 7)),
    ],
)
def test_registration_totals_are_read_from_siril_lines(line: str, expected: tuple[int, int]) -> None:
    """The failed and registered counts come out of Siril's summary line."""
    assert parse_registration_totals(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "log: Matching stars in image 11: done",
        "log: Initial pair matches: 16",
        "log: Total exposure 250 s",
        "",
    ],
)
def test_other_lines_are_not_registration_totals(line: str) -> None:
    """Lines that merely mention totals or matches give `None`."""
    assert parse_registration_totals(line) is None


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("17:56:20,947 - INFO - log: Rejection stacking complete. 140 images have been stacked.", 140),
        ("06:43:52,049 - INFO - log: Rejection stacking complete. 94 images have been stacked.", 94),
    ],
)
def test_stacked_image_count_is_read_from_siril_lines(line: str, expected: int | None) -> None:
    """The number of stacked images comes out of Siril's stacking summary."""
    assert parse_stacked_image_count(line) == expected


def test_other_lines_are_not_a_stacked_count() -> None:
    """A line that is not the stacking summary gives `None`."""
    assert parse_stacked_image_count("log: Integration of 94 images on 94 of the sequence:") is None
