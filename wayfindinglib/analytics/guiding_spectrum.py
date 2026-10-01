"""Frequency analysis of autoguiding drift, periodic error, and backlash.

Finds the repeating parts of the guide error and the mount's backlash:
- Peak-to-peak size of the repeating error (PE_p-p)
- Worm gear fundamental harmonic periods (e.g. ~480s, ~600s, ~430s)
- Declination backlash reversal response delay

Guide samples come in separate stretches: each guiding run is continuous, and
the runs of one night, or of several nights, are separated by pauses of
minutes to months. A spectrum must never join two stretches into one series,
because the join looks like a step the mount never made and puts a made-up
time scale on the result. So the samples are first split wherever they pause
for 60 seconds or more. Each stretch is analysed on its own with its true
timestamps (a Lomb-Scargle periodogram, which handles samples that are not
evenly spaced), and the stretches are then combined. A stretch counts toward
a period only if it is long enough to hold two full cycles of it, so a short
run cannot make up a long period.

The error measured here is the offset left after the guider's earlier
corrections, so the peaks show the repeating error the guider did not remove.
It is not a measurement of the mount's unguided periodic error.
"""

import math
from typing import Any

import numpy as np
from scipy.signal import find_peaks, lombscargle

from wayfindinglib.models.session.telemetry import (
    GuidingSpectrumAnalysis,
    GuidingSpectrumPeak,
)

SEGMENT_BREAK_SECONDS = 60.0
"""A pause of at least this long between samples ends a continuous stretch.

The guide cycle is 2 to 6 seconds, so a pause of a minute is a stop in guiding,
not a slow cycle.
"""

MINIMUM_SAMPLES = 16
"""Fewest samples in a stretch for its spectrum to be worked out."""

MINIMUM_STRETCH_SECONDS = 10.0
"""Shortest stretch whose spectrum is worked out."""

MINIMUM_CYCLES = 2
"""A stretch counts toward a period only if it holds at least this many cycles.

With fewer, the fit cannot tell the period from a trend.
"""

_MAXIMUM_FREQUENCY_HZ = 0.5
"""Highest frequency looked at (a period of 2 seconds)."""

_FREQUENCY_POINTS = 1000
"""Points on the logarithmic frequency grid.

Periods are 0.66 percent apart, so a 480 second period is placed within
about 3 seconds.
"""

_MAXIMUM_PEAKS = 5
"""Most spectral peaks reported."""

SLOW_DRIFT = "Slow Drift (not a gear period)"
"""The label for a peak slower than any gear in the mount's drive train."""

_LONGEST_MECHANICAL_PERIOD_SECONDS = 650.0
"""The longest period attributed to the mount's gears.

The worm gear turns once in 8 to 10 minutes on mounts of this class, so a
repeating error slower than 650 seconds is wander from polar alignment,
flexure, or the sky, not a gear.
"""


def _split_into_stretches(
    times: np.ndarray, dra: np.ndarray, pulse_dec: np.ndarray
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Split sorted samples at every pause of `SEGMENT_BREAK_SECONDS` or more.

    Returns
    -------
    stretches : `list` [`tuple`]
        The times, RA errors and Dec pulses (three arrays) of each continuous
        stretch.
    """
    breaks = np.flatnonzero(np.diff(times) >= SEGMENT_BREAK_SECONDS) + 1
    starts = np.concatenate([[0], breaks])
    ends = np.concatenate([breaks, [times.size]])
    return [(times[a:b], dra[a:b], pulse_dec[a:b]) for a, b in zip(starts, ends, strict=True)]


def _backlash_accumulations(pulse_dec: np.ndarray) -> list[float]:
    """Add up the Dec pulse length between reversals in one stretch.

    Returns
    -------
    accumulations : `list` [`float`]
        The total pulse length before each reversal, in milliseconds.
    """
    accumulations: list[float] = []
    current = 0.0
    last_sign = 0
    for pulse in pulse_dec:
        sign = 1 if pulse > 0 else (-1 if pulse < 0 else 0)
        if sign == 0:
            continue
        if last_sign != 0 and sign != last_sign:
            if current > 0:
                accumulations.append(current)
            current = abs(float(pulse))
        else:
            current += abs(float(pulse))
        last_sign = sign
    return accumulations


def analyze_guiding_telemetry(
    samples: list[dict[str, Any]],
) -> GuidingSpectrumAnalysis:
    """Perform harmonic analysis and backlash estimation on guide samples.

    Parameters
    ----------
    samples : `list` [`dict` [`str`, `Any`]]
        Guiding telemetry samples containing `timestamp`, `dra`, `ddec`,
        `pulse_ra`, and `pulse_dec`. They may come from several runs and
        several nights, in any order.

    Returns
    -------
    analysis : `GuidingSpectrumAnalysis`
        Computed repeating-error metrics, dominant periods and PSD.
        `duration_seconds` is the time spent guiding in the stretches used,
        not the span from the first sample to the last.
    """
    valid_samples: list[dict[str, Any]] = []
    for s in samples:
        t = s.get("timestamp", s.get("time"))
        dra = s.get("dra")
        ddec = s.get("ddec")
        if t is not None and dra is not None and ddec is not None:
            if math.isfinite(t) and math.isfinite(dra) and math.isfinite(ddec):
                valid_samples.append({
                    "time": float(t),
                    "dra": float(dra),
                    "pulse_dec": float(s.get("pulse_dec", s.get("pulseDec", 0.0))),
                })

    n = len(valid_samples)
    if n < MINIMUM_SAMPLES:
        return GuidingSpectrumAnalysis(
            sample_count=n,
            duration_seconds=0.0,
            periodic_error_peak_to_peak_arcsec=0.0,
            message=f"Need at least {MINIMUM_SAMPLES} guiding samples for frequency analysis; found {n}.",
        )

    valid_samples.sort(key=lambda x: x["time"])
    times = np.array([s["time"] for s in valid_samples])
    dra_values = np.array([s["dra"] for s in valid_samples])
    pulses = np.array([s["pulse_dec"] for s in valid_samples])

    stretches = [
        stretch
        for stretch in _split_into_stretches(times, dra_values, pulses)
        if stretch[0].size >= MINIMUM_SAMPLES and stretch[0][-1] - stretch[0][0] > MINIMUM_STRETCH_SECONDS
    ]
    if not stretches:
        return GuidingSpectrumAnalysis(
            sample_count=n,
            duration_seconds=0.0,
            periodic_error_peak_to_peak_arcsec=0.0,
            message=(
                f"No continuous stretch of guiding has {MINIMUM_SAMPLES} samples and lasts more than "
                f"{MINIMUM_STRETCH_SECONDS:g} seconds, so no frequency analysis is possible."
            ),
        )

    durations = np.array([stretch[0][-1] - stretch[0][0] for stretch in stretches])
    counts = np.array([stretch[0].size for stretch in stretches])
    guided_seconds = float(durations.sum())
    cadence = float(np.median(np.concatenate([np.diff(stretch[0]) for stretch in stretches])))
    f_max = min(_MAXIMUM_FREQUENCY_HZ, 0.5 / max(cadence, 1e-3))
    f_min = MINIMUM_CYCLES / float(durations.max())
    if f_min >= f_max:
        f_min = f_max / 10.0
    frequencies = np.geomspace(f_min, f_max, _FREQUENCY_POINTS)
    angular = 2.0 * np.pi * frequencies

    squared_amplitude = np.zeros_like(frequencies)
    weight = np.zeros_like(frequencies)
    peak_to_peak_values = []
    for (stretch_times, stretch_dra, _), duration, count in zip(stretches, durations, counts, strict=True):
        relative_time = stretch_times - stretch_times[0]
        trend = np.polyval(np.polyfit(relative_time, stretch_dra, 1), relative_time)
        residual = stretch_dra - trend
        peak_to_peak_values.append(float(np.percentile(residual, 97.5) - np.percentile(residual, 2.5)))
        power = lombscargle(relative_time, residual - residual.mean(), angular, normalize=False)
        amplitude_squared = 4.0 * power / count
        long_enough = frequencies * duration >= MINIMUM_CYCLES
        squared_amplitude[long_enough] += count * amplitude_squared[long_enough]
        weight[long_enough] += count
    covered = weight > 0
    combined = np.zeros_like(frequencies)
    combined[covered] = squared_amplitude[covered] / weight[covered]
    pe_p_to_p = max(0.0, float(np.average(peak_to_peak_values, weights=durations)))

    peaks: list[GuidingSpectrumPeak] = []
    dominant_period: float | None = None
    if combined.max() > 0:
        maximum = float(combined.max())
        located, _ = find_peaks(combined)
        ranked = sorted(located, key=lambda index: combined[index], reverse=True)[:_MAXIMUM_PEAKS]
        for index in ranked:
            period = round(1.0 / float(frequencies[index]), 1)
            if 420.0 <= period <= 650.0:
                source = "Worm Fundamental Period"
            elif 200.0 <= period <= 300.0:
                source = "2nd Worm Harmonic"
            elif 100.0 <= period <= 160.0:
                source = "Gear Transfer / Pulley"
            elif period < 60.0:
                source = "Seeing / Atmospheric Scintillation"
            elif period > _LONGEST_MECHANICAL_PERIOD_SECONDS:
                source = SLOW_DRIFT
            else:
                source = "Worm Harmonic"
            peaks.append(
                GuidingSpectrumPeak(
                    period_seconds=period,
                    amplitude_arcsec=round(float(np.sqrt(combined[index])), 2),
                    power=round(float(combined[index]) / maximum, 3),
                    probable_source=source,
                )
            )
        # A slow drift is real wander in the error, but it is not a period of
        # the mount's gears, so it is never the dominant period that the
        # correction code phase-locks to.
        dominant_period = next(
            (peak.period_seconds for peak in peaks if peak.probable_source != SLOW_DRIFT), None
        )

    psd_curve: list[dict[str, float]] = []
    if combined.max() > 0:
        step = max(1, int(covered.sum()) // 40)
        covered_indices = np.flatnonzero(covered)[::step]
        psd_curve = [
            {
                "periodSeconds": round(1.0 / float(frequencies[index]), 1),
                "power": round(float(combined[index]) / float(combined.max()), 3),
            }
            for index in covered_indices
        ]

    accumulations = [
        accumulation for stretch in stretches for accumulation in _backlash_accumulations(stretch[2])
    ]
    dec_backlash_ms = round(float(np.median(accumulations)), 1) if accumulations else None

    return GuidingSpectrumAnalysis(
        sample_count=n,
        duration_seconds=round(guided_seconds, 1),
        periodic_error_peak_to_peak_arcsec=round(pe_p_to_p, 2),
        dominant_period_seconds=dominant_period,
        dec_backlash_estimate_ms=dec_backlash_ms,
        peaks=peaks,
        psd_curve=psd_curve,
        message=(
            f"Analyzed {n} samples in {len(stretches)} continuous stretch(es), "
            f"{round(guided_seconds / 60.0, 1)}m of guiding: "
            f'PE={round(pe_p_to_p, 2)}", Backlash={dec_backlash_ms or "N/A"}ms.'
        ),
    )
