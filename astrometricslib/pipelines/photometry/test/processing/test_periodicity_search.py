"""Purpose: Unit tests for the light curve period and dip searches.

Description: Verifies that a real cycle or repeating dip is found and
called detected, that noise is not, that a single dip is not a repeating
pattern, and that data too short to show a repeat says so instead of
reporting a period. The false-positive rate over many random light
curves is checked in validate_spectral_and_period_analysis.py;
these tests pin the behavior on fixed cases. A group of tests covers the
two-stage Lomb-Scargle search used when the frequency grid is too large to
search whole: the long-baseline case that the plain thinned grid got wrong,
the unchanged short case, and the false-alarm rate of noise.
"""

import math
import time as time_module

import numpy as np
import pytest
from astropy.timeseries import LombScargle

from astrometricslib.pipelines.photometry.processing import periodicity_search as search_module
from astrometricslib.pipelines.photometry.processing.periodicity_search import (
    _MAXIMUM_PERIOD_RATIO,
    VERDICT_DETECTED,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_NOT_DETECTED,
    _cap_grid_size,
    _robust_point_scatter,
    box_search,
    build_search_grid,
    describe_frequency_grid,
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


def test_robust_point_scatter_matches_a_hand_computed_scaled_mad() -> None:
    """Pins the scatter value, now via a library MAD, not a hand-rolled one.

    Uses `scipy.stats.median_abs_deviation` for the raw MAD, but keeps this
    file's own `1.4826 *` scale factor and `/ sqrt(2)` correction unchanged,
    so the result must still match the old hand-rolled formula exactly.
    """
    flux = np.array([1.0, 1.02, 0.98, 1.05, 0.90, 1.10, 1.01])

    scatter = _robust_point_scatter(flux)

    differences = np.diff(flux)
    hand_rolled_spread = 1.4826 * float(np.median(np.abs(differences - np.median(differences))))
    expected = max(hand_rolled_spread / math.sqrt(2.0), 1e-6)
    assert scatter == pytest.approx(expected)


def test_the_search_grid_needs_two_full_cycles() -> None:
    """Verify the longest searchable period is half the span."""
    times = np.arange(0.0, 1.0, 0.01)

    grid = build_search_grid(times)

    assert grid is not None
    assert abs(grid.maximum_period_days - 0.495) < 0.01
    assert abs(grid.minimum_period_days - 0.03) < 0.005


def test_data_too_short_for_two_cycles_is_insufficient_and_gives_no_period() -> None:
    """Verify 5 points over 10 minutes report insufficient data."""
    times = np.arange(5) * (2.5 / 1440.0)

    result = lomb_scargle_search(times, np.array([1.0, 1.1, 0.9, 1.05, 0.95]))

    assert result.verdict == VERDICT_INSUFFICIENT_DATA
    assert result.best_period_days == pytest.approx(0.0)
    assert "too short" in result.note


def test_a_real_cycle_is_detected_and_its_period_found() -> None:
    """Verify a 0.25 day sine is detected with a small false alarm."""
    times = _times(150, 0.02)
    rng = np.random.default_rng(1)
    flux = 1.0 + 0.05 * np.sin(2 * np.pi * times / 0.25) + rng.normal(0.0, 0.02, times.size)

    result = lomb_scargle_search(times, flux)

    assert result.verdict == VERDICT_DETECTED
    assert abs(result.best_period_days - 0.25) < 0.01
    assert result.false_alarm_probability < 0.01
    assert result.cycles_observed > 10


def test_noise_is_not_reported_as_a_cycle() -> None:
    """Verify random light curves are almost never called detected."""
    times = _times(100, 0.02)
    detected = 0
    for seed in range(20):
        flux = 1.0 + np.random.default_rng(100 + seed).normal(0.0, 0.02, times.size)
        detected += lomb_scargle_search(times, flux).verdict == VERDICT_DETECTED

    assert detected <= 2  # about 1 in 100 expected


def test_repeating_dips_are_detected() -> None:
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


def test_a_single_dip_is_not_a_repeating_pattern() -> None:
    """Verify one dip is reported with a note, not as a detection."""
    times = np.arange(20) * (15.0 / 1440.0)
    flux = np.ones(20)
    flux[8:11] = 0.985

    candidate = box_search(times, flux)

    assert candidate.verdict != VERDICT_DETECTED
    assert candidate.transit_depth_mag > 0.0


def test_noise_is_not_reported_as_dips() -> None:
    """Verify random light curves are almost never called detected dips."""
    times = _times(200, 0.0125)
    detected = 0
    for seed in range(15):
        flux = 1.0 + np.random.default_rng(200 + seed).normal(0.0, 0.01, times.size)
        detected += box_search(times, flux).verdict == VERDICT_DETECTED

    assert detected <= 2


def test_a_seventeen_minute_light_curve_is_not_called_a_detection() -> None:
    """Verify 31 points over 17 minutes are not called a detection."""
    times = np.sort(np.random.default_rng(3).uniform(0.0, 17.0 / 1440.0, 31))
    flux = 1.0 + np.random.default_rng(4).normal(0.0, 0.05, 31)

    assert lomb_scargle_search(times, flux).verdict != VERDICT_DETECTED
    assert box_search(times, flux).verdict in (VERDICT_NOT_DETECTED, VERDICT_INSUFFICIENT_DATA)


def test_cap_grid_size_leaves_a_grid_within_the_limit_unchanged() -> None:
    """Verify a grid already at or under the cap is returned as-is."""
    grid = np.linspace(0.0, 1.0, 500)

    result = _cap_grid_size(grid, maximum_points=500)

    assert result is grid


def test_cap_grid_size_thins_an_oversized_grid_to_the_limit() -> None:
    """Verify an oversized grid is thinned to the cap, keeping its range."""
    grid = np.linspace(0.0, 1.0, 1_000_000)

    result = _cap_grid_size(grid, maximum_points=100)

    assert result.size == 100
    assert result[0] == pytest.approx(0.0)
    assert result[-1] == pytest.approx(1.0)


def test_a_wide_span_with_fine_cadence_does_not_stall_the_period_search() -> None:
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


LONG_BASELINE_PERIOD_DAYS = 0.21


def _long_baseline_times(
    nights: int = 40, spacing_days: float = 3.0, cadence_minutes: float = 5.0
) -> np.ndarray:
    """Build the times of several six-hour observing runs spread over months.

    Parameters
    ----------
    nights : `int`, optional
        How many nights were observed.
    spacing_days : `float`, optional
        Days from the start of one night to the start of the next.
    cadence_minutes : `float`, optional
        Minutes between measurements within a night.

    Returns
    -------
    times : `np.ndarray`
        The measurement times, in days. With the defaults, 40 nights of 72
        measurements spanning about 117 days.
    """
    one_night = np.arange(0.0, 6.0 / 24.0, cadence_minutes / 1440.0)
    return np.concatenate([night * spacing_days + one_night for night in range(nights)])


def _long_baseline_flux(
    times: np.ndarray, amplitude: float = 0.05, noise: float = 0.01, seed: int = 1
) -> np.ndarray:
    """Build a sinusoid of `LONG_BASELINE_PERIOD_DAYS` plus seeded white noise.

    Returns
    -------
    flux : `np.ndarray`
        The brightness at each time.
    """
    rng = np.random.default_rng(seed)
    cycle = amplitude * np.sin(2 * np.pi * times / LONG_BASELINE_PERIOD_DAYS)
    return 1.0 + cycle + rng.normal(0.0, noise, times.size)


def test_a_120_day_baseline_finds_the_true_period_to_within_one_peak_width() -> None:
    """Verify the two-stage search finds a 0.21 d cycle a thinned grid missed.

    Forty six-hour nights over 117 days, sampled every 5 minutes, need a
    39,981-point frequency grid, which is thinned to 19,991 points (a step of
    0.2 peak widths). Searching only that thinned grid gave a best period of
    0.19636 d: a nightly-schedule alias of the real peak, which the thinned
    sample of the true peak (0.926 power) lost to by a small margin (0.905).
    The two-stage search refines the 20 strongest coarse peaks at 0.1 peak
    widths and finds 0.210006 d, the same peak as the full grid.
    """
    times = _long_baseline_times()
    flux = _long_baseline_flux(times)
    span_days = float(times.max() - times.min())
    peak_width_in_period = LONG_BASELINE_PERIOD_DAYS**2 / span_days

    result = lomb_scargle_search(times, flux, shuffle_count=20)

    assert abs(result.best_period_days - LONG_BASELINE_PERIOD_DAYS) < peak_width_in_period
    assert result.power == pytest.approx(0.9263, abs=1e-3)
    assert result.verdict in (VERDICT_DETECTED, "possible")
    assert "too large to search whole" in result.note


def test_the_search_result_records_that_the_grid_was_thinned_and_how_finely() -> None:
    """Verify the grid description gives both step sizes in peak widths."""
    times = _long_baseline_times()

    resolution = describe_frequency_grid(times)

    assert resolution is not None
    assert resolution.thinned is True
    assert resolution.coarse_step_peak_widths == pytest.approx(0.2)
    assert resolution.fine_step_peak_widths == pytest.approx(0.1)
    assert resolution.coarse_point_count <= search_module._MAXIMUM_SEARCH_GRID_POINTS
    assert resolution.full_point_count > search_module._MAXIMUM_SEARCH_GRID_POINTS
    assert resolution.candidate_peak_count == search_module._CANDIDATE_PEAK_COUNT


def test_a_short_single_night_search_is_unchanged_and_not_thinned() -> None:
    """Verify a one-night search gives the numbers it gave before.

    The expected values were produced by the plain full-grid search before
    the two-stage search existed. The grid here has 607 points, far under the
    limit, so it is used whole and the result carries no thinning note.
    """
    rng = np.random.default_rng(11)
    times = np.sort(rng.uniform(0.0, 0.25, 120))
    flux = 1.0 + 0.04 * np.sin(2 * np.pi * times / 0.07) + rng.normal(0.0, 0.015, times.size)

    result = lomb_scargle_search(times, flux)
    resolution = describe_frequency_grid(times)

    assert result.best_period_days == pytest.approx(0.07013751946677886, rel=1e-9)
    assert result.power == pytest.approx(0.811231728631169, rel=1e-9)
    assert result.false_alarm_probability == pytest.approx(0.0033222591362126247)
    assert result.shuffle_count == 300
    assert result.verdict == VERDICT_DETECTED
    assert result.note == ""
    assert resolution is not None
    assert resolution.thinned is False
    assert resolution.coarse_step_peak_widths == pytest.approx(resolution.fine_step_peak_widths)


def test_the_two_stage_peak_matches_the_full_grid_when_the_grid_is_thinned() -> None:
    """Verify the refined peak equals the full-grid maximum when thinned.

    The cap is lowered to 400 points so a cheap light curve is thinned
    (stride 4). The refined result must be at least as high as the coarse
    maximum and equal to the maximum over the whole grid.
    """
    times = _long_baseline_times(nights=12, spacing_days=7.0, cadence_minutes=10.0)
    flux = _long_baseline_flux(times, noise=0.02, seed=4)
    model = LombScargle(times, flux)
    grid = build_search_grid(times)
    original_cap = search_module._MAXIMUM_SEARCH_GRID_POINTS
    search_module._MAXIMUM_SEARCH_GRID_POINTS = 400
    try:
        plan = search_module._plan_frequency_grid(model, grid)
        frequency, power = search_module._strongest_peak(model, plan)
    finally:
        search_module._MAXIMUM_SEARCH_GRID_POINTS = original_cap

    full_power = model.power(plan.frequency, assume_regular_frequency=True)
    coarse_power = model.power(plan.coarse_frequency, assume_regular_frequency=True)
    assert plan.stride > 1
    assert power >= float(coarse_power.max())
    assert power == pytest.approx(float(full_power.max()), rel=1e-9)
    assert frequency == pytest.approx(float(plan.frequency[np.argmax(full_power)]))


def test_the_noise_only_versions_use_the_same_two_stage_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the real data and every shuffle go through `_strongest_peak`."""
    times = _long_baseline_times(nights=8, spacing_days=2.0, cadence_minutes=15.0)
    flux = _long_baseline_flux(times, seed=6)
    calls: list[int] = []
    original = search_module._strongest_peak

    def counting_peak(model: LombScargle, plan: object) -> tuple[float, float]:
        """Count a call, then run the real two-stage search.

        Returns
        -------
        peak : `tuple` [`float`, `float`]
            The frequency and power the real function found.
        """
        calls.append(1)
        return original(model, plan)

    monkeypatch.setattr(search_module, "_strongest_peak", counting_peak)

    lomb_scargle_search(times, flux, shuffle_count=7)

    assert len(calls) == 1 + 7


def test_noise_on_a_thinned_long_baseline_keeps_its_nominal_false_alarm_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify pure noise is rarely called significant when the grid is thinned.

    The cap is lowered to 1,000 points so a cheap 100-day light curve (10
    nights of 12 measurements) is thinned hard: the coarse step is 1.5 peak
    widths, wider than a peak. For 16 seeded noise-only light curves, the
    false-alarm probability should be spread evenly between 0 and 1, so about
    10% fall at or below 0.1 and the mean is about 0.5. The bounds are loose:
    for 16 draws, six or more at or below 0.1 happens about 1 time in 300. If
    the noise-only versions were searched less carefully than the real data,
    the probabilities would fall toward 0 and this would fail.
    """
    monkeypatch.setattr(search_module, "_MAXIMUM_SEARCH_GRID_POINTS", 1_000)
    times = _long_baseline_times(nights=10, spacing_days=10.0, cadence_minutes=30.0)
    assert describe_frequency_grid(times).coarse_step_peak_widths > 1.0

    false_alarms = [
        lomb_scargle_search(
            times, 1.0 + np.random.default_rng(300 + seed).normal(0.0, 0.01, times.size), shuffle_count=29
        ).false_alarm_probability
        for seed in range(16)
    ]

    assert sum(value <= 0.1 for value in false_alarms) <= 5
    assert float(np.mean(false_alarms)) > 0.3
