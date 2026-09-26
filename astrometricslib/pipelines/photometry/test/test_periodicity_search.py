"""Purpose: Unit tests for the light curve period and dip searches.

Description: Verifies that a real cycle or repeating dip is found and
called detected, that noise is not, that a single dip is not a repeating
pattern, and that data too short to show a repeat says so instead of
reporting a period. The false-positive rate over many random light
curves is checked in validate_spectral_and_period_analysis.py;
these tests pin the behavior on fixed cases.
"""

import time as time_module

import numpy as np
import pytest

from astrometricslib.pipelines.photometry.periodicity_search import (
    _MAXIMUM_PERIOD_RATIO,
    VERDICT_DETECTED,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_NOT_DETECTED,
    _cap_grid_size,
    box_search,
    build_search_grid,
    lomb_scargle_search,
)


def _times(count: int, cadence_days: float, seed: int = 0) -> np.ndarray:
    """Build slightly uneven measurement times.

    Returns
    -------
    times : `np.ndarray`
        Times in days, one every `cadence_days` on average.
    """
    rng = np.random.default_rng(seed)
    return np.arange(count) * cadence_days + rng.normal(0.0, cadence_days * 0.05, count)


def test_the_search_grid_needs_two_full_cycles():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the longest searchable period is half the span."""
    times = np.arange(0.0, 1.0, 0.01)

    grid = build_search_grid(times)

    assert grid is not None
    assert abs(grid.maximum_period_days - 0.495) < 0.01
    assert abs(grid.minimum_period_days - 0.03) < 0.005


def test_data_too_short_for_two_cycles_is_insufficient_and_gives_no_period():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify 5 points over 10 minutes report insufficient data."""
    times = np.arange(5) * (2.5 / 1440.0)

    result = lomb_scargle_search(times, np.array([1.0, 1.1, 0.9, 1.05, 0.95]))

    assert result.verdict == VERDICT_INSUFFICIENT_DATA
    assert result.best_period_days == pytest.approx(0.0)
    assert "too short" in result.note


def test_a_real_cycle_is_detected_and_its_period_found():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a 0.25 day sine is detected with a small false alarm."""
    times = _times(150, 0.02)
    rng = np.random.default_rng(1)
    flux = 1.0 + 0.05 * np.sin(2 * np.pi * times / 0.25) + rng.normal(0.0, 0.02, times.size)

    result = lomb_scargle_search(times, flux)

    assert result.verdict == VERDICT_DETECTED
    assert abs(result.best_period_days - 0.25) < 0.01
    assert result.false_alarm_probability < 0.01
    assert result.cycles_observed > 10


def test_noise_is_not_reported_as_a_cycle():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify random light curves are almost never called detected."""
    times = _times(100, 0.02)
    detected = 0
    for seed in range(20):
        flux = 1.0 + np.random.default_rng(100 + seed).normal(0.0, 0.02, times.size)
        detected += lomb_scargle_search(times, flux).verdict == VERDICT_DETECTED

    assert detected <= 2  # about 1 in 100 expected


def test_repeating_dips_are_detected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a 5% dip every 0.5 day, seen 5 times, is detected."""
    times = _times(400, 0.0125)
    rng = np.random.default_rng(2)
    flux = 1.0 + rng.normal(0.0, 0.01, times.size)
    flux[(times % 0.5) < 0.04] -= 0.05

    candidate = box_search(times, flux)

    assert candidate.verdict == VERDICT_DETECTED
    assert abs(candidate.period_days - 0.5) < 0.02
    assert candidate.transit_count >= 3
    assert candidate.false_alarm_probability < 0.01
    assert candidate.transit_depth_mag > 0.03


def test_a_single_dip_is_not_a_repeating_pattern():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify one dip is reported with a note, not as a detection."""
    times = np.arange(20) * (15.0 / 1440.0)
    flux = np.ones(20)
    flux[8:11] = 0.985

    candidate = box_search(times, flux)

    assert candidate.verdict != VERDICT_DETECTED
    assert candidate.transit_depth_mag > 0.0


def test_noise_is_not_reported_as_dips():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify random light curves are almost never called detected dips."""
    times = _times(200, 0.0125)
    detected = 0
    for seed in range(15):
        flux = 1.0 + np.random.default_rng(200 + seed).normal(0.0, 0.01, times.size)
        detected += box_search(times, flux).verdict == VERDICT_DETECTED

    assert detected <= 2


def test_a_seventeen_minute_light_curve_is_not_called_a_detection():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify 31 points over 17 minutes are not called a detection."""
    times = np.sort(np.random.default_rng(3).uniform(0.0, 17.0 / 1440.0, 31))
    flux = 1.0 + np.random.default_rng(4).normal(0.0, 0.05, 31)

    assert lomb_scargle_search(times, flux).verdict != VERDICT_DETECTED
    assert box_search(times, flux).verdict in (VERDICT_NOT_DETECTED, VERDICT_INSUFFICIENT_DATA)


def test_cap_grid_size_leaves_a_grid_within_the_limit_unchanged():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a grid already at or under the cap is returned as-is."""
    grid = np.linspace(0.0, 1.0, 500)

    result = _cap_grid_size(grid, maximum_points=500)

    assert result is grid


def test_cap_grid_size_thins_an_oversized_grid_to_the_limit():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an oversized grid is thinned to the cap, keeping its range."""
    grid = np.linspace(0.0, 1.0, 1_000_000)

    result = _cap_grid_size(grid, maximum_points=100)

    assert result.size == 100
    assert result[0] == pytest.approx(0.0)
    assert result[-1] == pytest.approx(1.0)


def test_a_wide_span_with_fine_cadence_does_not_stall_the_period_search():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a pathological span/cadence ratio does not stall a search.

    A real incident: Vega's light curve spanned ~121 days across two
    sessions but had ~3-second cadence within a session -- a raw period
    ratio (maximum_period_days / (3 * measured cadence)) of about
    600,000. That produced an unbounded Lomb-Scargle frequency grid of
    over 12 million points, and a single box_search period-grid
    evaluation alone took 7+ minutes, before even reaching the shuffle
    loop that repeats the same evaluation 150 times. Two tightly-spaced
    bursts of measurements 121 days apart reproduce that same
    wide-span/fine-cadence shape; both searches must still return
    promptly now that build_search_grid bounds the ratio at its source.
    """
    burst_1 = np.linspace(0.0, 0.0005, 50)
    burst_2 = np.linspace(121.0, 121.0005, 50)
    times = np.concatenate([burst_1, burst_2])
    flux = 1.0 + np.random.default_rng(5).normal(0.0, 0.01, times.size)

    raw_cadence_days = float(np.median(np.diff(np.sort(times))))
    span_days = float(times.max() - times.min())
    raw_period_ratio = (span_days / 2.0) / (3.0 * raw_cadence_days)
    assert raw_period_ratio > 100_000, "this scenario should reproduce the real incident's ratio"

    grid = build_search_grid(times)
    assert grid.maximum_period_days / grid.minimum_period_days <= _MAXIMUM_PERIOD_RATIO * 1.0001

    started_at = time_module.monotonic()
    lomb_scargle_search(times, flux)
    box_search(times, flux)
    elapsed_seconds = time_module.monotonic() - started_at

    assert elapsed_seconds < 30.0
