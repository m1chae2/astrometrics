"""Purpose: Unit tests for how the response fit blurs its reference spectrum.

Description: The instrument response is the ratio of an observed standard
star (Vega) to a bundled reference spectrum of its type. The reference is
much sharper than a slitless grism spectrum, so it is blurred first. The
grism's line spread grows with wavelength, from about 42 Angstroms in the
blue to 148 Angstroms at H-alpha for the ASI533 setup. These tests observe
the bundled A0V reference through the stored line-spread profile, times a
known tilted response, and check that the fit recovers that response. They
also check that a camera with no stored profile still uses the single
resolution width.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.pre_processing import instrument_response
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    InstrumentResponse,
    derive_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    blur_sigma_in_samples,
    blur_to_resolution_profile,
    load_line_spread_profile,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import _get_reference_templates

CAMERA_NAME = "ZWO ASI 533MM Pro"
# The observed spectrum's sampling, close to the instrument's 11 A per pixel.
OBSERVED_GRID_ANGSTROM = np.arange(4000.0, 8200.0, 11.0)
# Points where the fitted response is compared with the true one.
CHECK_GRID_ANGSTROM = np.linspace(4250.0, 7950.0, 400)
# A smooth true response: a tilt and a gentle curve, as a log polynomial in
# the same scaled wavelength the fit uses.
TRUE_LOG_RESPONSE_COEFFICIENTS = (0.0, 0.0, -0.4, 0.3, 2.0)
# The fitted response must match the true one to this fraction everywhere.
RECOVERY_TOLERANCE = 0.001


def _response(coefficients: tuple[float, ...] | list[float], wavelength_angstrom: np.ndarray) -> np.ndarray:
    """Evaluate a log-polynomial response like the one the fit stores.

    Parameters
    ----------
    coefficients : `tuple` [`float`] or `list` [`float`]
        Polynomial coefficients of the natural log of the response, highest
        power first, in the fit's scaled wavelength.
    wavelength_angstrom : `numpy.ndarray`
        Where to evaluate it, in Angstroms.

    Returns
    -------
    response : `numpy.ndarray`
        The response at each wavelength.
    """
    scaled = (wavelength_angstrom - 6000.0) / 2000.0
    return np.exp(np.polyval(coefficients, scaled))


def _observed_vega() -> np.ndarray:
    """Observe the bundled A0V reference through the stored line spread.

    Returns
    -------
    intensity : `numpy.ndarray`
        The observed brightness on `OBSERVED_GRID_ANGSTROM`: the reference
        blurred by the camera's line-spread profile, times the true response.
    """
    wavelength, flux = _get_reference_templates()["A0V"]
    profile = load_line_spread_profile(CAMERA_NAME)
    assert profile is not None
    blurred = blur_to_resolution_profile(wavelength, flux, profile)
    return _response(TRUE_LOG_RESPONSE_COEFFICIENTS, OBSERVED_GRID_ANGSTROM) * np.interp(
        OBSERVED_GRID_ANGSTROM, wavelength, blurred
    )


def _worst_relative_error(response: InstrumentResponse) -> float:
    """Compare a fitted response with the true one, ignoring overall scale.

    Parameters
    ----------
    response : `InstrumentResponse`
        The fitted response.

    Returns
    -------
    error : `float`
        The largest fractional difference over `CHECK_GRID_ANGSTROM`.
    """
    truth = _response(TRUE_LOG_RESPONSE_COEFFICIENTS, CHECK_GRID_ANGSTROM)
    fitted = _response(response.coefficients, CHECK_GRID_ANGSTROM)
    fitted = fitted / np.median(fitted / truth)
    return float(np.max(np.abs(fitted / truth - 1.0)))


@pytest.mark.parametrize("passed_width_angstrom", [45.0, 119.0])
def test_the_fit_recovers_the_response_whatever_single_width_is_passed(passed_width_angstrom: float) -> None:
    """With a stored profile, the fit ignores a mismatched single width.

    The script passes the width measured from the trail, about 45 A, which
    is right in the blue and three times too narrow at H-alpha. Blurring
    the reference to that one width left the fitted response up to 0.5
    percent wrong, and 119 A left it up to 2.6 percent wrong.
    """
    response = derive_instrument_response(
        OBSERVED_GRID_ANGSTROM,
        _observed_vega(),
        "A0V",
        CAMERA_NAME,
        "synthetic Vega",
        resolution_element_angstrom=passed_width_angstrom,
    )
    assert _worst_relative_error(response) < RECOVERY_TOLERANCE


def test_without_a_profile_the_single_width_blurs_the_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """A camera with no stored profile falls back to the single width.

    The star is observed with one 60 A blur and the fit is told 60 A, so the
    fit must recover the response. Without the fallback the reference would
    stay sharp and the Balmer wings would bend the response.
    """
    monkeypatch.setattr(instrument_response, "load_line_spread_profile", lambda camera_name: None)
    wavelength, flux = _get_reference_templates()["A0V"]
    width_angstrom = 60.0
    blurred = instrument_response.gaussian_filter1d(
        flux, blur_sigma_in_samples(width_angstrom, float(np.median(np.diff(wavelength))))
    )
    observed = _response(TRUE_LOG_RESPONSE_COEFFICIENTS, OBSERVED_GRID_ANGSTROM) * np.interp(
        OBSERVED_GRID_ANGSTROM, wavelength, blurred
    )
    response = derive_instrument_response(
        OBSERVED_GRID_ANGSTROM,
        observed,
        "A0V",
        CAMERA_NAME,
        "synthetic Vega, no profile",
        resolution_element_angstrom=width_angstrom,
    )
    assert _worst_relative_error(response) < RECOVERY_TOLERANCE
