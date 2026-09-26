"""Tests for mount pointing model decomposition and guiding FFT spectrum."""

import math
from unittest.mock import MagicMock

import pytest

from wayfindinglib.analytics.guiding_spectrum import analyze_guiding_telemetry
from wayfindinglib.analytics.pointing_model import fit_pointing_model


def test_fit_pointing_model_insufficient_points() -> None:
    """Verify that fewer than 4 points returns insufficient_data fallback."""
    attempts = [
        {"ra": 180.0, "dec": 45.0, "delta_ra_arcsec": 5.0, "delta_dec_arcsec": -3.0},
        {"ra": 190.0, "dec": 50.0, "delta_ra_arcsec": 6.0, "delta_dec_arcsec": -2.0},
    ]
    model = fit_pointing_model(attempts)
    assert model.confidence == "insufficient_data"
    assert model.sample_count == 2
    assert model.improvement_percent == pytest.approx(0.0)


def test_fit_pointing_model_decomposes_known_coefficients() -> None:
    """Verify pointing model accurately recovers synthetic geometric errors."""
    # Synthetic known terms
    true_ih = 15.0
    true_id = -25.0
    true_me = 60.0  # Polar elevation error
    true_ma = -40.0  # Polar azimuth error
    true_ch = 30.0  # Cone non-orthogonality
    true_tf = 20.0  # Gravity tube flexure

    latitude_deg = 45.0
    phi = math.radians(latitude_deg)

    attempts = []
    # Generate points across an hour angle and declination grid
    for ra_h in range(0, 24, 2):
        for dec_d in [15.0, 35.0, 55.0, 75.0]:
            dec_rad = math.radians(dec_d)
            h_rad = math.radians((ra_h * 15.0 * 3.0) % 360.0 - 180.0)

            tan_dec = math.tan(dec_rad)
            cos_dec = math.cos(dec_rad)
            sec_dec = 1.0 / cos_dec
            sin_h = math.sin(h_rad)
            cos_h = math.cos(h_rad)
            sin_phi = math.sin(phi)
            cos_phi = math.cos(phi)

            d_ra_proj = (
                -true_ih
                + true_me * sin_h * tan_dec
                - true_ma * cos_h * tan_dec
                + true_ch * sec_dec
                + true_tf * cos_h * cos_phi * tan_dec
            )
            d_ra = d_ra_proj / cos_dec

            d_dec = (
                -true_id
                + true_me * cos_h
                + true_ma * sin_h
                + true_tf * (cos_h * sin_phi * math.sin(dec_rad) - cos_phi * cos_dec)
            )

            attempts.append({
                "ra": float(ra_h),
                "dec": float(dec_d),
                "delta_ra_arcsec": d_ra,
                "delta_dec_arcsec": d_dec,
            })

    model = fit_pointing_model(attempts, latitude_deg=latitude_deg)
    assert model.confidence == "high"
    assert model.sample_count == len(attempts)
    assert model.residual_rms_arcsec < 0.1
    assert model.improvement_percent > 99.0

    # Verify parameters match synthetic truth within 0.1 arcsec
    assert model.ih_arcsec == pytest.approx(true_ih, abs=0.1)
    assert model.id_arcsec == pytest.approx(true_id, abs=0.1)
    assert model.me_arcsec == pytest.approx(true_me, abs=0.1)
    assert model.ma_arcsec == pytest.approx(true_ma, abs=0.1)
    assert model.ch_arcsec == pytest.approx(true_ch, abs=0.1)
    assert model.tf_arcsec == pytest.approx(true_tf, abs=0.1)
    assert model.total_polar_error_arcsec == pytest.approx(math.hypot(true_me, true_ma), abs=0.1)


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
    assert analysis.duration_seconds == pytest.approx(total_duration, abs=5.0)
    assert analysis.periodic_error_peak_to_peak_arcsec == pytest.approx(7.0, abs=1.0)
    assert analysis.dominant_period_seconds == pytest.approx(worm_period, abs=25.0)
    assert len(analysis.peaks) > 0
    assert analysis.peaks[0].probable_source == "Worm Fundamental Period"
    assert analysis.dec_backlash_estimate_ms is not None


def test_alignment_service_compute_pointing_model() -> None:
    """Verify AlignmentService.compute_pointing_model queries logger."""
    from backend.services.observatory.alignment_service import AlignmentService

    logger_mock = MagicMock()
    logger_mock.get_session_alignment_attempts.return_value = [
        {"ra": 180.0, "dec": 45.0, "delta_ra_arcsec": 5.0, "delta_dec_arcsec": -3.0},
        {"ra": 190.0, "dec": 50.0, "delta_ra_arcsec": 6.0, "delta_dec_arcsec": -2.0},
    ]

    service = AlignmentService(indi_interface=MagicMock(), logger_interface=logger_mock)
    res = service.compute_pointing_model(session_id="2026-09-25")
    assert res["sampleCount"] == 2
    assert res["confidence"] == "insufficient_data"
    logger_mock.get_session_alignment_attempts.assert_called_once_with("2026-09-25")


def test_guiding_service_analyze_guiding_spectrum() -> None:
    """Verify GuidingService.analyze_guiding_spectrum delegates to logger."""
    from backend.services.observatory.guiding_service import GuidingService

    logger_mock = MagicMock()
    logger_mock.get_guiding_logs.return_value = []

    service = GuidingService(indi_interface=MagicMock(), logger_interface=logger_mock)
    res = service.analyze_guiding_spectrum(session_id="2026-09-25")
    assert res["sampleCount"] == 0
    logger_mock.get_guiding_logs.assert_called_once_with(session_id="2026-09-25", limit=2000)
