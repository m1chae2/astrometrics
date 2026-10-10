"""Tests that the detector chains dots within one observing night only.

Two unrelated stars, seen on nights a month apart, must never join into a
"mover". Before chaining was split by night, the search radius grew to a
whole degree for a gap of weeks, and a star on one night linked to a
different star half a degree away on another night. That pair looked like a
mover creeping at 2.5 arcsec per hour. These tests keep that from coming
back, and check that a real mover inside one night is still found at its
true rate.
"""

import math
from datetime import datetime

import pytest

from astrometricslib.models.moving_object import CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.detection import MovingObjectDetector

_MONTH_APART_NIGHT_ONE = datetime(2026, 8, 10, 22, 0).timestamp()
_MONTH_APART_NIGHT_TWO = datetime(2026, 9, 9, 22, 0).timestamp()
_SECONDS_BETWEEN_FRAMES = 600.0


def _detection(
    name: str, timestamp: float, pixel: tuple[float, float], sky: tuple[float, float]
) -> FrameDetection:
    """Build one fake detection.

    Parameters
    ----------
    name : `str`
        Used to name the frame the detection belongs to.
    timestamp : `float`
        The frame time, as a Unix timestamp.
    pixel : `tuple` [`float`, `float`]
        Pixel position `(x, y)`.
    sky : `tuple` [`float`, `float`]
        Sky position `(RA, Dec)` in degrees.

    Returns
    -------
    detection : `FrameDetection`
        The fake detection.
    """
    return FrameDetection(
        frame_path=name,
        timestamp=timestamp,
        pixel_x=pixel[0],
        pixel_y=pixel[1],
        right_ascension_deg=sky[0],
        declination_deg=sky[1],
    )


def _star_detections(
    prefix: str, first_time: float, sky: tuple[float, float], pixel_origin: tuple[float, float]
) -> list[FrameDetection]:
    """Build three detections of one fixed star, dithered on the sensor.

    Returns
    -------
    detections : `list` [`FrameDetection`]
        Three detections, ten minutes apart, at the same sky position.
    """
    return [
        _detection(
            f"{prefix}_{index}.fits",
            first_time + _SECONDS_BETWEEN_FRAMES * index,
            (pixel_origin[0] + 30.0 * index, pixel_origin[1] + 20.0 * index),
            sky,
        )
        for index in range(3)
    ]


def _two_stars_on_two_nights() -> list[FrameDetection]:
    """Build star A on one night and star B, half a degree east, a month later.

    Returns
    -------
    detections : `list` [`FrameDetection`]
        Six detections. Each night alone shows a motionless star.
    """
    star_a = (150.0, 20.0)
    star_b = (150.0 + 0.5 / math.cos(math.radians(20.0)), 20.0)
    return _star_detections("night1", _MONTH_APART_NIGHT_ONE, star_a, (100.0, 100.0)) + _star_detections(
        "night2", _MONTH_APART_NIGHT_TWO, star_b, (300.0, 250.0)
    )


def test_two_stars_half_a_degree_apart_a_month_apart_make_no_mover() -> None:
    """Different stars on different nights are never chained into a mover."""
    candidates = MovingObjectDetector(MovingObjectConfig()).detect_candidates(
        "TestTarget", _two_stars_on_two_nights()
    )

    confirmed = [
        candidate
        for candidate in candidates
        if candidate.cascade_stage in (CascadeStage.RATE_LINEARITY_CONFIRMED, CascadeStage.EPHEMERIS_MATCHED)
    ]
    assert confirmed == []
    # Each night gives its own three-frame chain: a motionless star.
    assert len(candidates) == 2
    assert {len(candidate.frame_detections) for candidate in candidates} == {3}
    assert {candidate.cascade_stage for candidate in candidates} == {CascadeStage.REJECTED_STATIONARY_SKY}


def test_a_real_mover_is_found_on_one_night_with_its_true_rate() -> None:
    """A mover on night two is found at its true rate."""
    right_ascension_rate_arcsec_per_hour = 20.0
    declination_rate_arcsec_per_hour = 10.0
    pixels = [(100.0, 100.0), (150.0, 160.0), (200.0, 215.0), (250.0, 270.0)]
    mover = [
        _detection(
            f"mover_{index}.fits",
            _MONTH_APART_NIGHT_TWO + _SECONDS_BETWEEN_FRAMES * index,
            pixels[index],
            (
                150.0 + right_ascension_rate_arcsec_per_hour * (index / 6.0) / 3600.0,
                20.0 + declination_rate_arcsec_per_hour * (index / 6.0) / 3600.0,
            ),
        )
        for index in range(4)
    ]
    detections = _star_detections("night1", _MONTH_APART_NIGHT_ONE, (150.0, 20.0), (100.0, 100.0)) + mover
    # With a 2 arcsec error, 11 arcsec of motion in 30 min is a clear move.
    detector = MovingObjectDetector(MovingObjectConfig(astrometric_error_default_arcsec=2.0))

    candidates = detector.detect_candidates("TestTarget", detections)

    confirmed = [c for c in candidates if c.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED]
    assert len(confirmed) == 1
    assert confirmed[0].track is not None
    # The rate is on the tangent plane, so RA is scaled by cos(dec).
    expected_rate = math.hypot(
        right_ascension_rate_arcsec_per_hour * math.cos(math.radians(20.0)), declination_rate_arcsec_per_hour
    )
    assert confirmed[0].track.total_rate_arcsec_per_hour == pytest.approx(expected_rate, rel=0.02)


def test_the_match_radius_stops_growing_at_the_cap_inside_one_night() -> None:
    """Two stars 25 arcmin apart, six hours apart on one night, are not linked.

    The old radius would have reached 300 arcsec per hour times 6 hours,
    which is 30 arcmin. The default cap is 5 arcmin.
    """
    detections = [
        _detection("a0.fits", _MONTH_APART_NIGHT_ONE, (100.0, 100.0), (150.0, 20.0)),
        _detection("a1.fits", _MONTH_APART_NIGHT_ONE + 600.0, (130.0, 120.0), (150.0, 20.0)),
        _detection("a2.fits", _MONTH_APART_NIGHT_ONE + 1200.0, (160.0, 140.0), (150.0, 20.0)),
        # Six hours later on the same observing night, a different star.
        _detection(
            "b0.fits",
            _MONTH_APART_NIGHT_ONE + 6 * 3600.0,
            (300.0, 300.0),
            (150.0 + 25.0 / 60.0 / math.cos(math.radians(20.0)), 20.0),
        ),
    ]

    default_chains = MovingObjectDetector(MovingObjectConfig())._chain_detections_by_persistence(detections)
    wide_chains = MovingObjectDetector(
        MovingObjectConfig(chain_match_radius_max_arcsec=3600.0)
    )._chain_detections_by_persistence(detections)

    assert sorted(len(chain) for chain in default_chains) == [1, 3]
    assert sorted(len(chain) for chain in wide_chains) == [4]


def test_nights_are_named_noon_to_noon_so_one_night_across_midnight_stays_together() -> None:
    """Frames at 23:00 and 01:00 belong to one night and can be chained."""
    before_midnight = datetime(2026, 8, 10, 23, 0).timestamp()
    after_midnight = datetime(2026, 8, 11, 1, 0).timestamp()
    detections = [
        _detection("a.fits", before_midnight, (100.0, 100.0), (150.0, 20.0)),
        _detection("b.fits", after_midnight, (100.0, 100.0), (150.0, 20.0)),
    ]

    chains = MovingObjectDetector(MovingObjectConfig())._chain_detections_by_persistence(detections)

    assert [len(chain) for chain in chains] == [2]
