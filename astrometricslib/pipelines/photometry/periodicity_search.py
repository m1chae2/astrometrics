"""Searches a light curve for repeating patterns and says how sure it is.

Two searches are provided. The Lomb-Scargle periodogram looks for a
smooth up-and-down cycle. The box-fitting search (BLS) looks for a
repeating, flat-bottomed dip, the shape a planet passing in front of a
star or one star of an eclipsing pair crossing the other makes.

Either search always has a "best" answer, even for pure noise, so the
answer alone means nothing. Each result therefore carries a verdict built
from three checks:

1. Is the data able to show a repeat at all? At least two full cycles must
   fit in the observed time, and a cycle cannot be shorter than a few
   times the gap between measurements. Otherwise the verdict is
   ``insufficient_data`` and no period is reported.
2. How often does noise beat it? The measurements are shuffled among the
   real observation times many times and searched again with exactly the
   same settings. The false-alarm probability is the fraction of shuffles
   whose best result is at least as strong. Shuffling keeps the real
   sampling (gaps between nights included) and so pays for having tried
   many periods.
3. Were enough repeats seen? A pattern seen fewer than three times is at
   most ``possible``.

Shuffling removes any slow drift in the data, so a light curve with drift
(clouds, changing airmass) can look more significant than it is. The
verdicts are a guard against reading noise as a finding, not a proof that
a pattern is real.
"""

import math
from dataclasses import dataclass

import numpy as np
from astropy.timeseries import BoxLeastSquares, LombScargle

from astrometricslib.models.stellar_source import PeriodogramResult, TransitCandidate

VERDICT_DETECTED = "detected"
VERDICT_POSSIBLE = "possible"
VERDICT_NOT_DETECTED = "not_detected"
VERDICT_INSUFFICIENT_DATA = "insufficient_data"

# A cycle cannot be shorter than this many times the typical gap between
# measurements. Two measurements per cycle is the theoretical limit
# (the Nyquist limit); three leaves a margin for the uneven spacing real
# observing has, where the limit is not exact.
_MINIMUM_PERIOD_CADENCES = 3.0

# At least this many full cycles must fit inside the observed time for a
# period to be searched. One cycle cannot show that anything repeats.
_MINIMUM_CYCLES_IN_SPAN = 2.0

# The largest ratio allowed between the longest and shortest period this
# search will try. Both lomb_scargle_search's frequency grid and
# box_search's internal per-period binning cost scale with this ratio,
# and nothing before this constant bounded it: cadence_days (below) is
# the median gap between *all* measurements, however they were taken,
# so a target combining a fine within-session cadence (a burst of quick
# exposures, a few seconds apart) with a wide cross-session baseline
# (sessions weeks or months apart) can reach a ratio in the hundreds of
# thousands purely from how it happened to be observed, not from
# anything astrophysically meaningful about it. A real production run
# hit a ratio of about 604,000 this way: over 12 million Lomb-Scargle
# frequency samples, and a single box_search period-grid evaluation
# that alone took 7+ minutes -- both per star, before even reaching the
# shuffle loop that repeats the same evaluation 150 times. Capping the
# ratio, by never letting the effective cadence used for grid sizing be
# finer than the span allows, bounds both regardless of how tightly
# spaced any one burst of real measurements happens to be.
_MAXIMUM_PERIOD_RATIO = 2000.0

# Cycles that must have been observed for a result to be called
# "detected" rather than "possible".
_CYCLES_FOR_DETECTION = 3.0

# Fewest repeat events (dips) for "possible", and for "detected". One
# dip is a single event, not a period.
_EVENTS_FOR_POSSIBLE = 2
_EVENTS_FOR_DETECTION = 3

# Fewest measurements inside the dips, summed over all events, for
# "possible" (the same count for "detected" is twice this).
_POINTS_IN_DIP_FOR_POSSIBLE = 3

# False-alarm probability cutoffs for the verdicts. 0.01 means noise
# matches the result in about 1 shuffle in 100.
_FALSE_ALARM_FOR_DETECTION = 0.01
_FALSE_ALARM_FOR_POSSIBLE = 0.05

# How many times the data is shuffled. With 300 shuffles the smallest
# false-alarm probability that can be reported is 1/301, about 0.003,
# which is below the 0.01 cutoff.
_SHUFFLE_COUNT = 300

# Long light curves make each search slow (a 400-measurement dip search
# takes about 16 seconds with 300 shuffles), so fewer shuffles are used
# when (measurements x periods searched) passes this. 150 shuffles still
# reach a false-alarm probability of 1/151, about 0.007, below the 0.01
# cutoff for "detected", and take about half as long.
_SLOW_SEARCH_WORK_LIMIT = 400_000
_REDUCED_SHUFFLE_COUNT = 150

# How many box widths the transit search tries, from the shortest a
# dip can be seen at (two measurements wide) up to the longest allowed
# below.
_DURATION_COUNT = 5

# The longest dip searched, in multiples of the gap between measurements.
# A dip that lasts more than about 12 measurements is a slow dimming
# (clouds, a trend) more than a transit or eclipse, and allowing longer
# dips would also rule out short periods, since a dip must be shorter than
# the period it repeats with. It is also capped at a quarter of the
# longest period searched.
_LONGEST_DIP_CADENCES = 12.0

# The search grid (frequencies for Lomb-Scargle, periods for the box
# search) is otherwise sized from minimum_period_days/maximum_period_days
# alone, with no bound on how wide that range can be. A star combining a
# multi-month observing baseline (a wide maximum_period_days, from
# sessions taken weeks or months apart) with a cadence of a few seconds
# (a tiny minimum_period_days, from rapid burst exposures within one of
# those sessions) can need a period ratio in the hundreds of thousands --
# a real Vega run hit a ratio of about 604,000 and a resulting Lomb-Scargle
# frequency grid of over 12 million points, which made a single star's
# significance test (150 shuffles, one full periodogram evaluation each)
# run for hours instead of the "several seconds per star" this search was
# designed for. Thinning an oversized grid down to this many points keeps
# worst-case runtime bounded regardless of any future target's span/cadence
# ratio, at the cost of coarser period resolution only in that pathological
# case -- a well-behaved grid (like the 1,133-point one a same-night search
# produces) is never touched.
_MAXIMUM_SEARCH_GRID_POINTS = 20_000


@dataclass(frozen=True)
class SearchGrid:
    """What periods a light curve can be searched over.

    Attributes
    ----------
    span_days : `float`
        Time from the first to the last measurement, in days.
    cadence_days : `float`
        The typical gap between measurements, in days. Never smaller
        than `span_days` allows -- see `_MAXIMUM_PERIOD_RATIO` -- so
        this can be larger than the actual measured median gap.
    minimum_period_days : `float`
        The shortest period worth searching.
    maximum_period_days : `float`
        The longest period worth searching (half the span).
    """

    span_days: float
    cadence_days: float
    minimum_period_days: float
    maximum_period_days: float


def build_search_grid(time_days: np.ndarray) -> SearchGrid | None:
    """Work out which periods a set of measurement times can test.

    Parameters
    ----------
    time_days : `np.ndarray`
        The measurement times, in days from any starting point.

    Returns
    -------
    grid : `SearchGrid` or `None`
        The searchable period range, or `None` when the times cover too
        little to search any period (fewer than two full cycles of even
        the shortest allowed period, or no spacing between points).
    """
    ordered = np.sort(np.asarray(time_days, dtype=float))
    gaps = np.diff(ordered)
    gaps = gaps[gaps > 0]
    if gaps.size == 0:
        return None
    cadence = float(np.median(gaps))
    span = float(ordered[-1] - ordered[0])
    maximum_period = span / _MINIMUM_CYCLES_IN_SPAN
    # See _MAXIMUM_PERIOD_RATIO: never let the cadence used for grid
    # sizing be finer than the span allows, so a burst of tightly-spaced
    # measurements within an otherwise widely-spaced dataset cannot
    # blow up the search grid on its own.
    minimum_cadence = maximum_period / (_MAXIMUM_PERIOD_RATIO * _MINIMUM_PERIOD_CADENCES)
    cadence = max(cadence, minimum_cadence)
    minimum_period = _MINIMUM_PERIOD_CADENCES * cadence
    if maximum_period <= minimum_period:
        return None
    return SearchGrid(span, cadence, minimum_period, maximum_period)


def _cap_grid_size(grid_values: np.ndarray, maximum_points: int = _MAXIMUM_SEARCH_GRID_POINTS) -> np.ndarray:
    """Thin a search grid down to a safe maximum size, if it is oversized.

    Keeps the grid's own first and last values and picks evenly-spaced
    indices between them, rather than changing how the grid itself is
    built -- so a well-behaved, already-reasonable grid is returned
    completely unchanged.

    Parameters
    ----------
    grid_values : `np.ndarray`
        The frequency or period grid to thin.
    maximum_points : `int`, optional
        The largest size to allow, by default `_MAXIMUM_SEARCH_GRID_POINTS`.

    Returns
    -------
    thinned : `np.ndarray`
        `grid_values` unchanged if it was already within the limit,
        otherwise an evenly-thinned subset of at most `maximum_points`
        values.
    """
    if grid_values.size <= maximum_points:
        return grid_values
    keep_indices = np.linspace(0, grid_values.size - 1, maximum_points).round().astype(int)
    return grid_values[keep_indices]


def _verdict_from(
    false_alarm: float, cycles: float, events: int | None = None, dip_points: int | None = None
) -> str:
    """Turn a false-alarm probability and repeat counts into a verdict.

    Parameters
    ----------
    false_alarm : `float`
        The false-alarm probability.
    cycles : `float`
        How many full cycles of the period fit in the observed time.
    events : `int`, optional
        For a dip search, how many separate dips were seen.
    dip_points : `int`, optional
        For a dip search, how many measurements fell inside dips.

    Returns
    -------
    verdict : `str`
        `VERDICT_DETECTED`, `VERDICT_POSSIBLE` or `VERDICT_NOT_DETECTED`.
    """
    if false_alarm > _FALSE_ALARM_FOR_POSSIBLE:
        return VERDICT_NOT_DETECTED
    if events is not None:
        if events < _EVENTS_FOR_POSSIBLE or (dip_points or 0) < _POINTS_IN_DIP_FOR_POSSIBLE:
            return VERDICT_NOT_DETECTED
        if (
            false_alarm <= _FALSE_ALARM_FOR_DETECTION
            and events >= _EVENTS_FOR_DETECTION
            and (dip_points or 0) >= 2 * _POINTS_IN_DIP_FOR_POSSIBLE
        ):
            return VERDICT_DETECTED
        return VERDICT_POSSIBLE
    if false_alarm <= _FALSE_ALARM_FOR_DETECTION and cycles >= _CYCLES_FOR_DETECTION:
        return VERDICT_DETECTED
    return VERDICT_POSSIBLE


def _insufficient_note(time_days: np.ndarray) -> str:
    """Explain why a light curve cannot be searched for a period.

    Returns
    -------
    note : `str`
        A sentence saying how long the data spans and what is needed.
    """
    span = float(np.max(time_days) - np.min(time_days))
    span_text = f"{span * 24 * 60:.0f} min" if span < 0.1 else f"{span:.2f} d"
    return (
        f"The measurements span {span_text}, too short to see two full cycles of any period "
        f"longer than {_MINIMUM_PERIOD_CADENCES:.0f} measurements. Observe over a longer time."
    )


def lomb_scargle_search(time_days: np.ndarray, flux: np.ndarray) -> PeriodogramResult:
    """Search a light curve for a smooth repeating cycle.

    Parameters
    ----------
    time_days : `np.ndarray`
        The measurement times, in days.
    flux : `np.ndarray`
        The brightness at each time.

    Returns
    -------
    result : `PeriodogramResult`
        The strongest cycle with its false-alarm probability and verdict,
        or an ``insufficient_data`` result carrying a note and no period.
    """
    time_days = np.asarray(time_days, dtype=float)
    flux = np.asarray(flux, dtype=float)
    grid = build_search_grid(time_days)
    if grid is None:
        return PeriodogramResult(verdict=VERDICT_INSUFFICIENT_DATA, note=_insufficient_note(time_days))

    minimum_frequency = 1.0 / grid.maximum_period_days
    maximum_frequency = 1.0 / grid.minimum_period_days
    model = LombScargle(time_days, flux)
    frequency = _cap_grid_size(
        model.autofrequency(
            minimum_frequency=minimum_frequency, maximum_frequency=maximum_frequency, samples_per_peak=10
        )
    )
    # autofrequency() always returns an evenly-spaced grid (thinning it
    # in _cap_grid_size keeps that even spacing, just coarser), so this
    # is safe to assert. Without it, power() defaults to
    # assume_regular_frequency=False and falls back to a much slower
    # O[N^2] method instead of the O[N log N] fast method -- the same
    # slowdown autopower() avoids by asserting this internally, and
    # this matters even more here since the same fallback applies to
    # each of the (possibly hundreds of) shuffle iterations below.
    power = model.power(frequency, assume_regular_frequency=True)
    best_index = int(np.argmax(power))
    best_period = float(1.0 / frequency[best_index])
    best_power = float(power[best_index])

    # A fixed seed makes repeated searches of the same data agree.
    random_generator = np.random.default_rng(time_days.size)
    shuffles = (
        _SHUFFLE_COUNT
        if time_days.size * frequency.size <= _SLOW_SEARCH_WORK_LIMIT
        else _REDUCED_SHUFFLE_COUNT
    )
    at_least_as_strong = 0
    for _ in range(shuffles):
        shuffled_power = LombScargle(time_days, random_generator.permutation(flux)).power(
            frequency, assume_regular_frequency=True
        )
        at_least_as_strong += int(np.max(shuffled_power) >= best_power)
    false_alarm = (1 + at_least_as_strong) / (1 + shuffles)

    cycles = grid.span_days / best_period
    return PeriodogramResult(
        best_period_days=best_period,
        power=best_power,
        false_alarm_probability=float(false_alarm),
        verdict=_verdict_from(false_alarm, cycles),
        cycles_observed=float(cycles),
        searched_min_period_days=grid.minimum_period_days,
        searched_max_period_days=grid.maximum_period_days,
    )


def _robust_point_scatter(flux: np.ndarray) -> float:
    """Estimate the noise of a light curve from neighboring differences.

    Differences between neighboring measurements cancel any slow change
    in brightness, leaving the measurement noise. A median-based spread
    ignores a few large jumps (such as the dips being searched for).

    Returns
    -------
    scatter : `float`
        The estimated noise of one measurement, never zero.
    """
    differences = np.diff(flux)
    spread = 1.4826 * float(np.median(np.abs(differences - np.median(differences))))
    # A difference of two independent measurements has sqrt(2) times the
    # noise of one, so divide it out.
    return max(spread / math.sqrt(2.0), 1e-6)


def box_search(time_days: np.ndarray, flux: np.ndarray) -> TransitCandidate:
    """Search a light curve for a repeating flat-bottomed dip.

    Parameters
    ----------
    time_days : `np.ndarray`
        The measurement times, in days.
    flux : `np.ndarray`
        The brightness at each time. It is divided by its median first,
        so depths are fractions of the normal brightness.

    Returns
    -------
    candidate : `TransitCandidate`
        The strongest dip pattern with its false-alarm probability, event
        count and verdict, or an ``insufficient_data`` result carrying a
        note and no period.
    """
    time_days = np.asarray(time_days, dtype=float)
    flux = np.asarray(flux, dtype=float)
    normalized = flux / np.median(flux)
    grid = build_search_grid(time_days)
    if grid is None:
        return TransitCandidate(verdict=VERDICT_INSUFFICIENT_DATA, note=_insufficient_note(time_days))

    shortest_dip = 2.0 * grid.cadence_days
    longest_dip = min(_LONGEST_DIP_CADENCES * grid.cadence_days, 0.25 * grid.maximum_period_days)
    durations = np.geomspace(shortest_dip, max(longest_dip, shortest_dip * 1.01), _DURATION_COUNT)
    # A dip must be shorter than the period it repeats with.
    minimum_period = max(grid.minimum_period_days, 1.5 * float(durations.max()))
    if minimum_period >= grid.maximum_period_days:
        return TransitCandidate(verdict=VERDICT_INSUFFICIENT_DATA, note=_insufficient_note(time_days))

    point_scatter = _robust_point_scatter(normalized)
    model = BoxLeastSquares(time_days, normalized, dy=np.full_like(normalized, point_scatter))
    try:
        # BoxLeastSquares.autoperiod's own resolution heuristic scales
        # with the transit duration and observing baseline, and unlike
        # LombScargle.autofrequency it eagerly allocates its full period
        # array up front rather than building it lazily -- for the same
        # wide-span/fine-cadence shape that drove the Lomb-Scargle grid
        # to 12 million points, this tried to allocate a 3.9-trillion
        # element (28 TiB) array and raised MemoryError immediately,
        # before there was any array here to cap. A manually built,
        # already-capped grid sidesteps that allocation entirely.
        periods = model.autoperiod(
            durations, minimum_period=minimum_period, maximum_period=grid.maximum_period_days
        )
    except MemoryError, OverflowError:
        periods = np.geomspace(minimum_period, grid.maximum_period_days, _MAXIMUM_SEARCH_GRID_POINTS)
    periods = _cap_grid_size(periods)
    results = model.power(periods, durations, objective="snr")
    best_index = int(np.argmax(results.power))
    best_period = float(results.period[best_index])
    best_duration = float(results.duration[best_index])
    best_epoch = float(results.transit_time[best_index])
    best_power = float(results.power[best_index])
    best_depth = float(results.depth[best_index])

    statistics = model.compute_stats(best_period, best_duration, best_epoch)
    points_per_event = np.asarray(statistics["per_transit_count"])
    event_count = int(np.sum(points_per_event > 0))
    dip_points = int(np.sum(points_per_event))

    random_generator = np.random.default_rng(time_days.size)
    periods = results.period
    shuffles = (
        _SHUFFLE_COUNT if time_days.size * periods.size <= _SLOW_SEARCH_WORK_LIMIT else _REDUCED_SHUFFLE_COUNT
    )
    at_least_as_strong = 0
    for _ in range(shuffles):
        shuffled = BoxLeastSquares(
            time_days, random_generator.permutation(normalized), dy=np.full_like(normalized, point_scatter)
        )
        at_least_as_strong += int(
            np.max(shuffled.power(periods, durations, objective="snr").power) >= best_power
        )
    false_alarm = (1 + at_least_as_strong) / (1 + shuffles)

    cycles = grid.span_days / best_period
    verdict = (
        _verdict_from(false_alarm, cycles, event_count, dip_points)
        if best_depth > 0
        else VERDICT_NOT_DETECTED
    )
    note = ""
    if event_count < _EVENTS_FOR_POSSIBLE:
        note = "The dip was seen only once, which is a single event, not a repeating pattern."
    return TransitCandidate(
        period_days=best_period,
        transit_depth_mag=best_depth * 1.0857,
        transit_duration_hours=best_duration * 24.0,
        epoch_t0=best_epoch,
        transit_snr=best_power,
        transit_confidence=float(min(max(1.0 - math.exp(-max(best_power, 0.0) / 2.0), 0.0), 1.0)),
        false_alarm_probability=float(false_alarm),
        transit_count=event_count,
        points_in_transit=dip_points,
        verdict=verdict,
        note=note,
        searched_min_period_days=minimum_period,
        searched_max_period_days=grid.maximum_period_days,
    )
