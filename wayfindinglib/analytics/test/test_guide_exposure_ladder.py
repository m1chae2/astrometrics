"""Purpose: Unit tests for analysing a ladder of guide frames.

Description: Simulates a guide star with a known brightness, a known seeing
jitter and a slow drift, at several exposure lengths, and checks that the
analysis recovers the position noise, flags saturation, ignores drift, and
picks the shortest exposure that works. The simulation gives the true answer,
so a wrong measurement shows.
"""

import numpy as np
import pytest

from wayfindinglib.analytics.guide_exposure_ladder import (
    MINIMUM_FRAMES,
    analyze_guide_exposure_ladder,
    measure_star,
    position_noise_pixels,
)

_SIZE = 64
"""Frame width and height, in pixels."""

_CEILING = 65532.0
"""The guide camera's saturation value."""

_PLATE_SCALE = 6.39
"""Guide plate scale, in arcseconds per pixel."""


def _frame(
    generator: np.random.Generator,
    exposure: float,
    flux_per_second: float,
    centre: tuple[float, float],
    fwhm: float = 1.6,
    background: float = 100.0,
    read_noise: float = 4.0,
) -> np.ndarray:
    """Draw one guide frame of a single star.

    Returns
    -------
    frame : `numpy.ndarray`
        A Gaussian star of the given brightness on a flat background, with
        read noise and shot noise, clipped at the camera's ceiling.
    """
    rows, columns = np.mgrid[:_SIZE, :_SIZE]
    sigma = fwhm / 2.3548
    profile = np.exp(-((columns - centre[0]) ** 2 + (rows - centre[1]) ** 2) / (2.0 * sigma**2))
    profile /= profile.sum()
    signal = flux_per_second * exposure * profile
    expected = background + signal
    frame = (
        expected
        + generator.normal(0.0, read_noise, expected.shape)
        + generator.normal(0.0, np.sqrt(signal), expected.shape)
    )
    return np.clip(frame, 0.0, _CEILING)


def _series(
    exposure: float,
    flux_per_second: float,
    seed: int,
    count: int = 10,
    seeing_px: float = 0.05,
    drift_px: float = 0.03,
) -> list[np.ndarray]:
    """Draw a series of frames with seeing jitter and a steady drift.

    Returns
    -------
    frames : `list` [`numpy.ndarray`]
        `count` frames of one star.
    """
    generator = np.random.default_rng(seed)
    frames = []
    for index in range(count):
        centre = (
            32.0 + drift_px * index + generator.normal(0.0, seeing_px),
            30.0 - drift_px * index + generator.normal(0.0, seeing_px),
        )
        frames.append(_frame(generator, exposure, flux_per_second, centre))
    return frames


def test_a_star_is_found_and_measured() -> None:
    """Verify position, brightness and background are recovered."""
    frame = _frame(np.random.default_rng(0), 2.0, 50000.0, (20.3, 41.7))

    star = measure_star(frame)

    assert star.x == pytest.approx(20.3, abs=0.1)
    assert star.y == pytest.approx(41.7, abs=0.1)
    assert star.flux == pytest.approx(100000.0, rel=0.1)
    assert star.background == pytest.approx(100.0, abs=2.0)


def test_no_star_is_reported_as_none() -> None:
    """Verify a frame of noise does not give a false star."""
    noise = np.random.default_rng(0).normal(100.0, 4.0, (_SIZE, _SIZE))

    assert measure_star(noise) is None


def test_steady_drift_is_not_position_noise() -> None:
    """Verify the second difference removes a straight-line drift."""
    positions = [(10.0 + 0.2 * index, 5.0 - 0.1 * index) for index in range(10)]

    assert position_noise_pixels(positions) == pytest.approx(0.0, abs=1e-9)


def test_white_noise_in_the_positions_is_recovered() -> None:
    """Verify the noise estimate matches the noise that was put in."""
    generator = np.random.default_rng(3)
    positions = [
        (10.0 + 0.2 * index + generator.normal(0.0, 0.05), 5.0 + generator.normal(0.0, 0.05))
        for index in range(400)
    ]

    assert position_noise_pixels(positions) == pytest.approx(0.05, rel=0.15)


def test_too_few_positions_give_no_noise() -> None:
    """Verify four frames are not enough."""
    assert position_noise_pixels([(0.0, 0.0)] * (MINIMUM_FRAMES - 1)) is None


def test_a_bright_star_needs_only_a_short_exposure() -> None:
    """Verify a star that is bright enough passes at the shortest exposure."""
    ladder = {t: _series(t, 60000.0, seed=int(t * 10)) for t in (0.5, 1.0, 2.0)}

    test = analyze_guide_exposure_ladder(ladder, _PLATE_SCALE, _CEILING, 1.09, 0.10)

    assert test.recommended_exposure_seconds == pytest.approx(0.5)
    assert "already" in test.summary
    assert "not needed" in test.summary


def test_a_faint_star_needs_a_longer_exposure_and_the_test_finds_it() -> None:
    """Verify noisy short exposures are rejected and a good one is chosen."""
    ladder = {t: _series(t, 1500.0, seed=int(t * 10), seeing_px=0.01) for t in (0.25, 0.5, 1.0, 2.0, 4.0)}

    test = analyze_guide_exposure_ladder(ladder, _PLATE_SCALE, _CEILING, 1.09, 0.10)
    by_length = {result.exposure_seconds: result for result in test.results}

    assert by_length[0.25].jitter_arcsec > by_length[4.0].jitter_arcsec
    assert not by_length[0.25].acceptable
    assert test.recommended_exposure_seconds is not None
    assert test.recommended_exposure_seconds > 0.25
    assert "shortest exposure" in test.summary


def test_brightness_grows_with_exposure() -> None:
    """Verify the light per second is about the same at every length."""
    ladder = {t: _series(t, 20000.0, seed=int(t * 10)) for t in (0.5, 1.0, 2.0)}

    test = analyze_guide_exposure_ladder(ladder, _PLATE_SCALE, _CEILING, 1.09, 0.10)

    rates = [result.flux_per_second for result in test.results]
    assert max(rates) / min(rates) < 1.2


def test_a_saturated_star_is_not_acceptable_however_steady() -> None:
    """Verify clipping at the ceiling rejects an exposure."""
    ladder = {0.5: _series(0.5, 8.0e6, seed=1)}

    test = analyze_guide_exposure_ladder(ladder, _PLATE_SCALE, _CEILING, 1.09, 0.10)

    assert test.results[0].saturated
    assert not test.results[0].acceptable
    assert test.recommended_exposure_seconds is None
    assert "saturates" in test.summary


def test_a_star_that_is_too_faint_at_every_length_says_what_to_check() -> None:
    """Verify the summary points at the likely causes when nothing works."""
    ladder = {t: _series(t, 400.0, seed=int(t * 10), seeing_px=0.2) for t in (0.5, 1.0)}

    test = analyze_guide_exposure_ladder(ladder, _PLATE_SCALE, _CEILING, 1.09, 0.10)

    assert test.recommended_exposure_seconds is None
    assert "focus, dew" in test.summary or "No star was found" in test.summary


def test_empty_frames_say_that_no_star_was_found() -> None:
    """Verify frames of pure noise give a clear message."""
    noise = [np.random.default_rng(index).normal(100.0, 4.0, (_SIZE, _SIZE)) for index in range(6)]

    test = analyze_guide_exposure_ladder({1.0: noise}, _PLATE_SCALE, _CEILING, 1.09, 0.10)

    assert test.results[0].frames_measured == 0
    assert "No star was found" in test.summary


def test_the_position_noise_limit_follows_the_blur_tolerance() -> None:
    """Verify the allowed noise is the limit times sqrt((1 + f/2)^2 - 1)."""
    test = analyze_guide_exposure_ladder({}, _PLATE_SCALE, _CEILING, 1.0, 0.10)

    assert test.jitter_limit_arcsec == pytest.approx(np.sqrt(1.05**2 - 1.0))


def test_without_a_guiding_limit_nothing_is_acceptable() -> None:
    """Verify an exposure cannot be called good when there is no limit."""
    ladder = {1.0: _series(1.0, 60000.0, seed=1)}

    test = analyze_guide_exposure_ladder(ladder, _PLATE_SCALE, _CEILING, None, 0.10)

    assert test.jitter_limit_arcsec is None
    assert test.recommended_exposure_seconds is None
