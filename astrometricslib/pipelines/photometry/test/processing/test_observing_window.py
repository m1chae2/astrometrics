"""Tests for the window function of a run's times and its alias peaks."""

import numpy as np
import pytest
from astropy.timeseries import LombScargle

from astrometricslib.pipelines.photometry.processing.observing_window import (
    ALIAS_PEAK_COUNT,
    strongest_window_peaks,
    window_power,
)
from astrometricslib.pipelines.photometry.processing.period_checks import alias_check


def _nights(night_days: list[float], per_night: int = 40, spacing_minutes: float = 3.0) -> np.ndarray:
    """Build observing times of several short nights.

    Returns
    -------
    time_days : `numpy.ndarray`
        ``per_night`` frames on each night, ``spacing_minutes`` apart.
    """
    within_night = np.arange(per_night) * spacing_minutes / 1440.0
    return (np.asarray(night_days)[:, None] + within_night[None, :]).ravel()


def test_the_window_is_one_at_zero_frequency_and_less_elsewhere() -> None:
    """A constant signal agrees with itself at zero frequency only."""
    times = _nights([0.0, 1.0, 2.0, 4.0])
    power = window_power(times, np.array([1e-6, 0.37, 1.0]))
    assert power[0] == pytest.approx(1.0, abs=1e-3)
    assert power[1] < 0.5


def test_the_window_function_is_the_one_the_alias_check_uses() -> None:
    """Both read the Lomb-Scargle power of a constant, mean not removed."""
    times = _nights([0.0, 1.0, 2.0, 3.0])
    frequency = np.linspace(0.2, 1.8, 50)
    direct = LombScargle(times, np.ones_like(times), fit_mean=False, center_data=False).power(frequency)
    assert np.allclose(window_power(times, frequency), direct)


def test_the_alias_check_calls_the_shared_window_function(monkeypatch: pytest.MonkeyPatch) -> None:
    """`alias_check` reads its window from the one `window_power`."""
    from astrometricslib.pipelines.photometry.processing import period_checks

    calls = []
    original = period_checks.window_power

    def recording(time_days: np.ndarray, frequency_per_day: np.ndarray) -> np.ndarray:
        """Record the call and return the real window.

        Returns
        -------
        power : `numpy.ndarray`
            The real window function.
        """
        calls.append(len(frequency_per_day))
        return original(time_days, frequency_per_day)

    monkeypatch.setattr(period_checks, "window_power", recording)
    times = _nights([0.0, 1.0, 2.0, 3.0])
    period_checks.alias_check(times, np.sin(2.0 * np.pi * times / 0.7), 0.7)
    assert calls
    assert window_power is original


def test_nightly_sampling_puts_the_strongest_peak_at_one_cycle_per_day() -> None:
    """Nightly sampling puts the daily echo near 1 cycle per day."""
    times = _nights([float(day) for day in range(8)])
    peaks, reason = strongest_window_peaks(times)
    assert reason == ""
    assert len(peaks) == ALIAS_PEAK_COUNT
    assert peaks[0].frequency_per_day == pytest.approx(1.0, abs=0.03)
    assert peaks[0].power_fraction > 0.9
    powers = [peak.power_fraction for peak in peaks]
    assert powers == sorted(powers, reverse=True)


def test_a_gap_of_a_few_days_adds_peaks_at_the_gap_frequency() -> None:
    """Nights spaced 5 days apart echo at 0.2 cycles per day."""
    times = _nights([0.0, 5.0, 10.0, 15.0, 20.0])
    peaks, _ = strongest_window_peaks(times, peak_count=6)
    assert any(abs(peak.frequency_per_day - 0.2) < 0.02 for peak in peaks)


def test_a_single_night_has_no_daily_echo() -> None:
    """One night gives no peaks and says why."""
    peaks, reason = strongest_window_peaks(_nights([0.0], per_night=200))
    assert peaks == []
    assert "single night" in reason


def test_too_few_distinct_times_give_no_peaks() -> None:
    """Fewer than three distinct times cannot define a window."""
    peaks, reason = strongest_window_peaks(np.array([0.0, 0.0, 1.0]))
    assert peaks == []
    assert "three" in reason


def test_a_run_with_fewer_peaks_than_asked_says_so() -> None:
    """Asking for more peaks than exist gives those that do, and a reason."""
    times = _nights([0.0, 1.0])
    peaks, reason = strongest_window_peaks(times, peak_count=500)
    assert len(peaks) < 500
    assert "only" in reason


def test_the_alias_check_still_reports_a_nightly_alias() -> None:
    """The alias check still runs on a nightly schedule."""
    rng = np.random.default_rng(3)
    times = _nights([float(day) for day in range(10)], per_night=30)
    period = 0.9
    flux = 1.0 + 0.05 * np.sin(2 * np.pi * times / period) + rng.normal(0.0, 0.002, times.size)
    gate = alias_check(times, flux, period)
    assert gate.status.value in {"failed", "passed"}
