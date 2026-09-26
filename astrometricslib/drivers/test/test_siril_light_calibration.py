"""Purpose: Unit tests for the calibration masters applied to the lights.

Description: The Nikon D5300 dark masters already contain the bias level, and
Siril subtracts a bias master as well when both are given. On the 300 mm
session of NGC 7023 (2023-10-07) that drove a 30 s light (median 672 ADU, dark
598, bias 598) below zero and the stack came out blank. These tests check the
choice of masters and the parsing of Siril's negative-pixel warning, which was
printed 40 times in that run and never reached the quality summary.
"""

import pytest

from astrometricslib.drivers.siril_interface import light_calibration_flags
from astrometricslib.drivers.siril_output_parsing import parse_negative_pixel_percentage


def test_bias_is_left_off_the_lights_when_a_dark_is_applied() -> None:
    """A dark master holds the bias, so it is not subtracted twice."""
    dark_flag, flat_flag, bias_flag = light_calibration_flags(num_darks=20, num_flats=10, num_biases=100)

    assert dark_flag == "-dark=dark_stacked"
    assert flat_flag == "-flat=flat_stacked"
    assert bias_flag == ""


def test_bias_is_applied_to_the_lights_when_there_is_no_dark() -> None:
    """Without a dark master the bias still has to come off the lights."""
    assert light_calibration_flags(num_darks=0, num_flats=10, num_biases=100)[2] == "-bias=bias_stacked"


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((0, 0, 0), ("", "", "")),
        ((5, 0, 0), ("-dark=dark_stacked", "", "")),
        ((0, 4, 0), ("", "-flat=flat_stacked", "")),
    ],
)
def test_masters_that_are_missing_are_not_applied(
    counts: tuple[int, int, int], expected: tuple[str, str, str]
) -> None:
    """A master that was not found adds no option; no dark means no dark."""
    assert light_calibration_flags(*counts) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "2026-09-21 17:26:29,567 - INFO - log: After dark subtraction, the image contains many "
            "negative pixels (99%), calibration frames are probably incorrect",
            99,
        ),
        ("log: After dark subtraction, the image contains many negative pixels (45%), x", 45),
        ("log: Total: 0 failed, 140 registered.", None),
        ("", None),
    ],
)
def test_negative_pixel_warning_is_read_from_siril_lines(line: str, expected: int | None) -> None:
    """The percentage comes from Siril's warning; other lines give `None`."""
    assert parse_negative_pixel_percentage(line) == expected


def test_the_dark_exposure_is_the_most_common_one_not_the_first_frames() -> None:
    """A batch led by one odd frame gets the dark of the majority length."""
    from types import SimpleNamespace

    from astrometricslib.drivers.siril_interface import dominant_exposure

    frames = [SimpleNamespace(exposure="60.0")] + [SimpleNamespace(exposure="300.0") for _ in range(5)]

    assert dominant_exposure(frames) == "300.0"
    assert dominant_exposure([{"exposure": "30.0"}, {"exposure": "30.0"}, {"exposure": "10.0"}]) == "30.0"


def test_the_dominant_exposure_of_frames_without_one_is_zero() -> None:
    """With no readable exposure the lookup falls back to "0", as before."""
    from types import SimpleNamespace

    from astrometricslib.drivers.siril_interface import dominant_exposure

    assert dominant_exposure([]) == "0"
    assert dominant_exposure([SimpleNamespace(exposure=None)]) == "0"


def test_each_output_file_gets_its_own_work_folder() -> None:
    """Stacks of one target that run together must not share a work folder."""
    from astrometricslib.drivers.siril_interface import work_directory_name

    luminance = work_directory_name("M 13", "M_13_L_Stacked.fits")
    spectral = work_directory_name("M 13", "M_13_SPEC_Stacked.fits")

    assert luminance == "M_13__M_13_L_Stacked"
    assert spectral == "M_13__M_13_SPEC_Stacked"
    assert luminance != spectral
    assert work_directory_name("M 13", None) == "M_13"
