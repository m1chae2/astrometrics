"""Unit tests for airmass detrending, BLS transit search, and periodograms."""

from datetime import datetime, timedelta

import numpy as np
from astropy.io import fits

from astrometricslib.models.stellar_source import LightCurve, StellarObject
from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
    VariabilityAnalyzer,
    compute_frame_airmass,
)


def test_compute_frame_airmass_direct_header():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify compute_frame_airmass extracts AIRMASS from FITS header."""
    header = fits.Header()
    header["AIRMASS"] = 1.35
    assert abs(compute_frame_airmass(header) - 1.35) < 1e-4


def test_compute_frame_airmass_from_altitude():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify compute_frame_airmass calculates airmass from altitude."""
    header = fits.Header()
    header["ALTITUDE"] = 60.0  # Zenith angle = 30 deg -> sec(30 deg) = 1.1547
    airmass = compute_frame_airmass(header)
    assert abs(airmass - 1.1547) < 0.01


def test_detrend_light_curves_airmass():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify detrend_light_curves_airmass removes extinction trend."""
    analyzer = VariabilityAnalyzer()
    star = StellarObject(id="star_1")
    star.light_curve = LightCurve()

    airmasses = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
    # Synthetic light curve with extinction slope
    fluxes_norm = [1.0 - 0.05 * (x - 1.0) for x in airmasses]

    star.light_curve.fluxes_normalized = fluxes_norm
    star.light_curve.airmasses = airmasses
    analyzer.stellar_objects = [star]

    analyzer.detrend_light_curves_airmass()

    assert len(star.light_curve.fluxes_detrended) == 6
    # Detrended fluxes should have lower variance than original trend
    assert np.std(star.light_curve.fluxes_detrended) < np.std(fluxes_norm)


def test_bls_transit_search_synthetic_transit():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify BLS transit search identifies a periodic transit dip."""
    analyzer = VariabilityAnalyzer()
    star = StellarObject(id="exo_target")

    t0 = datetime(2026, 1, 1, 0, 0, 0)
    timestamps = [t0 + timedelta(minutes=15 * i) for i in range(20)]
    fluxes = [1.0] * 20
    # Insert 1.5% transit dip at frames 8, 9, 10
    fluxes[8] = 0.985
    fluxes[9] = 0.985
    fluxes[10] = 0.985

    star.light_curve = LightCurve(
        timestamps=timestamps,
        fluxes=fluxes,
        fluxes_normalized=fluxes,
        fluxes_detrended=fluxes,
    )

    candidate = analyzer.run_bls_transit_search(star)
    assert candidate is not None
    assert candidate.transit_depth_mag > 0.0
    assert candidate.transit_snr > 0.0


def test_lomb_scargle_periodogram_periodic_signal():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify Lomb-Scargle periodogram recovers periodic variable signals."""
    analyzer = VariabilityAnalyzer()
    star = StellarObject(id="variable_target")

    t0 = datetime(2026, 1, 1, 0, 0, 0)
    timestamps = [t0 + timedelta(hours=i) for i in range(24)]
    # Sinusoidal variability with 6-hour period
    t_days = np.array([i / 24.0 for i in range(24)])
    fluxes = (1.0 + 0.1 * np.sin(2 * np.pi * t_days / 0.25)).tolist()

    star.light_curve = LightCurve(
        timestamps=timestamps,
        fluxes=fluxes,
        fluxes_normalized=fluxes,
        fluxes_detrended=fluxes,
    )

    periodogram = analyzer.run_lomb_scargle_periodogram(star)
    assert periodogram is not None
    assert periodogram.best_period_days > 0.0
    assert periodogram.power > 0.0
