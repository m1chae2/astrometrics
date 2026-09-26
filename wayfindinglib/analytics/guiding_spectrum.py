"""Frequency analysis of autoguiding drift, periodic error, and backlash.

Uses Fast Fourier Transform (FFT) and Periodogram power spectral density
to extract:
- Peak-to-peak Periodic Error (PE_p-p)
- Worm gear fundamental harmonic periods (e.g. ~480s, ~600s, ~430s)
- Declination backlash reversal response delay
"""

import math
from typing import Any

import numpy as np

from wayfindinglib.models.session.telemetry import (
    GuidingSpectrumAnalysis,
    GuidingSpectrumPeak,
)


def analyze_guiding_telemetry(
    samples: list[dict[str, Any]],
) -> GuidingSpectrumAnalysis:
    """Perform FFT harmonic analysis and backlash estimation on guide samples.

    Parameters
    ----------
    samples : `list` [`dict` [`str`, `Any`]]
        Chronological guiding telemetry samples containing `timestamp`,
        `dra`, `ddec`, `pulse_ra`, and `pulse_dec`.

    Returns
    -------
    analysis : `GuidingSpectrumAnalysis`
        Computed periodic error metrics, dominant frequencies, and PSD.
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
                    "ddec": float(ddec),
                    "pulse_dec": float(s.get("pulse_dec", s.get("pulseDec", 0.0))),
                })

    n = len(valid_samples)
    if n < 16:
        return GuidingSpectrumAnalysis(
            sample_count=n,
            duration_seconds=0.0,
            periodic_error_peak_to_peak_arcsec=0.0,
            message=f"Need at least 16 guiding samples for frequency analysis; found {n}.",
        )

    # Sort chronologically
    valid_samples.sort(key=lambda x: x["time"])
    times = np.array([s["time"] for s in valid_samples])
    dra = np.array([s["dra"] for s in valid_samples])
    ddec = np.array([s["ddec"] for s in valid_samples])
    pulse_dec = np.array([s["pulse_dec"] for s in valid_samples])

    duration = float(times[-1] - times[0])
    if duration <= 10.0:
        return GuidingSpectrumAnalysis(
            sample_count=n,
            duration_seconds=duration,
            periodic_error_peak_to_peak_arcsec=0.0,
            message="Guiding sample duration too short for periodic error analysis.",
        )

    # Resample onto uniform time grid for FFT
    dt = max(duration / (n - 1), 0.5)
    uniform_times = np.linspace(times[0], times[-1], n)
    uniform_dra = np.interp(uniform_times, times, dra)

    # Detrend linear drift
    poly = np.polyfit(uniform_times - uniform_times[0], uniform_dra, 1)
    detrended_dra = uniform_dra - np.polyval(poly, uniform_times - uniform_times[0])

    # Peak-to-peak periodic error estimate (95th percentile spread)
    p97_5 = float(np.percentile(detrended_dra, 97.5))
    p2_5 = float(np.percentile(detrended_dra, 2.5))
    pe_p_to_p = max(0.0, p97_5 - p2_5)

    # FFT Power Spectral Density
    window = np.hanning(n)
    fft_vals = np.fft.rfft(detrended_dra * window)
    freqs = np.fft.rfftfreq(n, d=dt)

    power = np.abs(fft_vals) ** 2
    # Ignore DC component (0 Hz) and ultra-long frequencies > duration / 2
    valid_mask = (freqs > 1.0 / duration) & (freqs < 0.5)
    f_sub = freqs[valid_mask]
    p_sub = power[valid_mask]

    peaks: list[GuidingSpectrumPeak] = []
    dominant_period: float | None = None

    if len(f_sub) > 0 and np.max(p_sub) > 0:
        # Sort spectral peaks by power
        peak_indices = np.argsort(p_sub)[::-1][:5]
        max_p = float(np.max(p_sub))

        for idx in peak_indices:
            peak_f = float(f_sub[idx])
            peak_p = float(p_sub[idx])
            if peak_f > 0:
                period_sec = round(1.0 / peak_f, 1)
                # Calibrate amplitude to arcsec
                amp = round(float(np.sqrt(peak_p) * 2.0 / n), 2)
                norm_p = round(peak_p / max_p, 3)

                # Classify probable harmonic sources
                source = "Worm Harmonic"
                if 420.0 <= period_sec <= 650.0:
                    source = "Worm Fundamental Period"
                elif 200.0 <= period_sec <= 300.0:
                    source = "2nd Worm Harmonic"
                elif 100.0 <= period_sec <= 160.0:
                    source = "Gear Transfer / Pulley"
                elif period_sec < 60.0:
                    source = "Seeing / Atmospheric Scintillation"

                peaks.append(
                    GuidingSpectrumPeak(
                        period_seconds=period_sec,
                        amplitude_arcsec=amp,
                        power=norm_p,
                        probable_source=source,
                    )
                )

        if peaks:
            dominant_period = peaks[0].period_seconds

    # Resampled PSD curve for UI plotting (top 30 points)
    psd_curve: list[dict[str, float]] = []
    if len(f_sub) > 0:
        step = max(1, len(f_sub) // 40)
        norm_factor = float(np.max(p_sub)) if np.max(p_sub) > 0 else 1.0
        for i in range(0, len(f_sub), step):
            f_val = float(f_sub[i])
            if f_val > 0:
                psd_curve.append({
                    "periodSeconds": round(1.0 / f_val, 1),
                    "power": round(float(p_sub[i]) / norm_factor, 3),
                })

    # Estimate DEC Backlash Reversal Delay
    # Measure cumulative pulse duration between reversal commands
    dec_backlash_ms: float | None = None
    reversal_pulse_accum: list[float] = []
    current_accum = 0.0
    last_pulse_sign = 0

    for i in range(len(pulse_dec)):
        p = pulse_dec[i]
        sign = 1 if p > 0 else (-1 if p < 0 else 0)
        if sign != 0:
            if last_pulse_sign != 0 and sign != last_pulse_sign:
                # Sign reversal detected: record accumulation until flip
                if current_accum > 0:
                    reversal_pulse_accum.append(current_accum)
                current_accum = abs(p)
            else:
                current_accum += abs(p)
            last_pulse_sign = sign

    if reversal_pulse_accum:
        dec_backlash_ms = round(float(np.median(reversal_pulse_accum)), 1)

    return GuidingSpectrumAnalysis(
        sample_count=n,
        duration_seconds=round(duration, 1),
        periodic_error_peak_to_peak_arcsec=round(pe_p_to_p, 2),
        dominant_period_seconds=dominant_period,
        dec_backlash_estimate_ms=dec_backlash_ms,
        peaks=peaks,
        psd_curve=psd_curve,
        message=(
            f"Analyzed {n} samples across {round(duration / 60.0, 1)}m: "
            f'PE={round(pe_p_to_p, 2)}", Backlash={dec_backlash_ms or "N/A"}ms.'
        ),
    )
