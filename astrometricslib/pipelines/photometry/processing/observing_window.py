"""The window function of a run's observing times.

This module finds its strongest alias peaks.

A light curve is sampled only when the telescope looks at the star. Observing
for a few hours each night puts echoes of every real signal at a spacing of
about one cycle per day: a star that really repeats every 0.9 day also fits
a period of 9 days or 0.47 day about as well. The echoes are not in the star.
They are in the schedule. The **window function** is the power spectrum of
the observing times alone, with every brightness set to the same value. It is
1 at zero frequency, where every point agrees, and has a peak at each
frequency spacing that the schedule cannot tell from no spacing at all.

Two periods ``P`` and ``P'`` whose frequencies ``1/P`` and ``1/P'`` differ by
one of the window's peak frequencies cannot be told apart by this run, however
good the photometry is. This module finds the strongest of those peaks.

`alias_check` in `period_checks.py` uses the same window function to ask
whether one particular period is an alias. This module asks the same question
once for the whole run, before any period is known. Both call the one
function `window_power` (defined in `period_checks.py`, re-exported here), so
the window cannot drift apart. The two keep their own peak finders: this one
lists the strongest peaks, while the alias check tests the peaks near one
period's aliases.

This module measures the schedule. It never says which nights to add; that is
for the observing side (``wayfindinglib``).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from astrometricslib.pipelines.photometry.processing.period_checks import (
    NIGHT_GAP_DAYS,
    split_into_nights,
    window_power,
)

__all__ = [
    "ALIAS_PEAK_COUNT",
    "MAXIMUM_ALIAS_FREQUENCY_PER_DAY",
    "WindowPeak",
    "strongest_window_peaks",
    "window_power",
]

# How many of the strongest window peaks the run stores. Three covers the
# daily echo and its first two neighbors (for example the 1-day, the
# 2-day and the one-month echo of a campaign spread over a month), which is
# the set that decides most period ambiguities. Chosen by judgment. On the
# real runs checked (PHO-18) the first three peaks all had power near 1, so
# three is not shown to be enough.
ALIAS_PEAK_COUNT = 3

# The highest frequency searched for window peaks, in cycles per day. Two
# cycles per day reaches the second harmonic of the daily echo, so periods
# down to 12 hours are covered. A period shorter than the exposure series
# cannot be sampled by these runs at all.
MAXIMUM_ALIAS_FREQUENCY_PER_DAY = 2.0

# Grid points per resolution width (1 / span). The same five samples across a
# peak that the period search uses, so a narrow peak is not stepped over.
_SAMPLES_PER_PEAK = 5

# The grid starts this many resolution widths from zero, to leave the central
# peak at zero frequency out. The central peak is 1 wide in units of 1 / span;
# two widths clear it, as `alias_check` does.
_CENTRAL_PEAK_WIDTHS = 2.0

# A run needs this many nights for the schedule to repeat. One night has no
# daily echo, only the sidelobes of its own length.
MINIMUM_NIGHTS_FOR_ALIASES = 2


@dataclass(frozen=True)
class WindowPeak:
    """One peak of the window function.

    Parameters
    ----------
    frequency_per_day : `float`
        The peak's frequency, in cycles per day.
    power_fraction : `float`
        The window's power at the peak, from 0 to 1. The central peak at
        zero frequency is 1.
    """

    frequency_per_day: float
    power_fraction: float


def strongest_window_peaks(
    time_days: Sequence[float] | np.ndarray,
    peak_count: int = ALIAS_PEAK_COUNT,
    maximum_frequency_per_day: float = MAXIMUM_ALIAS_FREQUENCY_PER_DAY,
) -> tuple[list[WindowPeak], str]:
    """Find the strongest peaks of a run's window function.

    Parameters
    ----------
    time_days : `Sequence` [`float`] or `numpy.ndarray`
        The observing times of the run's frames, in days. Repeated times are
        harmless.
    peak_count : `int`, optional
        How many peaks to return.
    maximum_frequency_per_day : `float`, optional
        The highest frequency searched, in cycles per day.

    Returns
    -------
    peaks : `list` [`WindowPeak`]
        The local maxima of the window, strongest first, at most
        `peak_count` of them. Empty when none could be found.
    reason : `str`
        Why the list is empty or shorter than `peak_count`. Empty when the
        list is full.
    """
    times = np.unique(np.asarray(time_days, dtype=float))
    times = times[np.isfinite(times)]
    if times.size < 3:
        return [], "the run has fewer than three distinct frame times"
    if len(split_into_nights(times, NIGHT_GAP_DAYS)) < MINIMUM_NIGHTS_FOR_ALIASES:
        return [], "the run is a single night, so the schedule has no daily echo to measure"
    span = float(times.max() - times.min())
    resolution = 1.0 / span
    first = _CENTRAL_PEAK_WIDTHS * resolution
    if first >= maximum_frequency_per_day:
        return [], "the run spans too short a time to separate a peak from the central peak"
    step = resolution / _SAMPLES_PER_PEAK
    frequency = np.arange(first, maximum_frequency_per_day, step)
    power = window_power(times, frequency)
    interior = np.arange(1, frequency.size - 1)
    is_peak = (power[interior] >= power[interior - 1]) & (power[interior] > power[interior + 1])
    peak_index = interior[is_peak]
    ordered = peak_index[np.argsort(-power[peak_index])][:peak_count]
    peaks = [WindowPeak(float(frequency[index]), float(power[index])) for index in ordered]
    reason = "" if len(peaks) == peak_count else f"the window has only {len(peaks)} peaks up to the limit"
    return peaks, reason
