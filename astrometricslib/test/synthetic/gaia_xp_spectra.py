"""Purpose: A fake Gaia XP driver and spectra with a known tilt and shift.

Description: Tests of the Gaia XP check need a Gaia XP spectrum and a
calibrated spectrum of the same star that differ in a known way. This file
builds both from one Pickles template (a bundled reference spectrum): the XP
spectrum is the template blurred to Gaia's resolution and sampled every 20
Angstroms, and the calibrated spectrum is the template blurred to the
instrument's resolution, then tilted and shifted by amounts the test chooses.
`FakeGaiaXpDriver` serves the XP spectrum without any network.
"""

import numpy as np

from astrometricslib.drivers.interfaces.gaia_xp_driver import GaiaXpDriver
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import (
    SLOPE_REFERENCE_ANGSTROM,
    xp_resolution_fwhm_angstrom,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    ResolutionProfile,
    blur_to_resolution_profile,
    load_line_spread_profile,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import _get_reference_templates

# The camera whose stored line spread blurs the calibrated spectrum.
CAMERA_NAME = "ZWO ASI 533MM Pro"

# Flux scale of the fake XP spectra, in W m^-2 nm^-1 (a star near G = 6).
XP_FLUX_SCALE = 1e-12


class FakeGaiaXpDriver(GaiaXpDriver):
    """Serves chosen XP spectra from memory and counts the requests.

    Parameters
    ----------
    spectra : `dict` [`int`, `tuple`], optional
        For each Gaia source id, the three arrays a real driver gives. A
        source not listed has no XP spectrum.
    """

    def __init__(self, spectra: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] | None = None) -> None:
        """Keep the spectra and start the request log.

        Parameters
        ----------
        spectra : `dict` [`int`, `tuple`], optional
            The spectra to serve, by Gaia source id.
        """
        self.spectra = dict(spectra or {})
        self.requested_ids: list[int] = []

    def sampled_spectrum(self, source_id: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Give the stored spectrum of a source, or `None`.

        Parameters
        ----------
        source_id : `int`
            The Gaia DR3 source id.

        Returns
        -------
        spectrum : `tuple` or `None`
            The stored arrays, or `None` for a source that was not stored.
        """
        self.requested_ids.append(int(source_id))
        return self.spectra.get(int(source_id))


def make_xp_spectrum(
    spectral_type: str = "G2V", relative_error: float = 0.01
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build the XP spectrum a star of a template's type would have.

    Parameters
    ----------
    spectral_type : `str`, optional
        The bundled Pickles template to use.
    relative_error : `float`, optional
        The flux error as a fraction of the flux.

    Returns
    -------
    spectrum : `tuple` [`numpy.ndarray`, `numpy.ndarray`, `numpy.ndarray`]
        Wavelengths in Angstroms (3360 to 10200 in steps of 20), flux in
        W m^-2 nm^-1 and its error.
    """
    template_wavelength, template_flux = _get_reference_templates()[spectral_type]
    blurred = blur_to_resolution_profile(
        template_wavelength,
        template_flux,
        ResolutionProfile(template_wavelength, xp_resolution_fwhm_angstrom(template_wavelength)),
    )
    wavelength = np.arange(3360.0, 10201.0, 20.0)
    flux = np.interp(wavelength, template_wavelength, blurred) * XP_FLUX_SCALE
    return wavelength, flux, relative_error * flux


def make_calibrated_spectrum(
    spectral_type: str = "G2V",
    tilt_percent_per_1000_angstrom: float = 0.0,
    shift_angstrom: float = 0.0,
    noise_fraction: float = 0.0,
    seed: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the response-corrected spectrum of a star with a known error.

    The spectrum is the template blurred to the instrument's line spread,
    sampled every 11.2 Angstroms (one pixel), then multiplied by a straight
    tilt that is 1 at 5500 Angstroms and shifted redward by a chosen amount.
    Samples outside 4200-8000 Angstroms are NaN, as the pipeline leaves them.

    Parameters
    ----------
    spectral_type : `str`, optional
        The bundled Pickles template to use.
    tilt_percent_per_1000_angstrom : `float`, optional
        The tilt, in percent of the flux at 5500 Angstroms per 1000 Angstroms.
    shift_angstrom : `float`, optional
        How far the features sit redward of where they belong.
    noise_fraction : `float`, optional
        The standard deviation of random noise, as a fraction of the flux.
    seed : `int`, optional
        The random seed.

    Returns
    -------
    wavelength_angstrom, intensity : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The wavelengths and the brightness.
    """
    template_wavelength, template_flux = _get_reference_templates()[spectral_type]
    profile = load_line_spread_profile(CAMERA_NAME)
    blurred = blur_to_resolution_profile(template_wavelength, template_flux, profile)
    wavelength = np.arange(4000.0, 8600.0, 11.2)
    intensity = np.interp(wavelength - shift_angstrom, template_wavelength, blurred)
    tilt = 1.0 + tilt_percent_per_1000_angstrom / 100.0 * (wavelength - SLOPE_REFERENCE_ANGSTROM) / 1000.0
    intensity = intensity * tilt
    if noise_fraction > 0:
        intensity = intensity * (
            1.0 + noise_fraction * np.random.default_rng(seed).standard_normal(wavelength.size)
        )
    intensity = np.where((wavelength < 4200.0) | (wavelength > 8000.0), np.nan, intensity)
    return wavelength, intensity
