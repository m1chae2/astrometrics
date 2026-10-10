"""Purpose: Tests for re-analysing stored spectra with the airmass correction.

Description: `recompute_spectral_analysis.recompute_star` redoes a stored
spectrum's classification. The live pipeline scales the response-corrected
flux for the difference between the target's airmass and the airmass of the
standard star the response was fitted to. These tests check that the
recompute path applies the same correction, with the same factor, so a
recomputed spectrum matches what the pipeline would give. They use the stored
response for the ZWO ASI 533MM Pro camera and a made-up star spectrum, and
never touch a catalog database.
"""

import numpy as np
import pytest

from astrometricslib.models.stellar_source import (
    ExtinctionCorrectionRecord,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.spectroscopy.pipeline import _frame_airmass
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction import (
    EXTINCTION_CURVE_NAME,
    REASON_NO_TARGET_AIRMASS,
    apply_extinction_correction,
    extinction_correction_factor,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    apply_instrument_response,
    load_instrument_response,
)
from astrometricslib.scripts.recompute_spectral_analysis import recompute_star

CAMERA_NAME = "ZWO ASI 533MM Pro"
# The camera's wavelength limits, in Angstroms, as the script passes them.
MINIMUM_WAVELENGTH_ANGSTROM = 3000.0
MAXIMUM_WAVELENGTH_ANGSTROM = 10000.0
TARGET_AIRMASS = 1.6


class _FakeImage:
    """A stand-in for a frame that has only a header.

    Attributes
    ----------
    header : `dict`
        The header cards, here only ``AIRMASS``.
    """

    def __init__(self, airmass: float) -> None:
        """Store the airmass as the frame's header.

        Parameters
        ----------
        airmass : `float`
            The value of the ``AIRMASS`` card.
        """
        self.header = {"AIRMASS": airmass}


def _stored_star(target_airmass: float | None) -> StellarObject:
    """Build a star with a stored spectrum, as the pipeline saves it.

    The spectrum is a 9500 K blackbody shape from 3800 A to 9000 A, which
    covers the response's range of 4200 A to 8000 A. The pipeline's
    extinction record carries the airmass of the frame.

    Parameters
    ----------
    target_airmass : `float` or `None`
        The airmass in the stored extinction record. `None` stores no
        record at all, as a spectrum saved before the record existed.

    Returns
    -------
    star : `StellarObject`
        A star with a spectrum that `recompute_star` can analyze.
    """
    wavelengths = np.linspace(3800.0, 9000.0, 520)
    wavelength_cm = wavelengths * 1e-8
    # Planck's law shape at 9500 K; only the relative shape matters.
    intensity = 1.0 / (wavelength_cm**5 * np.expm1(1.4388 / (wavelength_cm * 9500.0)))
    intensity = 1000.0 * intensity / intensity.max()
    record = (
        ExtinctionCorrectionRecord(
            is_applied=True,
            target_airmass=target_airmass,
            reference_airmass=1.15,
            curve_name=EXTINCTION_CURVE_NAME,
        )
        if target_airmass is not None
        else None
    )
    spectroscopy = SpectroscopyResult(
        wavelengths_angstrom=wavelengths.tolist(),
        intensities=intensity.tolist(),
        quantum_efficiency_corrected_intensities=intensity.tolist(),
        extinction_correction=record,
    )
    return StellarObject(id="airmass-test-star", name="Airmass test star", spectroscopy=spectroscopy)


def test_recompute_applies_the_same_extinction_factor_as_the_pipeline() -> None:
    """The recomputed flux equals the pipeline's, checked at one value too.

    The pipeline reads ``AIRMASS`` from the frame header and calls
    `apply_extinction_correction` with the response's reference airmass.
    This test makes the same call for a header with the stored airmass and
    compares the whole recomputed spectrum with it. It then compares one
    value (the sample nearest H-beta) with the response-corrected value
    times `extinction_correction_factor`.
    """
    response = load_instrument_response(CAMERA_NAME)
    assert response is not None
    assert response.reference_airmass == pytest.approx(1.15)
    star = _stored_star(TARGET_AIRMASS)
    wavelengths = np.array(star.spectroscopy.wavelengths_angstrom)
    stored_intensity = np.array(star.spectroscopy.quantum_efficiency_corrected_intensities)

    assert recompute_star(star, response, MINIMUM_WAVELENGTH_ANGSTROM, MAXIMUM_WAVELENGTH_ANGSTROM)

    uncorrected = apply_instrument_response(wavelengths, stored_intensity, response)
    pipeline_flux, _ = apply_extinction_correction(
        wavelengths, uncorrected, _frame_airmass(_FakeImage(TARGET_AIRMASS)), response.reference_airmass
    )
    recomputed = np.array(star.spectroscopy.response_corrected_intensities, dtype=float)
    np.testing.assert_allclose(recomputed, pipeline_flux, rtol=1e-12, equal_nan=True)

    index = int(np.argmin(np.abs(wavelengths - 4861.0)))
    factor = extinction_correction_factor(wavelengths[index], TARGET_AIRMASS, 1.15)
    assert factor > 1.05
    assert recomputed[index] == pytest.approx(uncorrected[index] * factor, rel=1e-12)


def test_recompute_records_the_extinction_correction() -> None:
    """The extinction record says it was applied, with both airmasses."""
    response = load_instrument_response(CAMERA_NAME)
    star = _stored_star(TARGET_AIRMASS)

    recompute_star(star, response, MINIMUM_WAVELENGTH_ANGSTROM, MAXIMUM_WAVELENGTH_ANGSTROM)

    record = star.spectroscopy.extinction_correction
    assert record is not None
    assert record.is_applied
    assert record.target_airmass == pytest.approx(TARGET_AIRMASS)
    assert record.reference_airmass == pytest.approx(1.15)
    assert record.curve_name == EXTINCTION_CURVE_NAME
    assert record.reason is None


def test_recompute_is_repeatable() -> None:
    """Recomputing twice gives the same flux, not a doubled correction."""
    response = load_instrument_response(CAMERA_NAME)
    star = _stored_star(TARGET_AIRMASS)

    recompute_star(star, response, MINIMUM_WAVELENGTH_ANGSTROM, MAXIMUM_WAVELENGTH_ANGSTROM)
    first = np.array(star.spectroscopy.response_corrected_intensities, dtype=float)
    recompute_star(star, response, MINIMUM_WAVELENGTH_ANGSTROM, MAXIMUM_WAVELENGTH_ANGSTROM)
    second = np.array(star.spectroscopy.response_corrected_intensities, dtype=float)

    np.testing.assert_allclose(second, first, rtol=1e-12, equal_nan=True)


def test_recompute_skips_the_correction_without_a_recorded_airmass() -> None:
    """A spectrum with no stored airmass is left uncorrected and says so."""
    response = load_instrument_response(CAMERA_NAME)
    star = _stored_star(None)
    wavelengths = np.array(star.spectroscopy.wavelengths_angstrom)
    stored_intensity = np.array(star.spectroscopy.quantum_efficiency_corrected_intensities)

    recompute_star(star, response, MINIMUM_WAVELENGTH_ANGSTROM, MAXIMUM_WAVELENGTH_ANGSTROM)

    np.testing.assert_allclose(
        np.array(star.spectroscopy.response_corrected_intensities, dtype=float),
        apply_instrument_response(wavelengths, stored_intensity, response),
        rtol=1e-12,
        equal_nan=True,
    )
    record = star.spectroscopy.extinction_correction
    assert record is not None
    assert not record.is_applied
    assert record.reason == REASON_NO_TARGET_AIRMASS
    assert record.reference_airmass == pytest.approx(1.15)


def test_recompute_without_a_response_records_no_extinction_correction() -> None:
    """With no stored response there is nothing to correct and no record."""
    star = _stored_star(TARGET_AIRMASS)

    recompute_star(star, None, MINIMUM_WAVELENGTH_ANGSTROM, MAXIMUM_WAVELENGTH_ANGSTROM)

    assert star.spectroscopy.extinction_correction is None
