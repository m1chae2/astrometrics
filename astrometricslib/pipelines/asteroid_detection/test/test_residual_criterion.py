"""Tests for the straight-line (residual) test on a chain of dots.

The old test used R-squared alone. R-squared is near 1 for any chain with a
large displacement, however far the dots sit from the line. The new test
compares the miss distance, in arcseconds, with the position error of one
picture. These tests build chains whose R-squared is high but whose dots
miss the line by about 20 arcsec, and check that they are rejected.
"""

import numpy as np
import pytest

from astrometricslib.models.moving_object import CascadeStage, FrameDetection
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection.detection import (
    MovingObjectDetector,
    _fit_linear_rate_arcsec_per_hour,
)

_HOUR = 3600.0
_ARCSEC_DEG = 1.0 / 3600.0
# Three frames, an hour and a half apart.
_TIMES = [0.0, 1.5 * _HOUR, 3.0 * _HOUR]
_PIXELS = [(100.0, 100.0), (180.0, 150.0), (260.0, 200.0)]


def _chain(
    right_ascension_offsets_arcsec: list[float],
    declination_offsets_arcsec: list[float],
    astrometric_error_arcsec: float | None,
) -> list[FrameDetection]:
    """Build a three-frame chain from offsets, in arcsec, from (150, 0).

    Returns
    -------
    chain : `list` [`FrameDetection`]
        Three detections, one per frame.
    """
    return [
        FrameDetection(
            frame_path=f"frame{index}.fits",
            timestamp=_TIMES[index],
            pixel_x=_PIXELS[index][0],
            pixel_y=_PIXELS[index][1],
            right_ascension_deg=150.0 + right_ascension_offsets_arcsec[index] * _ARCSEC_DEG,
            declination_deg=declination_offsets_arcsec[index] * _ARCSEC_DEG,
            astrometric_error_arcsec=astrometric_error_arcsec,
        )
        for index in range(3)
    ]


def _bent_offsets(slope_arcsec_per_hour: float, bend_arcsec: float) -> list[float]:
    """Make a straight line with its middle point pushed off by `bend_arcsec`.

    Returns
    -------
    offsets : `list` [`float`]
        Offsets at the three frame times. With the bend, the RMS miss
        from the best line is 0.471 times `bend_arcsec`.
    """
    return [
        slope_arcsec_per_hour * (time / _HOUR) + (bend_arcsec if time == _TIMES[1] else 0.0)
        for time in _TIMES
    ]


def test_a_three_point_chain_with_20_arcsec_residuals_is_rejected_though_r_squared_is_high() -> None:
    """Big displacement, 20 arcsec misses, 5 arcsec error: rejected."""
    bend_for_20_arcsec_rms = 20.0 / np.sqrt(2.0 / 9.0)
    right_ascension = _bent_offsets(150.0, bend_for_20_arcsec_rms)
    declination = _bent_offsets(60.0, 0.0)

    # The old verdict: R-squared on both axes is above 0.98.
    timestamps = np.array(_TIMES)
    _rate, r_squared_ra, rms_ra = _fit_linear_rate_arcsec_per_hour(timestamps, np.array(right_ascension))
    _rate, r_squared_dec, _rms_dec = _fit_linear_rate_arcsec_per_hour(timestamps, np.array(declination))
    assert min(r_squared_ra, r_squared_dec) > 0.98
    assert rms_ra == pytest.approx(20.0, rel=1e-6)

    detector = MovingObjectDetector(MovingObjectConfig())
    candidates = detector.detect_candidates("TestTarget", _chain(right_ascension, declination, 5.0))

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE
    assert candidates[0].track is None
    assert detector.last_residual_summary.chains_tested == 1
    assert detector.last_residual_summary.chains_rejected == 1


def test_the_same_chain_without_the_bend_is_accepted_and_records_both_numbers() -> None:
    """A straight chain passes; R-squared and the RMS are stored."""
    right_ascension = _bent_offsets(100.0, 0.0)
    declination = _bent_offsets(60.0, 0.0)
    detector = MovingObjectDetector(MovingObjectConfig())

    candidates = detector.detect_candidates("TestTarget", _chain(right_ascension, declination, 5.0))

    assert candidates[0].cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    track = candidates[0].track
    assert track is not None
    assert track.linear_fit_r_squared == pytest.approx(1.0)
    assert track.residual_rms_right_ascension_arcsec == pytest.approx(0.0, abs=1e-3)
    assert track.residual_rms_declination_arcsec == pytest.approx(0.0, abs=1e-3)
    assert track.astrometric_error_arcsec == pytest.approx(5.0)
    assert track.residual_limit_arcsec == pytest.approx(10.0)
    assert track.astrometric_error_assumed is False


def test_the_limit_follows_the_position_error_of_the_pictures() -> None:
    """An 8 arcsec RMS passes at 5 arcsec error, fails at 3 arcsec."""
    bend = 8.0 / np.sqrt(2.0 / 9.0)
    right_ascension = _bent_offsets(100.0, bend)
    declination = _bent_offsets(60.0, 0.0)
    detector = MovingObjectDetector(MovingObjectConfig())

    loose = detector.detect_candidates("TestTarget", _chain(right_ascension, declination, 5.0))
    tight = detector.detect_candidates("TestTarget", _chain(right_ascension, declination, 3.0))

    assert loose[0].cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    assert tight[0].cascade_stage == CascadeStage.REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE


def test_a_missing_position_error_uses_the_assumed_value_and_says_so() -> None:
    """A chain with no measured error uses the setting and is flagged."""
    detector = MovingObjectDetector(MovingObjectConfig(astrometric_error_default_arcsec=7.0))

    candidates = detector.detect_candidates(
        "TestTarget", _chain(_bent_offsets(100.0, 0.0), _bent_offsets(60.0, 0.0), None)
    )

    track = candidates[0].track
    assert track is not None
    assert track.astrometric_error_arcsec == pytest.approx(7.0)
    assert track.astrometric_error_assumed is True
    assert detector.last_residual_summary.assumed_error_detections == 3


def test_a_star_that_only_jitters_by_its_position_error_is_not_a_mover() -> None:
    """Scatter of a few arcsec with no trend does not move far enough."""
    times = [0.0, 600.0, 1200.0, 1800.0]
    jitter_arcsec = [0.0, 6.0, -3.0, 4.0]
    pixels = [(100.0, 100.0), (150.0, 160.0), (200.0, 215.0), (250.0, 270.0)]
    chain = [
        FrameDetection(
            frame_path=f"frame{index}.fits",
            timestamp=times[index],
            pixel_x=pixels[index][0],
            pixel_y=pixels[index][1],
            right_ascension_deg=150.0 + jitter_arcsec[index] * _ARCSEC_DEG,
            declination_deg=0.0,
            astrometric_error_arcsec=5.0,
        )
        for index in range(4)
    ]

    candidates = MovingObjectDetector(MovingObjectConfig()).detect_candidates("TestTarget", chain)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE


def _chain_from_sky_offsets(
    times: list[float],
    right_ascension_offsets_arcsec: list[float],
    declination_offsets_arcsec: list[float],
    astrometric_error_arcsec: float,
) -> list[FrameDetection]:
    """Build a chain of any length from offsets, in arcsec, from (150, 0).

    Pixel positions step along the chain so the dots do not look like a
    hot pixel.

    Returns
    -------
    chain : `list` [`FrameDetection`]
        One detection per time.
    """
    return [
        FrameDetection(
            frame_path=f"frame{index}.fits",
            timestamp=times[index],
            pixel_x=100.0 + 10.0 * index,
            pixel_y=100.0 + 5.0 * index,
            right_ascension_deg=150.0 + right_ascension_offsets_arcsec[index] * _ARCSEC_DEG,
            declination_deg=declination_offsets_arcsec[index] * _ARCSEC_DEG,
            astrometric_error_arcsec=astrometric_error_arcsec,
        )
        for index in range(len(times))
    ]


# A star whose measured centre alternates between two points 2.8 pixels
# (5 arcsec at 1.8 arcsec per pixel) apart, then settles on the second.
_POINT_A = (0.0, 0.0)
_POINT_B = (-2.0, -4.6)


def test_a_star_whose_centre_flips_between_two_points_is_rejected_as_non_monotonic() -> None:
    """Positions A, B, A, B, B, B, B, B fit a line but reverse."""
    pattern = [_POINT_A, _POINT_B, _POINT_A, *([_POINT_B] * 5)]
    times = [300.0 * index for index in range(len(pattern))]
    chain = _chain_from_sky_offsets(times, [p[0] for p in pattern], [p[1] for p in pattern], 0.9)
    detector = MovingObjectDetector(MovingObjectConfig())

    candidates = detector.detect_candidates("TestTarget", chain)

    assert len(candidates) == 1
    assert candidates[0].cascade_stage == CascadeStage.REJECTED_NON_MONOTONIC
    assert candidates[0].track is None
    assert detector.last_residual_summary.chains_non_monotonic == 1
    assert detector.last_residual_summary.chains_rejected == 0


def test_a_star_flipping_between_two_points_every_frame_is_rejected() -> None:
    """Positions A, B, A, B, 2.8 px apart, 0.9 arcsec error, never pass."""
    pattern = [_POINT_A, _POINT_B, _POINT_A, _POINT_B]
    times = [600.0 * index for index in range(4)]
    chain = _chain_from_sky_offsets(times, [p[0] for p in pattern], [p[1] for p in pattern], 0.9)

    candidates = MovingObjectDetector(MovingObjectConfig()).detect_candidates("TestTarget", chain)

    # The four dots spread only 2.5 arcsec from their mean, so the earlier
    # stationary-sky test may reject this chain first. Either way, it is not
    # a mover.
    assert candidates[0].cascade_stage not in (
        CascadeStage.RATE_LINEARITY_CONFIRMED,
        CascadeStage.EPHEMERIS_MATCHED,
    )
    assert candidates[0].track is None


def test_a_slow_mover_with_small_steps_and_noise_inside_the_error_is_still_found() -> None:
    """Steps below the error, total 4x the error, small wobble: no reversal.

    The along-track positions are 0, 0.5, 2.0, 2.5, 4.5, 5.5, 7.0 and 8.0
    arcsec. Each step is below the 2 arcsec error and the total, 8 arcsec,
    is four times it. The dip from 2.0 to 1.5 would be a small step back
    but stays under the combined error of the two dots.
    """
    along_track = [0.0, 0.5, 1.5, 2.5, 4.5, 5.5, 7.0, 8.0]
    times = [600.0 * index for index in range(len(along_track))]
    chain = _chain_from_sky_offsets(times, along_track, [0.0] * len(along_track), 2.0)
    # Pull one dot back 1 arcsec, under the 2.8 arcsec combined error.
    chain[3].right_ascension_deg -= 1.0 * _ARCSEC_DEG

    detector = MovingObjectDetector(MovingObjectConfig())
    candidates = detector.detect_candidates("TestTarget", chain)

    assert candidates[0].cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
    assert detector.last_residual_summary.chains_non_monotonic == 0


def test_a_slow_mover_that_steps_back_by_more_than_the_error_is_rejected() -> None:
    """The same slow mover, with one dot pulled back 5 arcsec, reverses."""
    along_track = [0.0, 0.5, 1.5, 2.5, 4.5, 5.5, 7.0, 8.0]
    times = [600.0 * index for index in range(len(along_track))]
    chain = _chain_from_sky_offsets(times, along_track, [0.0] * len(along_track), 2.0)
    chain[3].right_ascension_deg -= 5.0 * _ARCSEC_DEG

    detector = MovingObjectDetector(MovingObjectConfig(residual_rms_max_multiple=5.0))
    candidates = detector.detect_candidates("TestTarget", chain)

    assert candidates[0].cascade_stage == CascadeStage.REJECTED_NON_MONOTONIC
