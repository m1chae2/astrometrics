"""Purpose: Rebuild the mount's unguided error from a guiding run.

Description: A guide log records two things for every guide frame: the error
the guide star showed, and the correction the guider then sent, as a signed
pulse. Each pulse moves the mount by its length times the guide rate. Adding up
the pulses sent so far gives the total correction applied, so

    unguided error = measured error - total correction applied so far

is the error the mount would have shown with the guider off, apart from one
unknown constant (where the guider locked the star). That is the error a mount
simulator must copy. This module reconstructs it for one run and fits three
parts of it per axis:

- a repeating error in right ascension (the worm gear's periodic error), as a
  sine of fitted period, amplitude and phase;
- a slow linear drift;
- what is left: random jitter.

The fit is a linear least-squares fit at each trial period, with the best
period found on a grid and then refined. The period, amplitude and phase
uncertainties follow Montgomery and O'Donoghue (1999, Delta Scuti Newsletter
13), with the number of independent samples reduced when the remainder is
correlated from one frame to the next. A dither (the guider shifting its lock
position on purpose) shows up as a step that is not mount motion, so the run
is cut at each large step and each piece gets its own constant.

Which way a pulse moves the measured error is not the same in every log (the
Ekos guider's declination axis is opposite to PHD2's). A working guider sends
pulses against the error, so the sign is read from the run: the correlation
between the measured error and the pulses sent is negative when a positive
pulse raises the error, and positive when it lowers it.

The size of the reconstructed error rests on the guide rate in the log. A
rate that is wrong by some factor scales the reconstructed error by the same
factor, and the log alone cannot show it: with the guider working, the
corrections are by design the opposite of the mount's own error. PHD2 measures
its rate during calibration, so its logs can be trusted; the Ekos guider writes
the rate the mount reports. A repeating error is accepted only when its
amplitude is several times its uncertainty.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize_scalar

from wayfindinglib.analytics.guiding_spectrum import MINIMUM_CYCLES, SEGMENT_BREAK_SECONDS
from wayfindinglib.models.session.unguided_mount_error import (
    AxisErrorTerms,
    PeriodicErrorFit,
    PooledAxisTerms,
    UnguidedMountErrorSummary,
    UnguidedRunError,
)

MINIMUM_PERIOD_SECONDS = 60.0
"""Shortest repeating-error period searched, in seconds.

Faster swings are seeing and gear teeth, not the worm. Design estimate,
register row WAY-D120."""

MAXIMUM_PERIOD_SECONDS = 1500.0
"""Longest repeating-error period searched, in seconds.

Worm periods of amateur equatorial mounts run from about 100 to 1000 seconds
(8 minutes is common); 1500 leaves room for a slow gear. A run also cannot
fit a period longer than its length divided by `MINIMUM_CYCLES`. Design
estimate, register row WAY-D120."""

MINIMUM_FIT_SAMPLES = 60
"""Fewest samples in a run for a repeating-error fit.

The fit has at least five free numbers; 60 samples keep it well determined.
Design estimate, register row WAY-D120."""

MINIMUM_SAMPLES_FOR_TERMS = 10
"""Fewest samples in a run for the drift and random terms. Design estimate,
register row WAY-D120."""

MINIMUM_AMPLITUDE_SIGNIFICANCE = 4.0
"""A repeating error counts only if its amplitude is this many times its
uncertainty. The period is searched on a grid, so a plain 3 sigma would let
noise through about once in a hundred grids. Design estimate, register row
WAY-D120."""

_GRID_OVERSAMPLING = 5
"""Trial frequencies per resolution element 1/T. Five put the grid well below
the width of the periodogram peak."""

_STEP_MAD_MULTIPLE = 6.0
_STEP_MINIMUM_ARCSEC = 1.5
"""A change between two frames is a step (a dither) when it is more than 6
robust standard deviations from the typical change and more than 1.5
arcseconds. The worm's steepest slope for a 10 arcsecond swing in 480 seconds
is 0.2 arcseconds a frame, so the second limit keeps real motion out. Design
estimate, register row WAY-D120."""

_MINIMUM_PIECE_SAMPLES = 8
"""A piece between two steps with fewer samples than this is dropped."""

_MAXIMUM_AUTOCORRELATION = 0.9
"""Cap on the frame-to-frame correlation used to reduce the sample count, so
one smooth run does not make the uncertainties unbounded."""

MINIMUM_PULSES_FOR_SIGN = 10
"""Fewest corrections on an axis for the direction of a pulse to be read from
the run. An axis with fewer was not being guided. Design estimate, register
row WAY-D120."""

MINIMUM_SIGN_CORRELATION = 0.3
"""Smallest size of the correlation between the measured error and the pulses
for the direction to be trusted. A guider that corrects what it measures gives
0.6 to 1.0 on the PHD2 contract recording and the Ekos logs; below this the
pulses do not follow the error (guiding on this axis was off or broken).
Design estimate, register row WAY-D120."""

_NOISE_FLOOR_ARCSEC = 1e-9
"""Smallest residual spread used in a division, for noise-free test data."""

_MAD_TO_SIGMA = 1.4826
"""A median absolute deviation times this is a standard deviation for normal
noise."""


@dataclass(frozen=True)
class GuideRunSeries:
    """One guiding run's samples on the axes, ready to analyze.

    Attributes
    ----------
    run_id : `str`
        The run's id.
    session_id : `str`
        The observing night.
    times : `numpy.ndarray`
        Sample times, in seconds since the Unix epoch, in time order.
    ra_error_arcsec, dec_error_arcsec : `numpy.ndarray`
        The measured guide errors, in arcseconds.
    ra_pulse_ms, dec_pulse_ms : `numpy.ndarray`
        The corrections sent after each frame, signed: east and north
        positive, in milliseconds.
    ra_rate_arcsec_per_second, dec_rate_arcsec_per_second : `float` or `None`
        How far the star moves on the sky per second of pulse, in
        arcseconds. For right ascension it includes the cosine of the
        declination, as the guide log's section rate does.
    """

    run_id: str
    session_id: str
    times: np.ndarray
    ra_error_arcsec: np.ndarray
    dec_error_arcsec: np.ndarray
    ra_pulse_ms: np.ndarray
    dec_pulse_ms: np.ndarray
    ra_rate_arcsec_per_second: float | None
    dec_rate_arcsec_per_second: float | None


def split_stretches(times: np.ndarray) -> list[slice]:
    """Cut sorted times at every pause of `SEGMENT_BREAK_SECONDS` or more.

    The guider sends no corrections we can see during a pause, so the total
    correction cannot be carried across it.

    Parameters
    ----------
    times : `numpy.ndarray`
        Sample times in order, in seconds.

    Returns
    -------
    stretches : `list` [`slice`]
        One slice of the sample arrays for each continuous stretch.
    """
    breaks = np.flatnonzero(np.diff(times) >= SEGMENT_BREAK_SECONDS) + 1
    starts = np.concatenate([[0], breaks]).astype(int)
    ends = np.concatenate([breaks, [times.size]]).astype(int)
    return [slice(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]


def correction_sign(measured_error_arcsec: np.ndarray, pulses_ms: np.ndarray) -> float | None:
    """Read which way a positive pulse moves the measured error.

    Parameters
    ----------
    measured_error_arcsec : `numpy.ndarray`
        The measured error in each frame of a run, in arcseconds.
    pulses_ms : `numpy.ndarray`
        The signed pulse after each frame, in milliseconds.

    Returns
    -------
    sign : `float` or `None`
        +1 when a positive pulse raises the measured error (the pulses oppose
        the error: the correlation is negative), -1 when it lowers it.
        `None` when the axis sent too few pulses to tell, or the pulses do
        not follow the error.
    """
    sent = np.abs(pulses_ms) > 0.0
    if int(sent.sum()) < MINIMUM_PULSES_FOR_SIGN:
        return None
    if float(np.std(measured_error_arcsec[sent])) <= 0.0:
        return None
    correlation = float(np.corrcoef(measured_error_arcsec[sent], pulses_ms[sent])[0, 1])
    if not math.isfinite(correlation) or abs(correlation) < MINIMUM_SIGN_CORRELATION:
        return None
    return 1.0 if correlation < 0.0 else -1.0


def reconstruct_unguided_error(
    measured_error_arcsec: np.ndarray,
    pulses_ms: np.ndarray,
    rate_arcsec_per_second: float,
    sign: float = 1.0,
) -> np.ndarray:
    """Add the guider's corrections back to the measured error.

    The pulse in row k is sent after frame k is measured, so it first acts on
    frame k + 1. A pulse that moves the star one way (east or north is
    positive) raises the next measured error by its length times the rate,
    which a regression on the PHD2 contract recording (real PHD2 guiding the
    INDI simulators) confirms: the slope is 0.9 for right ascension and 1.2
    for declination.

    Parameters
    ----------
    measured_error_arcsec : `numpy.ndarray`
        The guide star's measured error in each frame of ONE continuous
        stretch, in arcseconds.
    pulses_ms : `numpy.ndarray`
        The signed pulse after each frame, in milliseconds.
    rate_arcsec_per_second : `float`
        Sky motion per second of pulse, in arcseconds per second.
    sign : `float`, optional
        +1 when a positive pulse raises the measured error, -1 when it
        lowers it (see `correction_sign`).

    Returns
    -------
    unguided_arcsec : `numpy.ndarray`
        The measured error minus the total correction applied before each
        frame. The constant it is known to is the guider's lock position.
    """
    applied = sign * np.cumsum(pulses_ms / 1000.0 * rate_arcsec_per_second)
    before = np.concatenate([[0.0], applied[:-1]])
    return measured_error_arcsec - before


def _find_pieces(times: np.ndarray, unguided: np.ndarray) -> np.ndarray:
    """Cut a stretch into pieces at its large steps.

    Parameters
    ----------
    times : `numpy.ndarray`
        Sample times of one stretch.
    unguided : `numpy.ndarray`
        The reconstructed error of the same samples.

    Returns
    -------
    piece_index : `numpy.ndarray`
        An integer for each sample; it rises by one after each step.
    """
    changes = np.diff(unguided)
    if changes.size < MINIMUM_SAMPLES_FOR_TERMS:
        return np.zeros(times.size, dtype=int)
    center = float(np.median(changes))
    spread = _MAD_TO_SIGMA * float(np.median(np.abs(changes - center)))
    limit = max(_STEP_MAD_MULTIPLE * spread, _STEP_MINIMUM_ARCSEC)
    steps = np.abs(changes - center) > limit
    return np.concatenate([[0], np.cumsum(steps)]).astype(int)


@dataclass
class _Fit:
    """The outcome of one least-squares fit of a run and axis."""

    coefficients: np.ndarray
    covariance: np.ndarray
    residual: np.ndarray
    degrees_of_freedom: int
    inflation: float
    period_seconds: float | None = None


def _design(relative_times: np.ndarray, piece_index: np.ndarray, frequency_hz: float | None) -> np.ndarray:
    """Build the design matrix of the fit.

    It holds a constant per piece, one shared slope and, if a frequency is
    given, a sine and a cosine of that frequency.

    Returns
    -------
    design : `numpy.ndarray`
        Columns: one indicator per piece, the time, then (if `frequency_hz`)
        the sine and cosine of the period.
    """
    piece_count = int(piece_index.max()) + 1
    columns = [(piece_index == piece).astype(float) for piece in range(piece_count)]
    columns.append(relative_times)
    if frequency_hz is not None:
        angle = 2.0 * math.pi * frequency_hz * relative_times
        columns.extend([np.sin(angle), np.cos(angle)])
    return np.column_stack(columns)


def _least_squares(design: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Solve a least-squares fit.

    Returns
    -------
    coefficients, residual : `numpy.ndarray`
        The fitted numbers and the leftover of each sample.
    """
    coefficients, *_ = np.linalg.lstsq(design, values, rcond=None)
    return coefficients, values - design @ coefficients


def _inflation_from_residual(residual: np.ndarray, piece_index: np.ndarray) -> float:
    """Work out how much correlated leftovers inflate the uncertainties.

    Parameters
    ----------
    residual : `numpy.ndarray`
        What is left after the fit, in sample order.
    piece_index : `numpy.ndarray`
        The piece of each sample; pairs across a step are not compared.

    Returns
    -------
    inflation : `float`
        ``(1 + rho) / (1 - rho)`` for the lag-one correlation rho, kept
        between 0 and `_MAXIMUM_AUTOCORRELATION`; 1 for uncorrelated leftovers.
        Multiply variances by it.
    """
    same_piece = piece_index[1:] == piece_index[:-1]
    first, second = residual[:-1][same_piece], residual[1:][same_piece]
    if first.size < MINIMUM_SAMPLES_FOR_TERMS or float(np.std(first)) <= 0.0 or float(np.std(second)) <= 0.0:
        return 1.0
    rho = float(np.corrcoef(first, second)[0, 1])
    rho = min(max(rho, 0.0), _MAXIMUM_AUTOCORRELATION)
    return (1.0 + rho) / (1.0 - rho)


def _finish_fit(
    design: np.ndarray, values: np.ndarray, piece_index: np.ndarray, period_seconds: float | None
) -> _Fit:
    """Fit once and work out the covariance of the fitted numbers.

    Returns
    -------
    fit : `_Fit`
        The coefficients, their covariance (already scaled by the leftover
        variance and the inflation) and the leftovers.
    """
    coefficients, residual = _least_squares(design, values)
    freedom = max(1, values.size - design.shape[1] - (1 if period_seconds is not None else 0))
    variance = float(residual @ residual) / freedom
    inflation = _inflation_from_residual(residual, piece_index)
    covariance = np.linalg.pinv(design.T @ design) * variance * inflation
    return _Fit(coefficients, covariance, residual, freedom, inflation, period_seconds)


def _search_period(
    relative_times: np.ndarray, piece_index: np.ndarray, values: np.ndarray, duration: float
) -> float | None:
    """Find the period that fits best.

    Parameters
    ----------
    relative_times : `numpy.ndarray`
        Sample times counted from the run start, in seconds.
    piece_index : `numpy.ndarray`
        The piece of each sample.
    values : `numpy.ndarray`
        The reconstructed error.
    duration : `float`
        The length of the run, in seconds.

    Returns
    -------
    period : `float` or `None`
        The best period in seconds, or `None` when the run is too short for
        any allowed period to fit `MINIMUM_CYCLES` times.
    """
    longest = min(MAXIMUM_PERIOD_SECONDS, duration / MINIMUM_CYCLES)
    if longest <= MINIMUM_PERIOD_SECONDS:
        return None
    step = 1.0 / (_GRID_OVERSAMPLING * duration)
    frequencies = np.arange(1.0 / longest, 1.0 / MINIMUM_PERIOD_SECONDS + step, step)

    def residual_sum(frequency: float) -> float:
        """Give the squared leftover at one trial frequency.

        Returns
        -------
        total : `float`
            The sum of the squared leftovers of the fit at `frequency`.
        """
        _, residual = _least_squares(_design(relative_times, piece_index, frequency), values)
        return float(residual @ residual)

    sums = np.array([residual_sum(float(f)) for f in frequencies])
    best = int(np.argmin(sums))
    low = float(frequencies[max(best - 1, 0)])
    high = float(frequencies[min(best + 1, frequencies.size - 1)])
    refined = minimize_scalar(residual_sum, bounds=(low, high), method="bounded")
    return 1.0 / float(refined.x)


def _periodic_error_from_fit(
    fit: _Fit, piece_count: int, duration: float, sample_count: int, reference_epoch: float
) -> PeriodicErrorFit:
    """Turn the sine coefficients of a fit into a `PeriodicErrorFit`.

    Parameters
    ----------
    fit : `_Fit`
        A fit made with a sine.
    piece_count : `int`
        Pieces in the design matrix (the sine columns come after the slope).
    duration : `float`
        The length of the run, in seconds.
    sample_count : `int`
        Samples fitted.
    reference_epoch : `float`
        The run start, in seconds since the Unix epoch.

    Returns
    -------
    periodic_error : `PeriodicErrorFit`
        Amplitude, period and phase with their uncertainties.
    """
    sine_index = piece_count + 1
    sine, cosine = float(fit.coefficients[sine_index]), float(fit.coefficients[sine_index + 1])
    covariance = fit.covariance[sine_index : sine_index + 2, sine_index : sine_index + 2]
    amplitude = math.hypot(sine, cosine)
    safe_amplitude = max(amplitude, _NOISE_FLOOR_ARCSEC)
    amplitude_variance = (
        sine**2 * covariance[0, 0] + cosine**2 * covariance[1, 1] + 2.0 * sine * cosine * covariance[0, 1]
    ) / safe_amplitude**2
    phase_variance = (
        cosine**2 * covariance[0, 0] + sine**2 * covariance[1, 1] - 2.0 * sine * cosine * covariance[0, 1]
    ) / safe_amplitude**4
    period = float(fit.period_seconds or 0.0)
    noise = math.sqrt(float(fit.residual @ fit.residual) / max(1, fit.degrees_of_freedom) * fit.inflation)
    # Montgomery and O'Donoghue (1999) equation 10: the frequency error, from
    # the noise, the amplitude, the length of the run and the sample count.
    frequency_error = math.sqrt(6.0) / math.pi * noise / (safe_amplitude * duration * math.sqrt(sample_count))
    amplitude_uncertainty = math.sqrt(max(amplitude_variance, 0.0))
    return PeriodicErrorFit(
        period_seconds=period,
        period_uncertainty_seconds=period**2 * frequency_error,
        amplitude_arcsec=amplitude,
        amplitude_uncertainty_arcsec=amplitude_uncertainty,
        phase_radians=math.atan2(cosine, sine) % (2.0 * math.pi),
        phase_uncertainty_radians=math.sqrt(max(phase_variance, 0.0)),
        reference_epoch_seconds=reference_epoch,
        significance=amplitude / max(amplitude_uncertainty, _NOISE_FLOOR_ARCSEC),
    )


def _axis_terms(fit: _Fit, piece_count: int, piece_index: np.ndarray) -> AxisErrorTerms:
    """Describe the drift and the leftovers of a fit.

    Returns
    -------
    terms : `AxisErrorTerms`
        Drift per minute with its uncertainty, and the random terms.
    """
    drift = float(fit.coefficients[piece_count]) * 60.0
    drift_uncertainty = math.sqrt(max(float(fit.covariance[piece_count, piece_count]), 0.0)) * 60.0
    residual = fit.residual
    same_piece = piece_index[1:] == piece_index[:-1]
    steps = np.diff(residual)[same_piece]
    return AxisErrorTerms(
        samples=int(residual.size),
        drift_arcsec_per_minute=drift,
        drift_uncertainty_arcsec_per_minute=drift_uncertainty,
        random_rms_arcsec=float(np.sqrt(np.mean(residual**2))),
        random_step_rms_arcsec=float(np.sqrt(np.mean(steps**2))) if steps.size else 0.0,
    )


def _build_axis_series(
    series: GuideRunSeries, axis: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Reconstruct one axis over all the continuous stretches of a run.

    Parameters
    ----------
    series : `GuideRunSeries`
        The run.
    axis : `str`
        ``"ra"`` or ``"dec"``.

    Returns
    -------
    times, unguided, piece_index, notes
        The kept sample times, the reconstructed error, the piece number of
        each sample (pieces are numbered through the whole run, so a pause or a
        step starts a new one), and notes.
    """
    errors = series.ra_error_arcsec if axis == "ra" else series.dec_error_arcsec
    pulses = series.ra_pulse_ms if axis == "ra" else series.dec_pulse_ms
    rate = series.ra_rate_arcsec_per_second if axis == "ra" else series.dec_rate_arcsec_per_second
    empty = np.array([])
    if rate is None or rate <= 0.0:
        return empty, empty, empty.astype(int), [f"The log gives no {axis.upper()} guide rate."]
    sign = correction_sign(errors, pulses)
    notes: list[str] = []
    if sign is None:
        sent = int(np.count_nonzero(pulses))
        if sent >= MINIMUM_PULSES_FOR_SIGN:
            return (
                empty,
                empty,
                empty.astype(int),
                [f"The {axis.upper()} pulses do not follow the error: the axis was not guided well."],
            )
        notes.append(
            f"Fewer than {MINIMUM_PULSES_FOR_SIGN} {axis.upper()} corrections: the axis was not guided."
        )
        sign = 1.0
    kept_times: list[np.ndarray] = []
    kept_values: list[np.ndarray] = []
    kept_pieces: list[np.ndarray] = []
    next_piece = 0
    for window in split_stretches(series.times):
        stretch_times = series.times[window]
        if stretch_times.size < _MINIMUM_PIECE_SAMPLES:
            continue
        values = reconstruct_unguided_error(errors[window], pulses[window], rate, sign)
        local = _find_pieces(stretch_times, values)
        for piece in range(int(local.max()) + 1):
            chosen = local == piece
            if int(chosen.sum()) < _MINIMUM_PIECE_SAMPLES:
                continue
            kept_times.append(stretch_times[chosen])
            kept_values.append(values[chosen])
            kept_pieces.append(np.full(int(chosen.sum()), next_piece, dtype=int))
            next_piece += 1
    if not kept_times:
        return empty, empty, empty.astype(int), [f"No {axis.upper()} stretch is long enough."]
    return (
        np.concatenate(kept_times),
        np.concatenate(kept_values),
        np.concatenate(kept_pieces),
        notes,
    )


def measure_run(series: GuideRunSeries) -> UnguidedRunError:
    """Measure the unguided error of one guiding run.

    Parameters
    ----------
    series : `GuideRunSeries`
        The run's samples and guide rates.

    Returns
    -------
    result : `UnguidedRunError`
        The repeating error in right ascension, and the drift and random
        terms on both axes, with notes on anything that limited the fit.
    """
    notes: list[str] = []
    result = UnguidedRunError(
        run_id=series.run_id,
        session_id=series.session_id,
        started_at=float(series.times[0]) if series.times.size else 0.0,
        duration_seconds=float(series.times[-1] - series.times[0]) if series.times.size else 0.0,
        samples_used=0,
    )
    for axis in ("ra", "dec"):
        times, values, pieces, axis_notes = _build_axis_series(series, axis)
        notes.extend(axis_notes)
        if times.size < MINIMUM_SAMPLES_FOR_TERMS:
            if not axis_notes:
                notes.append(f"Too few {axis.upper()} samples ({times.size}) for any term.")
            continue
        origin = float(times[0])
        relative = times - origin
        duration = float(relative[-1])
        piece_count = int(pieces.max()) + 1
        if axis == "ra":
            result.samples_used = int(times.size)
            plain = _finish_fit(_design(relative, pieces, None), values, pieces, None)
            period = None
            if times.size < MINIMUM_FIT_SAMPLES:
                notes.append(f"Only {times.size} samples: too few to fit a periodic error.")
            else:
                period = _search_period(relative, pieces, values, duration)
                if period is None:
                    notes.append(
                        f"The run lasts {duration:.0f} s, too short to hold {MINIMUM_CYCLES} cycles of "
                        f"a {MINIMUM_PERIOD_SECONDS:.0f} s period: no periodic error fitted."
                    )
            chosen = plain
            if period is not None:
                with_sine = _finish_fit(_design(relative, pieces, 1.0 / period), values, pieces, period)
                fit = _periodic_error_from_fit(with_sine, piece_count, duration, int(times.size), origin)
                result.periodic_error = fit
                result.significant_periodic_error = fit.significance >= MINIMUM_AMPLITUDE_SIGNIFICANCE
                if result.significant_periodic_error:
                    chosen = with_sine
            result.ra = _axis_terms(chosen, piece_count, pieces)
        else:
            result.dec = _axis_terms(
                _finish_fit(_design(relative, pieces, None), values, pieces, None), piece_count, pieces
            )
    result.notes = notes
    return result


def _robust_spread(values: np.ndarray) -> float:
    """Spread of values that a few outliers cannot inflate.

    Parameters
    ----------
    values : `numpy.ndarray`
        The values.

    Returns
    -------
    spread : `float`
        1.4826 times the median absolute deviation. For normally distributed
        values this equals the standard deviation (1.4826 is 1 over the 75th
        percentile of the standard normal curve).
    """
    median = float(np.median(values))
    return float(_MAD_TO_SIGMA * np.median(np.abs(values - median)))


def _robust_pool(
    values: Sequence[float], uncertainties: Sequence[float]
) -> tuple[float, float, float | None]:
    """Pool measurements that each have an uncertainty, resisting outliers.

    Parameters
    ----------
    values, uncertainties : `Sequence` [`float`]
        The runs' values and one standard error of each.

    Returns
    -------
    median, uncertainty, scatter : `tuple`
        The median of the values (a run with a wild fit does not move it,
        which an average would not survive), its standard error (the larger
        of the formal one from the inverse-variance weights and the
        run-to-run robust scatter over the square root of the number of
        runs), and the robust spread of the values (`None` for one run).
    """
    sigma = np.maximum(np.asarray(uncertainties, dtype=float), _NOISE_FLOOR_ARCSEC)
    weights = 1.0 / sigma**2
    median = float(np.median(values))
    formal = 1.0 / math.sqrt(float(weights.sum()))
    if len(values) < 2:
        return median, formal, None
    scatter = _robust_spread(np.asarray(values, dtype=float))
    return median, max(formal, scatter / math.sqrt(len(values))), scatter


def _pool_periodic_error(runs: Sequence[UnguidedRunError]) -> PeriodicErrorFit | None:
    """Pool the significant periodic errors of several runs.

    Returns
    -------
    pooled : `PeriodicErrorFit` or `None`
        The pooled period and amplitude (no phase), or `None` if no run has
        a significant fit.
    """
    fits = [run.periodic_error for run in runs if run.significant_periodic_error and run.periodic_error]
    if not fits:
        return None
    period, period_uncertainty, period_scatter = _robust_pool(
        [f.period_seconds for f in fits], [f.period_uncertainty_seconds for f in fits]
    )
    amplitude, amplitude_uncertainty, amplitude_scatter = _robust_pool(
        [f.amplitude_arcsec for f in fits], [f.amplitude_uncertainty_arcsec for f in fits]
    )
    return PeriodicErrorFit(
        period_seconds=period,
        period_uncertainty_seconds=period_uncertainty,
        amplitude_arcsec=amplitude,
        amplitude_uncertainty_arcsec=amplitude_uncertainty,
        significance=math.sqrt(sum(f.significance**2 for f in fits)),
        runs_used=len(fits),
        period_scatter_seconds=period_scatter,
        amplitude_scatter_arcsec=amplitude_scatter,
    )


def _pool_axis(terms: Sequence[AxisErrorTerms]) -> PooledAxisTerms | None:
    """Pool one axis's terms over runs.

    Returns
    -------
    pooled : `PooledAxisTerms` or `None`
        `None` if no run has terms on the axis.
    """
    if not terms:
        return None
    drifts = np.array([t.drift_arcsec_per_minute for t in terms])
    return PooledAxisTerms(
        runs_used=len(terms),
        samples=sum(t.samples for t in terms),
        drift_median_arcsec_per_minute=float(np.median(drifts)),
        drift_spread_arcsec_per_minute=_robust_spread(drifts),
        random_median_arcsec=float(np.median([t.random_rms_arcsec for t in terms])),
        random_step_median_arcsec=float(np.median([t.random_step_rms_arcsec for t in terms])),
    )


def summarize_runs(runs: Sequence[UnguidedRunError]) -> UnguidedMountErrorSummary:
    """Pool the per-run results.

    Parameters
    ----------
    runs : `Sequence` [`UnguidedRunError`]
        The measured runs.

    Returns
    -------
    summary : `UnguidedMountErrorSummary`
        The pooled repeating error and the pooled terms of each axis.
    """
    return UnguidedMountErrorSummary(
        periodic_error=_pool_periodic_error(runs),
        ra=_pool_axis([run.ra for run in runs if run.ra is not None]),
        dec=_pool_axis([run.dec for run in runs if run.dec is not None]),
        runs_measured=len(runs),
        nights_used=sorted({run.session_id for run in runs if run.ra is not None or run.dec is not None}),
    )
