"""Tests for Right Ascension wraparound in the moving object detector.

Right Ascension (RA) is an angle that wraps from 360 deg back to 0 deg. A
field, a moving object, or a star close to RA = 0 deg can have detections
on both sides of that line. These tests check that the detector treats
two such points as close together, so a mover is still chained, and a
star near the line is still rejected as stationary.
"""

import math

import pytest

from astrometricslib.models.moving_object import CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.detection import (
    MovingObjectDetector,
    _tangent_plane_offset_arcsec,
    wrapped_ra_difference_deg,
)


def _make_detection(
    frame_index: int,
    timestamp: float,
    pixel_x: float,
    pixel_y: float,
    right_ascension_deg: float,
    declination_deg: float,
) -> FrameDetection:
    """Build one fake detection in its own frame.

    Parameters
    ----------
    frame_index : `int`
        Number used to name the frame this detection belongs to.
    timestamp : `float`
        Time of the frame, in seconds.
    pixel_x : `float`
        Column of the detection on the sensor, in pixels.
    pixel_y : `float`
        Row of the detection on the sensor, in pixels.
    right_ascension_deg : `float`
        Right Ascension of the detection, in degrees.
    declination_deg : `float`
        Declination of the detection, in degrees.

    Returns
    -------
    detection : `FrameDetection`
        The fake detection.
    """
    return FrameDetection(
        frame_path=f"frame{frame_index}.fits",
        timestamp=timestamp,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        right_ascension_deg=right_ascension_deg,
        declination_deg=declination_deg,
    )


@pytest.mark.parametrize(
    ("ra1_deg", "ra2_deg", "expected_deg"),
    [
        (359.9, 0.1, -0.2),
        (0.1, 359.9, 0.2),
        (10.0, 350.0, 20.0),
        (350.0, 10.0, -20.0),
        (150.01, 150.0, 0.01),
        (0.0, 0.0, 0.0),
        (190.0, 0.0, -170.0),
        (180.0, 0.0, 180.0),
        (0.0, 180.0, 180.0),
    ],
)
def test_wrapped_ra_difference_takes_the_short_way_around(
    ra1_deg: float, ra2_deg: float, expected_deg: float
) -> None:
    """Check the helper's signed value near and away from the wrap."""
    assert wrapped_ra_difference_deg(ra1_deg, ra2_deg) == pytest.approx(expected_deg, abs=1e-9)


def test_wrapped_ra_difference_accepts_arrays() -> None:
    """Check that the helper wraps every element of an array."""
    import numpy as np

    result = wrapped_ra_difference_deg(np.array([359.9, 10.0, 0.3]), 0.1)

    assert result == pytest.approx([-0.2, 9.9, 0.2], abs=1e-9)


def test_tangent_plane_offset_wraps_across_zero_hours() -> None:
    """Check that points on opposite sides of RA = 0 deg are close."""
    right_ascension_offset, declination_offset = _tangent_plane_offset_arcsec(0.001, 0.0, 359.999, 0.0)

    assert right_ascension_offset == pytest.approx(0.002 * 3600.0)
    assert declination_offset == pytest.approx(0.0)


def test_mover_crossing_ra_zero_forms_a_chain_with_the_right_rate() -> None:
    """Check that a mover crossing RA = 0 deg is chained and measured."""
    declination_deg = 20.0
    ra_step_deg = 0.005
    seconds_between_frames = 600.0
    ra_values_deg = [359.995, 0.0, 0.005]
    detections = [
        _make_detection(
            index,
            index * seconds_between_frames,
            100.0 + 20.0 * index,
            100.0,
            ra_deg,
            declination_deg,
        )
        for index, ra_deg in enumerate(ra_values_deg)
    ]

    candidates = MovingObjectDetector(MovingObjectConfig()).detect_candidates("WrapMover", detections)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert len(candidate.frame_detections) == 3
    assert candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    expected_rate_arcsec_per_hour = (
        ra_step_deg * math.cos(math.radians(declination_deg)) * 3600.0 / (seconds_between_frames / 3600.0)
    )
    assert candidate.track is not None
    assert candidate.track.right_ascension_rate_arcsec_per_hour == pytest.approx(
        expected_rate_arcsec_per_hour, rel=1e-6
    )
    assert candidate.track.declination_rate_arcsec_per_hour == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    "star_ra_deg",
    [359.999, 359.9999],
    ids=["near_wrap", "straddling_wrap"],
)
def test_stationary_star_near_ra_zero_is_still_rejected(star_ra_deg: float) -> None:
    """Check that a star near RA = 0 deg stays rejected as stationary.

    The camera dithers (shifts a little between frames), so the star lands
    on different pixels. Its sky position changes by less than the
    tolerance. The jitter in RA is large enough that, for the second
    case, some frames fall on each side of the wrap.
    """
    ra_jitter_deg = [0.0, 0.0002, -0.0002, 0.0001, -0.0001]
    detections = [
        _make_detection(
            index,
            index * 60.0,
            100.0 + 5.0 * index,
            100.0 + 3.0 * index,
            (star_ra_deg + jitter_deg) % 360.0,
            0.0,
        )
        for index, jitter_deg in enumerate(ra_jitter_deg)
    ]
    ra_values_deg = [detection.right_ascension_deg for detection in detections]
    if star_ra_deg > 359.9995:
        assert min(ra_values_deg) < 1.0
        assert max(ra_values_deg) > 359.0

    candidates = MovingObjectDetector(MovingObjectConfig()).detect_candidates("WrapStar", detections)

    assert len(candidates) == 1
    assert len(candidates[0].frame_detections) == len(detections)
    assert candidates[0].cascade_stage == CascadeStage.REJECTED_STATIONARY_SKY
