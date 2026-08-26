"""Tests for the MovingObjectDetector (the tool that finds the asteroids).

These tests create fake dots that move in different ways (like a cosmic
ray, a hot camera pixel, a wiggly line, or a real asteroid) to make sure
the detector correctly filters out the junk and finds the real ones.
"""

import math

import pytest

from astrometricslib.models.moving_object import CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.tasks.moving_object_tasks.moving_object_detection_tasks import (
    MovingObjectDetector,
    _fit_linear_rate_arcsec_per_hour,
    _tangent_plane_offset_arcsec,
)


def _make_detection(
    frame_path,  # ruff: ignore[missing-type-function-argument]
    timestamp,  # ruff: ignore[missing-type-function-argument]
    pixel_x,  # ruff: ignore[missing-type-function-argument]
    pixel_y,  # ruff: ignore[missing-type-function-argument]
    right_ascension_deg,  # ruff: ignore[missing-type-function-argument]
    declination_deg,  # ruff: ignore[missing-type-function-argument]
) -> FrameDetection:
    """Create a fake dot with some default stats just for testing.

    Returns
    -------
    FrameDetection
        A fake dot located where we want it.
    """
    return FrameDetection(
        frame_path=frame_path,
        timestamp=timestamp,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        right_ascension_deg=right_ascension_deg,
        declination_deg=declination_deg,
        flux=500.0,
        sharpness=0.5,
        photutils_roundness1=0.1,
    )


def test_tangent_plane_offset_arcsec_matches_known_declination_scaling():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that our flat-sky math works correctly at different latitudes."""
    right_ascension_offset, declination_offset = _tangent_plane_offset_arcsec(150.01, 60.0, 150.0, 60.0)
    expected_right_ascension_offset = 0.01 * math.cos(math.radians(60.0)) * 3600.0
    assert right_ascension_offset == pytest.approx(expected_right_ascension_offset)
    assert declination_offset == pytest.approx(0.0)


def test_fit_linear_rate_arcsec_per_hour_recovers_known_rate():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that we measure the speed of an object in a straight line."""
    import numpy as np

    timestamps = np.array([0.0, 600.0, 1200.0, 1800.0])
    known_rate_arcsec_per_hour = 20.0
    offsets_arcsec = known_rate_arcsec_per_hour * (timestamps / 3600.0)

    rate, r_squared = _fit_linear_rate_arcsec_per_hour(timestamps, offsets_arcsec)

    assert rate == pytest.approx(known_rate_arcsec_per_hour)
    assert r_squared == pytest.approx(1.0)


def test_detect_candidates_rejects_single_frame_apparition_as_cosmic_ray():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that a dot that only appears in one photo gets ignored."""
    detector = MovingObjectDetector(MovingObjectConfig())
    detections = [_make_detection("frame0.fits", 0.0, 100.0, 100.0, 150.0, 30.0)]

    candidates = detector.detect_candidates("TestTarget", detections)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.REJECTED_SINGLE_FRAME


def test_detect_candidates_rejects_stationary_sky_as_missed_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test a still dot on the sky gets ignored (just a normal star)."""
    detector = MovingObjectDetector(MovingObjectConfig())
    detections = [
        _make_detection("frame0.fits", 0.0, 100.0, 100.0, 150.0, 30.0),
        _make_detection("frame1.fits", 600.0, 300.0, 300.0, 150.0, 30.0),
        _make_detection("frame2.fits", 1200.0, 500.0, 500.0, 150.0, 30.0),
    ]

    candidates = detector.detect_candidates("TestTarget", detections)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.REJECTED_STATIONARY_SKY


def test_detect_candidates_rejects_stationary_pixel_as_hot_pixel():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test a dot that never moves on the sensor is ignored (broken pixel)."""
    detector = MovingObjectDetector(MovingObjectConfig())
    detections = [
        _make_detection("frame0.fits", 0.0, 400.0, 400.0, 150.00, 30.0),
        _make_detection("frame1.fits", 600.0, 400.0, 400.0, 150.01, 30.0),
        _make_detection("frame2.fits", 1200.0, 400.0, 400.0, 150.02, 30.0),
    ]

    candidates = detector.detect_candidates("TestTarget", detections)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.REJECTED_STATIONARY_PIXEL


def test_detect_candidates_rejects_nonlinear_track():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that an object moving in a zig-zag line gets ignored."""
    config = MovingObjectConfig(rate_max_arcsec_per_hour=10000.0)
    detector = MovingObjectDetector(config)
    detections = [
        _make_detection("frame0.fits", 0.0, 100.0, 100.0, 150.0, 30.0),
        _make_detection("frame1.fits", 600.0, 150.0, 150.0, 150.0 + 50.0 / 3600.0, 30.0),
        _make_detection("frame2.fits", 1200.0, 200.0, 200.0, 150.0, 30.0),
        _make_detection("frame3.fits", 1800.0, 250.0, 250.0, 150.0 + 50.0 / 3600.0, 30.0),
    ]

    candidates = detector.detect_candidates("TestTarget", detections)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE
    assert candidates[0].track is None


def test_detect_candidates_links_a_track_near_the_celestial_pole():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test we still connect dots if the telescope points near the North Pole.

    Near the North Pole, the lines of longitude get very close together,
    so things can look like they are moving very fast horizontally when
    they actually aren't.
    """
    detector = MovingObjectDetector(MovingObjectConfig())
    reference_right_ascension_deg = 150.0
    reference_declination_deg = 89.9
    cos_reference_declination = math.cos(math.radians(reference_declination_deg))
    right_ascension_rate_arcsec_per_hour = 180.0
    declination_rate_arcsec_per_hour = 10.0
    timestamps = [0.0, 600.0, 1200.0, 1800.0]
    pixel_positions = [(100.0, 100.0), (150.0, 160.0), (200.0, 215.0), (250.0, 270.0)]

    detections = []
    for index, timestamp in enumerate(timestamps):
        elapsed_hours = timestamp / 3600.0
        right_ascension_offset_deg = (right_ascension_rate_arcsec_per_hour * elapsed_hours) / (
            3600.0 * cos_reference_declination
        )
        right_ascension_deg = reference_right_ascension_deg + right_ascension_offset_deg
        declination_deg = reference_declination_deg + (
            declination_rate_arcsec_per_hour * elapsed_hours / 3600.0
        )
        pixel_x, pixel_y = pixel_positions[index]
        detections.append(
            _make_detection(
                f"frame{index}.fits", timestamp, pixel_x, pixel_y, right_ascension_deg, declination_deg
            )
        )

    candidates = detector.detect_candidates("TestTarget", detections)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    assert candidates[0].track is not None


def test_detect_candidates_confirms_a_clean_linear_track():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that a perfectly normal moving asteroid passes all the tests."""
    detector = MovingObjectDetector(MovingObjectConfig())
    reference_right_ascension_deg = 150.0
    reference_declination_deg = 0.0
    right_ascension_rate_arcsec_per_hour = 20.0
    declination_rate_arcsec_per_hour = 10.0
    timestamps = [0.0, 600.0, 1200.0, 1800.0]
    pixel_positions = [(100.0, 100.0), (150.0, 160.0), (200.0, 215.0), (250.0, 270.0)]

    detections = []
    for index, timestamp in enumerate(timestamps):
        elapsed_hours = timestamp / 3600.0
        right_ascension_deg = reference_right_ascension_deg + (
            right_ascension_rate_arcsec_per_hour * elapsed_hours / 3600.0
        )
        declination_deg = reference_declination_deg + (
            declination_rate_arcsec_per_hour * elapsed_hours / 3600.0
        )
        pixel_x, pixel_y = pixel_positions[index]
        detections.append(
            _make_detection(
                f"frame{index}.fits", timestamp, pixel_x, pixel_y, right_ascension_deg, declination_deg
            )
        )

    candidates = detector.detect_candidates("TestTarget", detections)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    assert candidate.track is not None
    assert candidate.track.right_ascension_rate_arcsec_per_hour == pytest.approx(
        right_ascension_rate_arcsec_per_hour, rel=1e-3
    )
    assert candidate.track.declination_rate_arcsec_per_hour == pytest.approx(
        declination_rate_arcsec_per_hour, rel=1e-3
    )
    assert candidate.track.linear_fit_r_squared == pytest.approx(1.0, rel=1e-6)
