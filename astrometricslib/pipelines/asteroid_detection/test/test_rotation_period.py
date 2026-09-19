"""Tests for turning a tracked object's brightness into a rotation period.

Builds a synthetic tracked object with a brightness that rises and
falls on a known cycle, corrupts it with a per-picture sky-brightness
change, and checks that the corrected light curve and periodogram
still recover the real cycle -- and that too little data safely
returns nothing instead of crashing.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage, FrameDetection
from astrometricslib.pipelines.asteroid_detection.rotation_period import (
    build_light_curve_from_track,
    find_rotation_period,
)


def _make_candidate(frame_detections: list[FrameDetection]) -> AsteroidDetectionCandidate:
    """Wrap a list of per-picture detections into a minimal candidate.

    Returns
    -------
    candidate : `AsteroidDetectionCandidate`
        A candidate carrying exactly the given detections.
    """
    return AsteroidDetectionCandidate(
        id="test-object",
        target_id="TestTarget",
        frame_detections=frame_detections,
        cascade_stage=CascadeStage.RATE_LINEARITY_CONFIRMED,
    )


def _make_detection(timestamp: float, brightness, picture_brightness_level) -> FrameDetection:  # ruff: ignore[missing-type-function-argument]
    """Build a `FrameDetection` with only the fields this module reads.

    Returns
    -------
    detection : `FrameDetection`
        A detection at a fixed, arbitrary picture position.
    """
    return FrameDetection(
        frame_path=f"frame_{timestamp}.fits",
        timestamp=timestamp,
        pixel_x=100.0,
        pixel_y=100.0,
        right_ascension_deg=150.0,
        declination_deg=0.0,
        brightness=brightness,
        picture_brightness_level=picture_brightness_level,
    )


def test_build_light_curve_skips_pictures_missing_either_brightness_value():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify pictures without both brightness numbers are left out."""
    candidate = _make_candidate([
        _make_detection(0.0, brightness=100.0, picture_brightness_level=50.0),
        _make_detection(60.0, brightness=None, picture_brightness_level=50.0),
        _make_detection(120.0, brightness=100.0, picture_brightness_level=None),
        _make_detection(180.0, brightness=100.0, picture_brightness_level=0.0),
        _make_detection(240.0, brightness=120.0, picture_brightness_level=60.0),
    ])

    light_curve = build_light_curve_from_track(candidate)

    assert len(light_curve.timestamps) == 2
    assert light_curve.fluxes_normalized == [100.0 / 50.0, 120.0 / 60.0]


def test_build_light_curve_cancels_out_a_shared_sky_brightness_change():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a picture-wide brightness swing doesn't survive normalization."""
    # Same true object brightness (100) in both pictures, but the second
    # picture's sky was twice as bright overall (haze, moonlight, etc.).
    candidate = _make_candidate([
        _make_detection(0.0, brightness=100.0, picture_brightness_level=50.0),
        _make_detection(60.0, brightness=200.0, picture_brightness_level=100.0),
    ])

    light_curve = build_light_curve_from_track(candidate)

    assert light_curve.fluxes_normalized[0] == light_curve.fluxes_normalized[1]


def test_find_rotation_period_recovers_a_known_cycle():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a real repeating brightness pattern is found despite noise."""
    true_period_hours = 4.0
    start = datetime(2026, 1, 1)
    rng = np.random.default_rng(0)

    detections = []
    for i in range(60):
        # One picture roughly every 10 minutes, with some jitter --
        # real observing never lands on a perfectly even cadence, and
        # evenly-spaced samples make a clean sine wave alias onto other
        # candidate periods that score identically to the real one.
        timestamp_seconds = i * 600.0 + rng.normal(0.0, 45.0)
        hours_elapsed = timestamp_seconds / 3600.0
        true_brightness = 100.0 + 20.0 * np.sin(2 * np.pi * hours_elapsed / true_period_hours)
        # A different, randomly drifting sky brightness in each picture.
        sky_level = 50.0 * (1.0 + rng.normal(0.0, 0.05))
        detections.append(
            _make_detection(
                (start + timedelta(seconds=timestamp_seconds)).timestamp(),
                brightness=true_brightness * (sky_level / 50.0),
                picture_brightness_level=sky_level,
            )
        )
    candidate = _make_candidate(detections)

    periodogram = find_rotation_period(candidate)

    assert periodogram is not None
    # A generous tolerance -- a periodogram's best-fit period is rarely exact.
    assert periodogram.best_period_days == pytest.approx(true_period_hours / 24.0, rel=0.1)


def test_find_rotation_period_returns_none_with_too_few_pictures():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a handful of pictures isn't enough to attempt a period search."""
    candidate = _make_candidate([
        _make_detection(0.0, brightness=100.0, picture_brightness_level=50.0),
        _make_detection(60.0, brightness=110.0, picture_brightness_level=50.0),
    ])

    assert find_rotation_period(candidate) is None
