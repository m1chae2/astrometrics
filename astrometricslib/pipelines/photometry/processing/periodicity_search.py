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

A point-by-point shuffle removes any slow drift or correlated noise in the
data, so a light curve with drift (clouds, changing airmass) would look more
significant than it is. The shuffle therefore moves blocks of consecutive
measurements, with the block length taken from the light curve's own
autocorrelation (see `correlation_block_length`). For white noise the block
is one measurement, which is the plain shuffle; for red noise the blocks keep
the correlation inside them, so the false-alarm probability is honest about
it. The verdicts are a guard against reading noise as a finding, not a proof
that a pattern is real.

Both searches accept the 1-sigma uncertainty of each measurement
(``flux_errors``). The fit then weights each measurement by one over the square
of its uncertainty, so a noisy measurement counts for less. When the
uncertainties are missing or unusable (not one per measurement, or any
that is not a finite number above zero), the Lomb-Scargle search weights all
points equally, and the box search uses one scatter estimated from the
differences between neighboring measurements. In the noise-only versions each
measurement is shuffled together with its own uncertainty, so a shuffled light
curve has the same set of weights as the real one.

The Lomb-Scargle search samples frequency about ten times per periodogram peak
(a peak is about 1/T wide, where T is the time span). A long span with a short
minimum period can need more than 20,000 samples, which is too slow to repeat
for every shuffle. The search then runs in two stages. A thinned grid finds the
20 strongest peaks, and the full-resolution grid is searched a few thinned
steps either side of each. The shuffled light curves go through the same two
stages, so the noise-only distribution is built the same way as the real
result. The result's ``note`` says when the grid was thinned and gives the
coarse and fine steps in peak widths; `describe_frequency_grid` returns the
same numbers.
"""

import math
from dataclasses import dataclass

import numpy as np
from astropy.timeseries import BoxLeastSquares, LombScargle
from scipy.stats import median_abs_deviation

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

# The autocorrelation (how much each measurement resembles the one `lag`
# steps later) below which the correlation is called gone. 0.2 is a common
# convention and a design choice.
_CORRELATION_GONE_BELOW = 0.2

# How many correlation lengths one block spans. Measured on 60 simulated
# light curves of pure correlated noise (AR(1), three nights of 25
# measurements, 99 noise-only versions), the share called significant at the
# 5% level, which should be about 5%:
#
#   blocks of      1 lag   2 lags   3 lags   4 lags
#   phi = 0.70      28%      7%       2%       2%
#   phi = 0.95      37%     10%       3%       2%
#
# (A plain point shuffle gave 97% and 100%.) One or two lags is too
# permissive; three is slightly conservative, which is the safe side.
_BLOCK_LENGTH_IN_CORRELATION_LENGTHS = 3

# The longest block, as a fraction of the measurements. A block longer than
# a quarter of the data leaves too few blocks to shuffle into new orders.
_LONGEST_BLOCK_FRACTION = 0.25

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
# ratio. A well-behaved grid (like the 1,133-point one a same-night search
# produces) is never touched. The Lomb-Scargle search recovers the period
# resolution lost to thinning with a second, refining stage (see
# `_CANDIDATE_PEAK_COUNT`); the box search still searches the thinned grid
# only.
_MAXIMUM_SEARCH_GRID_POINTS = 20_000

# The Lomb-Scargle grid is sampled this many times per periodogram peak. A
# peak is about 1/T wide in frequency (T is the time span), so the full grid
# steps by 1/(10 T).
_SAMPLES_PER_PEAK = 10

# When the full Lomb-Scargle grid is thinned to `_MAXIMUM_SEARCH_GRID_POINTS`,
# the thinned (coarse) step can be wider than a peak, and a sample can then
# step over the true peak. The search therefore runs in two stages. The
# coarse grid finds the strongest peaks; the full-resolution grid is then
# evaluated within this many coarse steps either side of each of the
# `_CANDIDATE_PEAK_COUNT` strongest ones. Three steps covers the coarse
# sample being off the true peak by up to half a step, with margin.
_CANDIDATE_PEAK_COUNT = 20
_REFINE_HALF_WIDTH_COARSE_STEPS = 3


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


@dataclass(frozen=True)
class GridResolution:
    """How finely the Lomb-Scargle search samples the frequency axis.

    Steps are given in peak widths. A periodogram peak is about 1/T wide in
    frequency, where T is the time span, so a step of 0.1 peak widths samples
    each peak about ten times and a step above 1 can step over a peak.

    Attributes
    ----------
    thinned : `bool`
        True when the full-resolution grid had more than
        `_MAXIMUM_SEARCH_GRID_POINTS` points and the two-stage search was used.
    coarse_step_peak_widths : `float`
        The step of the first-stage grid, in peak widths. Equal to
        `fine_step_peak_widths` when `thinned` is false.
    fine_step_peak_widths : `float`
        The step of the full-resolution grid, in peak widths.
    full_point_count : `int`
        Points in the full-resolution grid.
    coarse_point_count : `int`
        Points in the first-stage grid.
    candidate_peak_count : `int`
        How many first-stage peaks are refined; 0 when `thinned` is false.
    """

    thinned: bool
    coarse_step_peak_widths: float
    fine_step_peak_widths: float
    full_point_count: int
    coarse_point_count: int
    candidate_peak_count: int


@dataclass(frozen=True)
class _FrequencyPlan:
    """The frequency grids of one Lomb-Scargle search.

    Attributes
    ----------
    frequency : `np.ndarray`
        The full-resolution, evenly spaced frequency grid.
    stride : `int`
        Every `stride`-th point of `frequency` forms the coarse grid. It is 1
        when the full grid is small enough to use whole.
    span_days : `float`
        The time span T, in days, used to express steps in peak widths.
    """

    frequency: np.ndarray
    stride: int
    span_days: float

    @property
    def coarse_frequency(self) -> np.ndarray:
        """The first-stage grid (the whole grid when it was not thinned)."""
        return self.frequency[:: self.stride]

    @property
    def resolution(self) -> GridResolution:
        """The grid steps, as a `GridResolution`."""
        fine_step = (float(self.frequency[1]) - float(self.frequency[0])) * self.span_days
        return GridResolution(
            thinned=self.stride > 1,
            coarse_step_peak_widths=fine_step * self.stride,
            fine_step_peak_widths=fine_step,
            full_point_count=int(self.frequency.size),
            coarse_point_count=int(self.coarse_frequency.size),
            candidate_peak_count=_CANDIDATE_PEAK_COUNT if self.stride > 1 else 0,
        )


def _plan_frequency_grid(model: LombScargle, grid: SearchGrid) -> _FrequencyPlan:
    """Build the frequency grids for a Lomb-Scargle search.

    Parameters
    ----------
    model : `astropy.timeseries.LombScargle`
        A model on the measurement times; only the times matter here.
    grid : `SearchGrid`
        The searchable period range.

    Returns
    -------
    plan : `_FrequencyPlan`
        The full-resolution grid and the stride that thins it to at most
        `_MAXIMUM_SEARCH_GRID_POINTS` points. A grid already within the limit
        has a stride of 1 and is used unchanged.
    """
    frequency = model.autofrequency(
        minimum_frequency=1.0 / grid.maximum_period_days,
        maximum_frequency=1.0 / grid.minimum_period_days,
        samples_per_peak=_SAMPLES_PER_PEAK,
    )
    stride = max(1, math.ceil(frequency.size / _MAXIMUM_SEARCH_GRID_POINTS))
    return _FrequencyPlan(frequency=frequency, stride=stride, span_days=grid.span_days)


def describe_frequency_grid(time_days: np.ndarray) -> GridResolution | None:
    """Say how finely a Lomb-Scargle search of these times samples frequency.

    Parameters
    ----------
    time_days : `np.ndarray`
        The measurement times, in days.

    Returns
    -------
    resolution : `GridResolution` or `None`
        The grid steps in peak widths and whether the search is two-stage, or
        `None` when the times are too few or too short to search.
    """
    time_days = np.asarray(time_days, dtype=float)
    grid = build_search_grid(time_days)
    if grid is None:
        return None
    return _plan_frequency_grid(LombScargle(time_days, np.zeros_like(time_days)), grid).resolution


def _strongest_peak_indices(power: np.ndarray, count: int) -> np.ndarray:
    """Find the positions of the strongest separate peaks in a power curve.

    A peak is a point at least as high as both neighbors. Taking peaks, not
    the highest points, keeps one broad peak from using up every slot.

    Parameters
    ----------
    power : `np.ndarray`
        The periodogram power on an evenly spaced grid.
    count : `int`
        The most peaks to return.

    Returns
    -------
    indices : `np.ndarray`
        Positions in `power`, strongest first. At least one (the maximum).
    """
    padded = np.concatenate([[-np.inf], power, [-np.inf]])
    is_peak = (power >= padded[:-2]) & (power >= padded[2:])
    peaks = np.flatnonzero(is_peak)
    if peaks.size == 0:
        return np.array([int(np.argmax(power))])
    return peaks[np.argsort(-power[peaks], kind="stable")][:count]


def _strongest_peak(model: LombScargle, plan: _FrequencyPlan) -> tuple[float, float]:
    """Find the strongest periodogram peak, refining it if thinned.

    Without thinning, this is the highest point of the full grid. With
    thinning, the coarse grid picks the `_CANDIDATE_PEAK_COUNT` strongest
    peaks, and the full-resolution grid is evaluated within
    `_REFINE_HALF_WIDTH_COARSE_STEPS` coarse steps of each. The answer is the
    highest point found in either stage. The noise-only versions of the data
    go through this same function, so their strongest peaks are found the
    same way as the real one.

    Parameters
    ----------
    model : `astropy.timeseries.LombScargle`
        The model whose periodogram is searched.
    plan : `_FrequencyPlan`
        The grids to use.

    Returns
    -------
    frequency, power : `tuple` [`float`, `float`]
        The frequency and power of the strongest point evaluated.
    """
    coarse_frequency = plan.coarse_frequency
    # The grid is evenly spaced (thinning by a whole-number stride keeps it
    # so), which allows the fast method. Without this flag power() falls back
    # to a much slower method, and the fallback would repeat for every
    # noise-only version of the data.
    coarse_power = model.power(coarse_frequency, assume_regular_frequency=True)
    best_coarse = int(np.argmax(coarse_power))
    best_frequency, best_power = float(coarse_frequency[best_coarse]), float(coarse_power[best_coarse])
    if plan.stride == 1:
        return best_frequency, best_power

    count = plan.frequency.size
    reach = _REFINE_HALF_WIDTH_COARSE_STEPS * plan.stride
    wanted = np.zeros(count, dtype=bool)
    for peak in _strongest_peak_indices(coarse_power, _CANDIDATE_PEAK_COUNT):
        centre = int(peak) * plan.stride
        wanted[max(0, centre - reach) : min(count, centre + reach + 1)] = True
    wanted[:: plan.stride] = False  # the coarse stage already evaluated these
    fine_frequency = plan.frequency[wanted]
    if fine_frequency.size:
        fine_power = model.power(fine_frequency)
        best_fine = int(np.argmax(fine_power))
        if float(fine_power[best_fine]) > best_power:
            best_frequency, best_power = float(fine_frequency[best_fine]), float(fine_power[best_fine])
    return best_frequency, best_power


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


def correlation_block_length(flux: np.ndarray) -> int:
    """Choose how many consecutive measurements move together in the null.

    The correlation length is the first lag at which the light curve's
    autocorrelation falls below `_CORRELATION_GONE_BELOW`. The block is
    `_BLOCK_LENGTH_IN_CORRELATION_LENGTHS` of those long. A light curve with
    no correlation beyond lag zero gives 1, the plain point shuffle.

    Parameters
    ----------
    flux : `np.ndarray`
        The brightness, in time order.

    Returns
    -------
    block_length : `int`
        At least 1, and at most `_LONGEST_BLOCK_FRACTION` of the measurements.
    """
    flux = np.asarray(flux, dtype=float)
    count = flux.size
    longest = max(1, int(count * _LONGEST_BLOCK_FRACTION))
    centred = flux - np.mean(flux)
    variance = float(np.dot(centred, centred))
    if count < 8 or variance <= 0.0:
        return 1
    for lag in range(1, longest + 1):
        correlation = float(np.dot(centred[:-lag], centred[lag:])) / variance
        if correlation < _CORRELATION_GONE_BELOW:
            return 1 if lag == 1 else min(longest, _BLOCK_LENGTH_IN_CORRELATION_LENGTHS * lag)
    return longest


def null_flux(flux: np.ndarray, random_generator: np.random.Generator, block_length: int) -> np.ndarray:
    """Make one noise-only version of a light curve by moving blocks.

    The measurements keep their times; the brightness values are cut into
    consecutive blocks of `block_length` (the first and last may be shorter),
    and the blocks are put back in a random order, so the correlation inside
    each block survives. A block length of 1 is a plain shuffle.

    Parameters
    ----------
    flux : `np.ndarray`
        The brightness, in time order.
    random_generator : `numpy.random.Generator`
        The source of randomness.
    block_length : `int`
        How many consecutive measurements move together.

    Returns
    -------
    shuffled : `np.ndarray`
        The same values in a new order.
    """
    if block_length <= 1:
        return random_generator.permutation(flux)
    offset = int(random_generator.integers(0, block_length))
    cut_points = [point for point in range(offset, flux.size, block_length) if point > 0]
    blocks = np.split(flux, cut_points) if cut_points else [flux]
    order = random_generator.permutation(len(blocks))
    return np.concatenate([blocks[index] for index in order])


FALSE_ALARM_FOR_DETECTION = _FALSE_ALARM_FOR_DETECTION


def verdict_from_false_alarm(
    false_alarm: float, cycles: float, events: int | None = None, dip_points: int | None = None
) -> str:
    """Turn a false-alarm probability and repeat counts into a verdict.

    The public name of the rule the searches use, so a later correction of the
    probability can be judged by the same rule.

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
    return _verdict_from(false_alarm, cycles, events, dip_points)


def _usable_flux_errors(flux_errors: np.ndarray | None, size: int) -> np.ndarray | None:
    """Check that per-measurement uncertainties can be used as weights.

    Parameters
    ----------
    flux_errors : `numpy.ndarray` or `None`
        The 1-sigma uncertainty of each measurement, or `None`.
    size : `int`
        The number of measurements.

    Returns
    -------
    errors : `numpy.ndarray` or `None`
        The uncertainties as floats, or `None` when they were not given, are
        not one per measurement, or include a value that is not a finite
        number above zero.
    """
    if flux_errors is None:
        return None
    errors = np.asarray(flux_errors, dtype=float)
    if errors.shape != (size,) or not np.all(np.isfinite(errors)) or np.any(errors <= 0):
        return None
    return errors


def _null_order(count: int, random_generator: np.random.Generator, block_length: int) -> np.ndarray:
    """Make one random re-ordering of the measurements, moving blocks.

    The same ordering is applied to the brightness and to its uncertainty,
    so each measurement keeps its own uncertainty.

    Parameters
    ----------
    count : `int`
        The number of measurements.
    random_generator : `numpy.random.Generator`
        The source of randomness.
    block_length : `int`
        How many consecutive measurements move together.

    Returns
    -------
    order : `numpy.ndarray`
        Indices into the measurements, in the new order.
    """
    return null_flux(np.arange(count), random_generator, block_length)


def _grid_note(resolution: GridResolution) -> str:
    """Say in a sentence how the frequency grid was thinned, if it was.

    Parameters
    ----------
    resolution : `GridResolution`
        The grid steps of the search.

    Returns
    -------
    note : `str`
        An empty string when the grid was not thinned. Otherwise a sentence
        giving the coarse and fine steps in peak widths and the number of
        peaks refined.
    """
    if not resolution.thinned:
        return ""
    return (
        f"The period grid was too large to search whole. The first pass stepped "
        f"{resolution.coarse_step_peak_widths:.2g} peak widths (1/T in frequency) at a time; "
        f"the {resolution.candidate_peak_count} strongest peaks were then searched again at "
        f"{resolution.fine_step_peak_widths:.2g} peak widths."
    )


def lomb_scargle_search(
    time_days: np.ndarray,
    flux: np.ndarray,
    shuffle_count: int | None = None,
    block_length: int | None = None,
    flux_errors: np.ndarray | None = None,
) -> PeriodogramResult:
    """Search a light curve for a smooth repeating cycle.

    Parameters
    ----------
    time_days : `np.ndarray`
        The measurement times, in days.
    flux : `np.ndarray`
        The brightness at each time.
    shuffle_count : `int`, optional
        How many noise-only versions to compare with. By default 300, or 150
        for a long light curve.
    block_length : `int`, optional
        How many consecutive measurements move together in the noise-only
        versions. By default chosen from the light curve's own correlation.
    flux_errors : `np.ndarray`, optional
        The 1-sigma uncertainty of each brightness value, in the same units
        as `flux`. When given and usable (see the module notes), the
        periodogram weights the points by it. By default all points count
        equally.

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

    errors = _usable_flux_errors(flux_errors, flux.size)
    model = LombScargle(time_days, flux, errors)
    plan = _plan_frequency_grid(model, grid)
    best_frequency, best_power = _strongest_peak(model, plan)
    best_period = 1.0 / best_frequency

    # A fixed seed makes repeated searches of the same data agree.
    random_generator = np.random.default_rng(time_days.size)
    shuffles = shuffle_count or (
        _SHUFFLE_COUNT
        if time_days.size * plan.coarse_frequency.size <= _SLOW_SEARCH_WORK_LIMIT
        else _REDUCED_SHUFFLE_COUNT
    )
    block = block_length or correlation_block_length(flux)
    at_least_as_strong = 0
    for _ in range(shuffles):
        order = _null_order(flux.size, random_generator, block)
        shuffled_model = LombScargle(time_days, flux[order], None if errors is None else errors[order])
        # The same two-stage search as the real data, so the noise-only
        # strongest peaks come from the same procedure as the real one.
        _, shuffled_power = _strongest_peak(shuffled_model, plan)
        at_least_as_strong += int(shuffled_power >= best_power)
    false_alarm = (1 + at_least_as_strong) / (1 + shuffles)

    cycles = grid.span_days / best_period
    return PeriodogramResult(
        best_period_days=best_period,
        power=best_power,
        false_alarm_probability=float(false_alarm),
        verdict=_verdict_from(false_alarm, cycles),
        shuffle_count=shuffles,
        null_block_length=block,
        cycles_observed=float(cycles),
        searched_min_period_days=grid.minimum_period_days,
        searched_max_period_days=grid.maximum_period_days,
        note=_grid_note(plan.resolution),
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
    spread = 1.4826 * float(median_abs_deviation(differences, scale=1.0))
    # A difference of two independent measurements has sqrt(2) times the
    # noise of one, so divide it out.
    return max(spread / math.sqrt(2.0), 1e-6)


def box_search(
    time_days: np.ndarray,
    flux: np.ndarray,
    shuffle_count: int | None = None,
    block_length: int | None = None,
    flux_errors: np.ndarray | None = None,
) -> TransitCandidate:
    """Search a light curve for a repeating flat-bottomed dip.

    Parameters
    ----------
    time_days : `np.ndarray`
        The measurement times, in days.
    flux : `np.ndarray`
        The brightness at each time. It is divided by its median first,
        so depths are fractions of the normal brightness.
    shuffle_count : `int`, optional
        How many noise-only versions to compare with. By default 300, or 150
        for a long light curve.
    block_length : `int`, optional
        How many consecutive measurements move together in the noise-only
        versions. By default chosen from the light curve's own correlation.
    flux_errors : `np.ndarray`, optional
        The 1-sigma uncertainty of each brightness value, in the same units
        as `flux`. It is divided by the median of `flux`, like the
        brightness. When given and usable (see the module notes), it is the
        weight of each point. By default one scatter, estimated from the
        differences between neighboring measurements, is used for all
        points.

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

    errors = _usable_flux_errors(flux_errors, flux.size)
    if errors is not None:
        point_errors = errors / np.median(flux)
    else:
        point_errors = np.full_like(normalized, _robust_point_scatter(normalized))
    model = BoxLeastSquares(time_days, normalized, dy=point_errors)
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
    shuffles = shuffle_count or (
        _SHUFFLE_COUNT if time_days.size * periods.size <= _SLOW_SEARCH_WORK_LIMIT else _REDUCED_SHUFFLE_COUNT
    )
    block = block_length or correlation_block_length(normalized)
    at_least_as_strong = 0
    for _ in range(shuffles):
        order = _null_order(normalized.size, random_generator, block)
        shuffled = BoxLeastSquares(time_days, normalized[order], dy=point_errors[order])
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
        shuffle_count=shuffles,
        null_block_length=block,
        note=note,
        searched_min_period_days=minimum_period,
        searched_max_period_days=grid.maximum_period_days,
    )
