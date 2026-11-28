"""Tests for mount pointing model decomposition and guiding FFT spectrum."""

import math

import pytest

from wayfindinglib.analytics.guiding_spectrum import analyze_guiding_telemetry
from wayfindinglib.analytics.pointing_model import fit_pointing_model


def test_fit_pointing_model_insufficient_points() -> None:
    """Verify that fewer than 4 points returns insufficient_data fallback."""
    attempts = [
        {
            "ra": 180.0,
            "dec": 45.0,
            "timestamp": 1_795_000_000.0,
            "delta_ra_arcsec": 5.0,
            "delta_dec_arcsec": -3.0,
        },
        {
            "ra": 190.0,
            "dec": 50.0,
            "timestamp": 1_795_000_100.0,
            "delta_ra_arcsec": 6.0,
            "delta_dec_arcsec": -2.0,
        },
    ]
    model = fit_pointing_model(attempts, latitude_deg=45.0, longitude_deg=-111.0)
    assert model.confidence == "insufficient_data"
    assert model.sample_count == 2
    assert model.improvement_pct == pytest.approx(0.0)


def test_analyze_guiding_telemetry_detects_worm_period_and_backlash() -> None:
    """Verify FFT isolates dominant worm period and backlash pulse delay."""
    # Synthesize 480-second worm periodic error
    worm_period = 480.0
    pe_amplitude = 3.5  # +/- 3.5 arcsec -> 7.0 arcsec peak-to-peak
    dt = 2.0  # 2-second guide camera exposures
    total_duration = 2400.0  # 5 complete worm cycles

    samples = []
    t = 0.0
    pulse_sign = 1
    while t < total_duration:
        # Periodic error on RA
        ra_drift = pe_amplitude * math.sin(2.0 * math.pi * t / worm_period)
        # Reverse DEC pulse every 120 seconds
        if int(t) % 120 == 0:
            pulse_sign *= -1
        pulse_dec = 250.0 * pulse_sign

        samples.append({
            "timestamp": 1700000000.0 + t,
            "dra": ra_drift,
            "ddec": 0.2,
            "pulse_ra": 50.0,
            "pulse_dec": pulse_dec,
        })
        t += dt

    analysis = analyze_guiding_telemetry(samples)
    assert analysis.sample_count == len(samples)
    assert analysis.duration_s == pytest.approx(total_duration, abs=5.0)
    assert analysis.periodic_error_peak_to_peak_arcsec == pytest.approx(7.0, abs=1.0)
    assert analysis.dominant_period_s == pytest.approx(worm_period, abs=25.0)
    assert len(analysis.peaks) > 0
    assert analysis.peaks[0].probable_source == "Worm Fundamental Period"
    assert analysis.dec_backlash_estimate_ms is not None
