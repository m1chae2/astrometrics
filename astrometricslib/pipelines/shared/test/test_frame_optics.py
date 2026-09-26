"""Tests for working out which optic took a frame.

Uses the same optics and pairings as the example config: the ASI533 on the
Apertura, and the D5300 on both the Apertura and the Nikkor lens.
"""

import logging

import pytest

from astrometricslib.pipelines.shared.frame_optics import (
    REASON_FOCAL_LENGTH,
    REASON_ONLY_SETUP,
    REASON_PATH_NAME,
    REASON_UNRESOLVED,
    UNKNOWN_TELESCOPE_NAME,
    resolve_frame_telescope,
)
from astrometricslib.utilities.observatory_setups import (
    ObservatorySetups,
    OpticConfig,
    SetupConfig,
)
from astrometricslib.utilities.warn_once import warn_once

APERTURA = OpticConfig(name="Apertura 75Q", focal_length_mm=405.0, focal_ratio=5.4)
NIKKOR = OpticConfig(name="Nikkor 300mm", focal_length_mm=300.0, focal_ratio=5.6)
SETUPS = ObservatorySetups(
    optics=(APERTURA, NIKKOR),
    setups=(
        SetupConfig(name="ASI533 on Apertura", camera_name="ZWO ASI533MM Pro", optic_name="Apertura 75Q"),
        SetupConfig(name="D5300 on Apertura", camera_name="Nikon D5300", optic_name="Apertura 75Q"),
        SetupConfig(name="D5300 on Nikkor", camera_name="Nikon D5300", optic_name="Nikkor 300mm"),
    ),
)


@pytest.fixture(autouse=True)
def fresh_warning_cache() -> None:
    """Forget which warnings were already logged, for each test."""
    warn_once.cache_clear()


@pytest.mark.parametrize(
    ("camera", "focal_length_mm", "expected_optic"),
    [
        ("Nikon DSLR DSC D5300", 300.0, "Nikkor 300mm"),
        ("Nikon DSLR DSC D5300", 405.0, "Apertura 75Q"),
        ("ZWO ASI 533MM Pro", 405.0, "Apertura 75Q"),
        ("ZWO CCD ASI533MM Pro", 405.0, "Apertura 75Q"),
        ("Nikon D5300", 297.0, "Nikkor 300mm"),
    ],
)
def test_a_recorded_focal_length_picks_the_matching_optic(
    camera: str, focal_length_mm: float, expected_optic: str
) -> None:
    """Check the main rule, using the spellings that appear in real headers."""
    result = resolve_frame_telescope(camera, focal_length_mm, "/any/path.fits", SETUPS)
    assert result.telescope_name == expected_optic
    assert result.reason == REASON_FOCAL_LENGTH


def test_the_focal_length_wins_over_a_path_that_names_another_optic() -> None:
    """Check the disagreement found in the real library: 764 frames."""
    result = resolve_frame_telescope(
        "Nikon DSLR DSC D5300", 300.0, "/frames/lights/M31/Apertura 75Q/frame.fits", SETUPS
    )
    assert result.telescope_name == "Nikkor 300mm"


def test_a_focal_length_matching_no_optic_of_that_camera_is_unknown_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check the ASI533 is not given the Nikkor it is not paired with."""
    with caplog.at_level(logging.WARNING):
        result = resolve_frame_telescope("ZWO ASI 533MM Pro", 300.0, "/x.fits", SETUPS)
    assert result.telescope_name == UNKNOWN_TELESCOPE_NAME
    assert result.reason == REASON_UNRESOLVED
    assert len(caplog.records) == 1


def test_the_tolerance_is_two_percent() -> None:
    """Check just inside and just outside the tolerance."""
    assert (
        resolve_frame_telescope("Nikon D5300", 405.0 * 1.019, None, SETUPS).telescope_name == "Apertura 75Q"
    )
    assert resolve_frame_telescope("Nikon D5300", 405.0 * 1.03, None, SETUPS).telescope_name == "Unknown"


def test_without_a_focal_length_a_camera_with_one_setup_gets_that_optic() -> None:
    """Check the second rule."""
    result = resolve_frame_telescope("ZWO ASI 533MM Pro", None, "/x.fits", SETUPS)
    assert result.telescope_name == "Apertura 75Q"
    assert result.reason == REASON_ONLY_SETUP


def test_without_a_focal_length_the_path_can_pick_between_two_optics() -> None:
    """Check the old rule, kept for frames that carry no focal length."""
    result = resolve_frame_telescope("Nikon D5300", None, "/frames/lights/M31/Nikkor 300mm/a.fits", SETUPS)
    assert result.telescope_name == "Nikkor 300mm"
    assert result.reason == REASON_PATH_NAME


def test_without_a_focal_length_and_a_path_that_names_nothing_the_frame_is_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that two possible optics are not guessed between."""
    with caplog.at_level(logging.WARNING):
        result = resolve_frame_telescope("Nikon D5300", None, "/frames/lights/M31/a.fits", SETUPS)
    assert result.telescope_name == UNKNOWN_TELESCOPE_NAME
    assert len(caplog.records) == 1


def test_a_path_naming_both_optics_is_not_guessed() -> None:
    """Check that an ambiguous path is unknown."""
    path = "/Nikkor 300mm/and/Apertura 75Q/a.fits"
    assert resolve_frame_telescope("Nikon D5300", None, path, SETUPS).telescope_name == "Unknown"


def test_a_camera_in_no_setup_is_unknown_with_one_warning_per_case(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check the warning is not repeated for every frame."""
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            result = resolve_frame_telescope("Acme Imager 9000", 405.0, "/x.fits", SETUPS)
    assert result.telescope_name == UNKNOWN_TELESCOPE_NAME
    assert len([r for r in caplog.records if "Could not tell which optic" in r.getMessage()]) == 1


def test_no_setups_in_the_config_gives_unknown() -> None:
    """Check an older config with no setups gives unknown, not a crash."""
    result = resolve_frame_telescope("ZWO ASI 533MM Pro", 405.0, "/x.fits", ObservatorySetups())
    assert result.telescope_name == UNKNOWN_TELESCOPE_NAME


@pytest.mark.parametrize("camera", [None, ""])
def test_no_camera_gives_unknown(camera: str | None) -> None:
    """Check a frame with no camera name."""
    assert resolve_frame_telescope(camera, 405.0, "/x.fits", SETUPS).telescope_name == UNKNOWN_TELESCOPE_NAME
