"""Corrects a spectrum for how the whole instrument favors some colors.

A camera's quantum efficiency is only one part of how an observed
spectrum differs from the star's true one. The grating passes different
colors with different efficiency, the telescope's coatings and the air
absorb some colors more than others, and none of that is known
accurately in advance. Left uncorrected, this tilt is larger than the
real differences between stellar types, and it is why a spectrum of Vega
(an A0V star) was matched to an F6V reference.

The fix is the standard one in spectroscopy: observe a star whose true
spectrum is known (Vega, type A0V, whose reference spectrum is bundled
here), divide the observation by the reference, and smooth the result.
That smooth curve is the instrument response. Dividing any other star's
spectrum by it removes the tilt and leaves the star's own shape.

A response belongs to one instrument setup (this camera, grating and
telescope). It is stored as a small JSON file next to the reference
spectra and used only for the camera it was derived for.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d

from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.foundation.errors import InvalidArgumentError, ProcessingError
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    ResolutionProfile,
    blur_sigma_in_samples,
    blur_to_resolution_profile,
    load_line_spread_profile,
)

_DATA_DIR = Path(__file__).parent.parent / "data"

# Wavelengths of strong hydrogen lines and the Ca II H line, in Angstroms.
# The star's own dips at these places are not part of the instrument
# response, so the fit skips the samples around them.
_LINES_TO_SKIP_ANGSTROM = (3970.0, 4102.0, 4340.0, 4861.0, 6563.0)

# How far either side of each line the fit skips, as a multiple of the
# instrument's line spread (the full width at half maximum, FWHM, of a sharp
# line after the instrument blurs it) at that line's wavelength. A Gaussian
# dip is down to 0.2 percent of its depth at 1.5 FWHM (3.5 sigma), so what is
# left outside the skipped band is far below the 1 percent the response must
# hold. The line spread is 42 A at 4200 A and 148 A at 6563 A, so the skipped
# band is 126 A wide at H-delta and 444 A wide at H-alpha. A fixed 60 A half
# width would leave most of the H-alpha and H-beta wings in the fit, and the
# response would then divide Vega's line wings out of every target.
_LINE_SKIP_HALF_WIDTH_PER_FWHM = 1.5

# The least the fit skips either side of a line, in Angstroms, whatever the
# line spread says. In the blue the line spread is only 42-45 A, but the
# Balmer wings there reach about 100 A, so those lines keep a band of at least
# 60 A each side. A wider band in the blue left the fit unconstrained below
# 4300 A, and it swung to a wrong value at the blue end.
_MINIMUM_LINE_SKIP_HALF_WIDTH_ANGSTROM = 60.0

# The default range the response is fitted over, and so the range spectra
# are compared with the references (a response marks everything outside
# its range as not usable). The reference spectra and the camera both
# reach 10000 A, but only this part is reliable:
#
# * Below 4200 A the spectrum is faint and the Balmer lines crowd together.
# * Above 8000 A the sensor's quantum efficiency falls from 27% to 6%, so
#   correcting for it multiplies the noise by 4 to 16, the atmosphere's
#   water and oxygen bands take over, and light from the blue arrives in
#   second order. A grism sends light of wavelength L to a second position
#   as well, where it overlaps first-order light of 2 x L. The extraction
#   starts at 3800 A, so second-order light from there lands at 7600 A and
#   pollutes every longer wavelength, most for blue-bright stars.
#
# Measured on the stored spectra (September 2026 standards check, see
# validate_spectral_and_period_analysis.py): extending the range to 10000 A
# left Vega, HD 151023 and Alcor about as well matched (residual 0.04-0.10),
# but pushed stars such as HD 150679 (an A2 star, so blue-bright) from
# 0.19-0.28 to 0.5-1.8, and weighting the comparison by the sensor's
# sensitivity did not fix that.
#
# The second-order reasoning above holds for the grism this default was
# chosen for: a Star Analyzer 200, used with no blocking filter in front
# of it to remove second-order light. For THAT grism, 8000 A is the
# physically appropriate cutoff, not a conservative placeholder waiting
# to be relaxed. A star-dependent cutoff (trusting a red star's own
# second-order-light-poor spectrum further than a blue star's) would be
# a possible refinement for it, since `second_order_blue_to_red_ratio`
# already measures how much of this each star has -- but that is not a
# fix for a wrong number here, since there is nothing wrong with 8000 A
# for a blue star on the SA-200.
#
# This module has no way to check which grism is actually in use: a
# response is looked up by camera name only (`load_instrument_response`),
# and nothing here records the grism, its line density, or whether a
# blocking filter is fitted. A different grism changes where second-order
# light lands (it depends on the grism's own dispersion, not just where
# extraction starts) and may not need this cutoff at all -- particularly
# one built with a blocking filter, which removes second-order light at
# its source rather than needing a cutoff to guess around it. Whoever
# re-derives this response for a new grism should re-check this range
# rather than assume 8000 A still applies; deriving it over the full
# camera range is supported (see `derive_instrument_response`).
DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM = (4200.0, 8000.0)

# Degree of the polynomial fitted to the logarithm of (observed /
# reference). A degree of 4 follows the grating and sensor's broad
# shape (it rises, peaks near 5300 A and falls) without bending to follow
# the noise. A degree of 5 fitted the Vega master stack marginally
# better but made the blue end swing, so it is not used.
_RESPONSE_POLYNOMIAL_DEGREE = 4

# The polynomial is written in this scaled variable, (wavelength - 6000) /
# 2000, so its coefficients stay of order one and the fit is stable.
_WAVELENGTH_CENTER_ANGSTROM = 6000.0
_WAVELENGTH_SCALE_ANGSTROM = 2000.0


@dataclass(frozen=True)
class InstrumentResponse:
    """The smooth tilt an instrument puts on every spectrum.

    Attributes
    ----------
    camera_name : `str`
        The camera the response was derived for.
    coefficients : `tuple` [`float`, ...]
        Polynomial coefficients, highest power first, for the natural
        logarithm of the response against the scaled wavelength.
    minimum_wavelength_angstrom : `float`
        The shortest wavelength the response is valid at.
    maximum_wavelength_angstrom : `float`
        The longest wavelength the response is valid at.
    reference_type : `str`
        The reference spectrum the response was derived against.
    source : `str`
        A note on what was observed to derive it.
    reference_airmass : `float` or `None`
        The airmass of the standard star's observation. The response
        includes the air's dimming at this airmass, so a target observed
        at another airmass needs the correction in `atmospheric_extinction`.
        `None` when the file that stored the response does not record it.
    """

    camera_name: str
    coefficients: tuple[float, ...]
    minimum_wavelength_angstrom: float
    maximum_wavelength_angstrom: float
    reference_type: str
    source: str
    reference_airmass: float | None = None

    def value_at(self, wavelength_angstrom: np.ndarray) -> np.ndarray:
        """Give the response at some wavelengths.

        Parameters
        ----------
        wavelength_angstrom : `np.ndarray`
            Wavelengths, in Angstroms. Values outside the valid range are
            held at the nearest valid wavelength's response.

        Returns
        -------
        response : `np.ndarray`
            The relative response (1.0 has no meaning by itself; only the
            shape matters).
        """
        clipped = np.clip(
            wavelength_angstrom, self.minimum_wavelength_angstrom, self.maximum_wavelength_angstrom
        )
        scaled = (clipped - _WAVELENGTH_CENTER_ANGSTROM) / _WAVELENGTH_SCALE_ANGSTROM
        return np.exp(np.polyval(self.coefficients, scaled))


def load_instrument_response(camera_name: str) -> InstrumentResponse | None:
    """Read the stored response for a camera, if one exists.

    Parameters
    ----------
    camera_name : `str`
        The camera's name.

    Returns
    -------
    response : `InstrumentResponse` or `None`
        The response, or `None` when none has been derived for this camera.
    """
    wanted = normalize_camera_name(camera_name)
    for path in sorted(_DATA_DIR.glob("instrument_response_*.json")):
        stored = json.loads(path.read_text())
        if normalize_camera_name(stored["camera_name"]) == wanted:
            return InstrumentResponse(
                camera_name=stored["camera_name"],
                coefficients=tuple(stored["coefficients"]),
                minimum_wavelength_angstrom=float(stored["minimum_wavelength_angstrom"]),
                maximum_wavelength_angstrom=float(stored["maximum_wavelength_angstrom"]),
                reference_type=stored["reference_type"],
                source=stored["source"],
                reference_airmass=(
                    float(stored["reference_airmass"])
                    if stored.get("reference_airmass") is not None
                    else None
                ),
            )
    return None


def apply_instrument_response(
    wavelength_angstrom: np.ndarray, intensity: np.ndarray, response: InstrumentResponse
) -> np.ndarray:
    """Remove an instrument's tilt from a spectrum.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The spectrum's wavelengths, in Angstroms.
    intensity : `np.ndarray`
        The spectrum's brightness (already corrected for the sensor's
        quantum efficiency).
    response : `InstrumentResponse`
        The response to divide out.

    Returns
    -------
    corrected : `np.ndarray`
        The corrected brightness, NaN outside the response's valid range
        (where it would only be a guess).
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    corrected = np.asarray(intensity, dtype=float) / response.value_at(wavelength_angstrom)
    outside = (wavelength_angstrom < response.minimum_wavelength_angstrom) | (
        wavelength_angstrom > response.maximum_wavelength_angstrom
    )
    corrected[outside] = np.nan
    return corrected


def instrument_response_correction_factor(
    wavelength_angstrom: np.ndarray, response: InstrumentResponse
) -> np.ndarray:
    """Give the number that removing the instrument's tilt multiplies by.

    `apply_instrument_response` divides the brightness by the response, so it
    multiplies by one over the response. A brightness error is multiplied by
    the same number (see `intensity_variance`).

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The spectrum's wavelengths, in Angstroms.
    response : `InstrumentResponse`
        The response that is divided out.

    Returns
    -------
    factor : `np.ndarray`
        One over the response at each wavelength, NaN outside the response's
        valid range, where `apply_instrument_response` also gives NaN.
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    factor = 1.0 / response.value_at(wavelength_angstrom)
    outside = (wavelength_angstrom < response.minimum_wavelength_angstrom) | (
        wavelength_angstrom > response.maximum_wavelength_angstrom
    )
    factor[outside] = np.nan
    return factor


def line_skip_half_widths_angstrom(
    line_spread_profile: ResolutionProfile | None, fallback_line_spread_angstrom: float
) -> np.ndarray:
    """Give how far either side of each strong line the response fit skips.

    The fit skips `_LINE_SKIP_HALF_WIDTH_PER_FWHM` times the line spread at
    the line's wavelength, and never less than
    `_MINIMUM_LINE_SKIP_HALF_WIDTH_ANGSTROM`.

    Parameters
    ----------
    line_spread_profile : `ResolutionProfile` or `None`
        The instrument's line spread (a FWHM, in Angstroms) at each
        wavelength, or `None` when none is known.
    fallback_line_spread_angstrom : `float`
        The line spread to use at every line when there is no profile, in
        Angstroms.

    Returns
    -------
    half_widths_angstrom : `numpy.ndarray`
        One half-width per entry of `_LINES_TO_SKIP_ANGSTROM`, in
        Angstroms.
    """
    lines = np.array(_LINES_TO_SKIP_ANGSTROM)
    line_spread = (
        line_spread_profile.at(lines)
        if line_spread_profile is not None
        else np.full(lines.size, fallback_line_spread_angstrom)
    )
    return np.maximum(_LINE_SKIP_HALF_WIDTH_PER_FWHM * line_spread, _MINIMUM_LINE_SKIP_HALF_WIDTH_ANGSTROM)


def derive_instrument_response(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    reference_type: str,
    camera_name: str,
    source: str,
    wavelength_range_angstrom: tuple[float, float] = DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    line_spread_profile: ResolutionProfile | None = None,
    reference_airmass: float | None = None,
) -> InstrumentResponse:
    """Fit the response from an observation of a star of known type.

    The reference spectrum is much sharper than a slitless grism spectrum,
    so it is blurred to the instrument's resolution before the two are
    compared. Blurring it to the wrong width would leave a mismatch around
    every line, and the fit would wrongly treat that as part of the
    instrument's response. The line spread of a slitless grism spectrum
    grows with wavelength (about 42 A at 4200 A to 148 A at 6563 A for the
    ASI533 setup), so the reference is blurred with the camera's stored
    line-spread profile when one exists, the same profile the classifier
    uses. A single width is used only when the camera has no profile. On a
    synthetic Vega observed through the stored profile, a single 45 A width
    left a fitted response up to 0.5 percent wrong and a single 119 A width
    up to 2.6 percent wrong, while the profile blur recovered it exactly.

    The fit also skips the samples around the strong hydrogen lines
    (`_LINES_TO_SKIP_ANGSTROM`). The skipped band reaches
    `_LINE_SKIP_HALF_WIDTH_PER_FWHM` times the instrument's line spread
    either side of the line. The line spread comes from the same stored
    profile the classifier uses (`load_line_spread_profile`), evaluated at
    the line's wavelength.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The observed spectrum's wavelengths, in Angstroms.
    intensity : `np.ndarray`
        The observed brightness, corrected for the sensor's quantum
        efficiency.
    reference_type : `str`
        The star's known spectral type as a bundled reference label, for
        example ``"A0V"``.
    camera_name : `str`
        The camera used.
    source : `str`
        A note describing the observation, stored with the response.
    wavelength_range_angstrom : `tuple` [`float`, `float`], optional
        The lowest and highest wavelength to fit, in Angstroms. The range
        is cut to where the reference spectrum exists (3000-10000 A, the
        camera's range). The default leaves out the blue end and the red
        end where second-order light and low sensitivity spoil the spectrum;
        see `DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM`.
    resolution_element_angstrom : `float`, optional
        How much the instrument blurs this observation, in Angstroms, used
        only when no line-spread profile is known. Defaults to
        `FALLBACK_RESOLUTION_ELEMENT_ANGSTROM`; pass the observation's own
        measured value when there is one (see `spectral_resolution`).
    line_spread_profile : `ResolutionProfile`, optional
        The instrument's line spread at each wavelength, in Angstroms. It
        sets both the reference's blur and the band skipped around each
        hydrogen line. When `None`, the stored profile for `camera_name` is
        read. When the camera has none, `resolution_element_angstrom`
        stands in for both.
    reference_airmass : `float`, optional
        The airmass the standard star was observed at, stored with the
        response so the extinction correction can use it. `None` if
        unknown.

    Returns
    -------
    response : `InstrumentResponse`
        The fitted response, valid only over the range it was fitted.

    Raises
    ------
    InvalidArgumentError
        If the reference type is not bundled.
    ProcessingError
        If too few samples fall in the fitting range.
    """
    from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import _get_reference_templates

    templates = _get_reference_templates()
    if reference_type not in templates:
        raise InvalidArgumentError(f"No bundled reference spectrum for {reference_type!r}.")
    template_wavelength, template_flux = templates[reference_type]
    profile = (
        line_spread_profile if line_spread_profile is not None else load_line_spread_profile(camera_name)
    )
    if profile is not None:
        smoothed_template = blur_to_resolution_profile(template_wavelength, template_flux, profile)
    else:
        smoothed_template = gaussian_filter1d(
            template_flux,
            blur_sigma_in_samples(
                resolution_element_angstrom, float(np.median(np.diff(template_wavelength)))
            ),
        )

    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    low = max(float(wavelength_range_angstrom[0]), float(template_wavelength.min()))
    high = min(float(wavelength_range_angstrom[1]), float(template_wavelength.max()))
    usable = (
        np.isfinite(intensity)
        & (intensity > 0)
        & (wavelength_angstrom >= low)
        & (wavelength_angstrom <= high)
    )
    for line, half_width in zip(
        _LINES_TO_SKIP_ANGSTROM,
        line_skip_half_widths_angstrom(profile, resolution_element_angstrom),
        strict=True,
    ):
        usable &= np.abs(wavelength_angstrom - line) > half_width
    if usable.sum() < 50:
        raise ProcessingError("Too few usable samples to fit an instrument response.")

    ratio = intensity[usable] / np.interp(wavelength_angstrom[usable], template_wavelength, smoothed_template)
    scaled = (wavelength_angstrom[usable] - _WAVELENGTH_CENTER_ANGSTROM) / _WAVELENGTH_SCALE_ANGSTROM
    coefficients = np.polyfit(scaled, np.log(ratio), _RESPONSE_POLYNOMIAL_DEGREE)
    return InstrumentResponse(
        camera_name=camera_name,
        coefficients=tuple(float(value) for value in coefficients),
        minimum_wavelength_angstrom=low,
        maximum_wavelength_angstrom=high,
        reference_type=reference_type,
        source=source,
        reference_airmass=reference_airmass,
    )
