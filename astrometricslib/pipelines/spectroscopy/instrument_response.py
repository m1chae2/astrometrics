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

from astrometricslib.pipelines.spectroscopy.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    blur_sigma_in_samples,
)
from astrometricslib.utilities.camera_names import normalize_camera_name

_DATA_DIR = Path(__file__).parent / "data"

# Wavelengths of strong hydrogen lines and the Ca II H line, in Angstroms.
# The star's own dips at these places are not part of the instrument
# response, so the fit skips the samples around them.
_LINES_TO_SKIP_ANGSTROM = (3970.0, 4102.0, 4340.0, 4861.0, 6563.0)

# How far either side of each line the fit skips, in Angstroms. The
# wider Balmer wings reach about 100 A at this resolution, but skipping
# 120 A around the blue lines left the fit unconstrained below 4300 A and
# it swung to a wrong value at the blue end. 60 A keeps the fit anchored
# and leaves only the far wings, which the smooth polynomial ignores.
_LINE_SKIP_HALF_WIDTH_ANGSTROM = 60.0

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
# sensitivity did not fix that. Deriving the response over the full camera
# range is supported (see `derive_instrument_response`) and may suit a
# setup with a blocking filter that removes second-order light.
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
    """

    camera_name: str
    coefficients: tuple[float, ...]
    minimum_wavelength_angstrom: float
    maximum_wavelength_angstrom: float
    reference_type: str
    source: str

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


def derive_instrument_response(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    reference_type: str,
    camera_name: str,
    source: str,
    wavelength_range_angstrom: tuple[float, float] = DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
) -> InstrumentResponse:
    """Fit the response from an observation of a star of known type.

    The reference spectrum is much sharper than a slitless grism spectrum,
    so it is blurred to the instrument's resolution before the two are
    compared. Blurring it to the wrong width would leave a mismatch around
    every line, and the fit would wrongly treat that as part of the
    instrument's response.

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
        How much the instrument blurs this observation, in Angstroms.
        Defaults to `FALLBACK_RESOLUTION_ELEMENT_ANGSTROM`; pass the
        observation's own measured value when there is one (see
        `spectral_resolution`).

    Returns
    -------
    response : `InstrumentResponse`
        The fitted response, valid only over the range it was fitted.

    Raises
    ------
    ValueError
        If the reference type is not bundled or too few samples fall in
        the fitting range.
    """
    from astrometricslib.pipelines.spectroscopy.spectral_classifier import _get_reference_templates

    templates = _get_reference_templates()
    if reference_type not in templates:
        raise ValueError(f"No bundled reference spectrum for {reference_type!r}.")
    template_wavelength, template_flux = templates[reference_type]
    smoothed_template = gaussian_filter1d(
        template_flux,
        blur_sigma_in_samples(resolution_element_angstrom, float(np.median(np.diff(template_wavelength)))),
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
    for line in _LINES_TO_SKIP_ANGSTROM:
        usable &= np.abs(wavelength_angstrom - line) > _LINE_SKIP_HALF_WIDTH_ANGSTROM
    if usable.sum() < 50:
        raise ValueError("Too few usable samples to fit an instrument response.")

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
    )
