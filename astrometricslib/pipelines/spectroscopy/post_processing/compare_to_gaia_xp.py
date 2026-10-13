"""Checks a calibrated spectrum against the star's Gaia DR3 XP spectrum.

The Gaia satellite measured a low-resolution spectrum (the "XP" spectrum) for
about 220 million stars. Its resolving power is about 30 to 100, close to that
of a Star Analyser grating, and it is calibrated by a different instrument
above the atmosphere. Comparing our response-corrected spectrum with it is an
independent test of three things at once: the instrument response, the
airmass (extinction) correction and the wavelength scale. A disagreement does
not say which of the three is wrong, but the pattern helps: a straight tilt
of the ratio points at the response or the extinction, a shift of the
features points at the wavelength scale.

What is compared
----------------
The pipeline's response-corrected spectrum is the QE-corrected counts divided
by the instrument response, with the airmass correction applied
(`pre_processing.instrument_response` and
`pre_processing.atmospheric_extinction`).
The response is fitted as (Vega observed) / (Pickles A0V template), and the
Pickles templates are in ``nflam``: energy flux per unit wavelength, normalised
at 5556 Angstroms. Dividing by the response therefore turns counts into energy
flux per unit wavelength, F_lambda, up to one unknown constant (the constant
is the star's brightness against Vega's, which the pipeline does not track).
The spectrum is, in that sense, a relative F_lambda above the atmosphere.

Gaia's sampled XP flux is also F_lambda: energy per second per square metre per
nanometre, in W m^-2 nm^-1. The two quantities differ only by a constant
factor, which cancels when both spectra are divided by their median over
5400-5600 Angstroms. No conversion between photons and energy, and none
between per-nanometre and per-Angstrom, is needed. One caveat comes from the
response itself: it assumes Vega's true shape is the Pickles A0V template, so
any difference between the two is inside every response-corrected spectrum
and shows up here as a tilt or a band offset common to all stars.

Resolution
----------
The two spectra do not have the same resolution, and a sharp line in one
looks like a residual if the other has smoothed it away. Wherever XP is
the broader of the two, the observed spectrum is blurred up to XP's
resolution. Wherever the instrument is broader, XP is blurred up to the
instrument's line spread (the stored profile in
`pre_processing.spectral_resolution`). The blur is a Gaussian whose full
width at half maximum (FWHM) is the quadrature difference of the two,
``sqrt(broad**2 - narrow**2)``, so the result has the broader resolution.

XP's own resolution is `XP_RESOLUTION_FWHM_NODES_ANGSTROM`. It was measured on
2026-10-10 with 12 Gaia A0V stars (G 6.0 to 6.6): for each, the Pickles A0V
template was blurred by a Gaussian and fitted, with a quadratic continuum, to
XP in a window around one hydrogen line, and the blur that fitted best was
kept. The median over the stars was 95 Angstroms at H-gamma (4340), 130 at
H-beta (4861), 70 at H-alpha (6563) and 185 at the Paschen lines (8800). The
spread between stars was 20 to 40 percent, and a mismatch between a star
and the template makes the numbers slightly too large. The instrument is
sharper than XP below about 5000 Angstroms and broader above it, so both
directions of blurring matter.

Method
------
1. Keep the observed samples between 4200 and 8000 Angstroms outside the
   atmospheric bands (`pre_processing.atmospheric_mask`).
2. Put both spectra on a 5 Angstrom grid, blur as above, and read both at the
   observed wavelengths.
3. Divide each by its median over 5400-5600 Angstroms.
4. From the ratio r = observed / XP: the RMS of r - 1; a straight line of r
   against wavelength, weighted by the errors, whose slope is the tilt; the
   median and RMS in four bands; and the wavelength shift that best
   correlates the two spectra's logarithmic derivatives (d ln F / d lambda),
   which ignores brightness and any smooth tilt and responds to where the
   features are.

Run level
---------
`summarize_gaia_xp` takes the per-spectrum records of a run and gives, for each
band, the median ratio over the compared stars and its scatter. That median
ratio is the measured residual instrument response: dividing the
response-corrected spectra by it would make them agree with Gaia.
`gaia_xp_gate` fails the run when the median absolute tilt of the stars is
above the designed limit.

The limits are designed values, not fitted ones. They have not been checked
against real spectra yet; `scripts/compare_spectra_with_gaia_xp.py` is how the
owner measures that.
"""

import logging
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import gaussian_filter1d

from astrometricslib.drivers.interfaces.gaia_xp_driver import GaiaXpDriver
from astrometricslib.models.gaia_xp_comparison import (
    GaiaXpBandResidual,
    GaiaXpBandSummary,
    GaiaXpComparison,
    GaiaXpRunSummary,
)
from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.spectroscopy_quality import StageQualityMetric, metric
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_mask import atmospheric_band_mask
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    ResolutionProfile,
    blur_to_resolution_profile,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

GAIA_XP_GATE_NAME = "gaia_xp_agreement"

# The wavelength range compared, in Angstroms. It is the range the instrument
# response is fitted over (`DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM`); the
# response-corrected spectrum is NaN outside it.
COMPARISON_RANGE_ANGSTROM = (4200.0, 8000.0)

# Both spectra are scaled to have the same median over this window, in
# Angstroms. It sits near the peak of the response and holds no strong line
# of an A, F or G star, so the median is steady.
NORMALIZATION_WINDOW_ANGSTROM = (5400.0, 5600.0)

# The wavelength the straight-line slope is quoted at: the middle of the
# normalisation window. The ratio is 1 there by construction, so the slope is
# in percent of the flux at 5500 Angstroms per 1000 Angstroms.
SLOPE_REFERENCE_ANGSTROM = 5500.0

# The four bands the residuals are reported in, in Angstroms.
COMPARISON_BANDS_ANGSTROM = (
    (4200.0, 5000.0),
    (5000.0, 6000.0),
    (6000.0, 7000.0),
    (7000.0, 8000.0),
)

# XP's resolution, as a Gaussian FWHM in Angstroms at a few wavelengths;
# values between them are linear, and beyond them constant. Measured on
# 2026-10-10 on 12 A0V stars against the Pickles A0V template; see the module
# description.
XP_RESOLUTION_FWHM_NODES_ANGSTROM = (
    (4340.0, 95.0),
    (4861.0, 130.0),
    (6563.0, 70.0),
    (8800.0, 185.0),
)

# Spacing of the grid the spectra are blurred on, in Angstroms. It is finer
# than XP's own 20 Angstrom sampling and the instrument's 11 Angstrom pixels.
GRID_STEP_ANGSTROM = 5.0

# How far beyond the compared range the grid reaches, in Angstroms, so the
# blur at the ends of the range has data to use. Observed values beyond the
# range are held at the nearest valid value (the response is NaN there).
GRID_PADDING_ANGSTROM = 400.0

# A blur narrower than this FWHM, in Angstroms, is skipped. It is a
# floor, too: the gaussian filter cannot take a zero width.
MINIMUM_BLUR_FWHM_ANGSTROM = 1.0

# The fewest samples the comparison needs, and the fewest a band needs.
MINIMUM_COMPARED_SAMPLES = 30
MINIMUM_BAND_SAMPLES = 3

# The error floor on the ratio at every sample, as a fraction. XP's absolute
# calibration is good to about 1 to 2 percent, and a line our blur does not
# match leaves a residual far above the formal errors. Without a floor the
# few samples with tiny errors would decide the straight-line fit. Designed.
RATIO_ERROR_FLOOR_FRACTION = 0.01

# The wavelength shift is searched for within this many Angstroms either
# way. Five pixels of dispersion, far more than a bad calibration should give.
MAXIMUM_SEARCH_SHIFT_ANGSTROM = 60.0

# The logarithmic derivatives are high-pass filtered: a Gaussian running mean
# of this sigma, in Angstroms, is subtracted, so a smooth tilt or curvature of
# the ratio does not drive the correlation. It is wider than the features
# that carry the shift (about 100 Angstroms) and narrower than the continuum.
SHIFT_HIGH_PASS_SIGMA_ANGSTROM = 400.0

# Both spectra are smoothed by a Gaussian of this sigma, in Angstroms, before
# the logarithmic derivative is taken. Differencing amplifies noise, and a
# derivative of the noisy observed spectrum correlated poorly with XP's.
# Smoothing both the same way does not move a feature, so the shift is
# unchanged; 25 Angstroms (a FWHM of 59) is a little more than the sample
# spacing and well below the resolution of both spectra.
SHIFT_SMOOTHING_SIGMA_ANGSTROM = 25.0

# A correlation peak below this is too weak to give a shift. Two spectra of
# the same star correlate at 0.9 or more in the synthetic tests; the value is
# designed, a point well above chance for about 700 independent points
# (about 0.04), not yet checked against real spectra.
MINIMUM_SHIFT_CORRELATION = 0.3

# The designed limits of the checkpoint metrics and of the run gate.
#
# 0.05: the whole-spectrum scatter of observed / XP. About the size of the
#   instrument-response errors the owner would want to find (a few percent),
#   and above XP's own 1 to 2 percent calibration.
# 3: the tilt, in percent per 1000 Angstroms. Over the 3800 Angstroms compared
#   it is a 11 percent change from end to end; it is also about the size of the
#   airmass error the extinction correction makes at 0.3 airmass.
# 11: the wavelength shift, in Angstroms. One pixel of dispersion
#   (11.0 to 11.4 Angstroms, see `spectral_resolution`).
GAIA_XP_RESIDUAL_RMS_LIMIT = 0.05
GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM = 3.0
GAIA_XP_WAVELENGTH_SHIFT_LIMIT_ANGSTROM = 11.0

# The run gate needs at least this many compared stars.
MINIMUM_STARS_FOR_GATE = 3

REASON_NO_SOURCE_ID = "no Gaia DR3 source id is known for this star"
REASON_NO_SPECTRUM = (
    "the star has no response-corrected spectrum (the camera has no stored instrument response)"
)
REASON_NO_XP = "Gaia DR3 has no XP spectrum for this source, or it could not be downloaded"


def xp_resolution_fwhm_angstrom(wavelength_angstrom: np.ndarray) -> np.ndarray:
    """Give XP's resolution at some wavelengths.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        Wavelengths, in Angstroms.

    Returns
    -------
    fwhm_angstrom : `numpy.ndarray`
        The Gaussian full width at half maximum that matches XP's blur,
        from `XP_RESOLUTION_FWHM_NODES_ANGSTROM`.
    """
    nodes = np.array(XP_RESOLUTION_FWHM_NODES_ANGSTROM)
    return np.interp(wavelength_angstrom, nodes[:, 0], nodes[:, 1])


def _not_checked(reason: str, source_id: int | None) -> GaiaXpComparison:
    """Build the record for a comparison that could not run.

    Parameters
    ----------
    reason : `str`
        Why the check did not run.
    source_id : `int` or `None`
        The Gaia id, when known.

    Returns
    -------
    comparison : `GaiaXpComparison`
        A ``not_checked`` record with the reason and no numbers.
    """
    return GaiaXpComparison(status="not_checked", not_checked_reason=reason, gaia_source_id=source_id)


def _blur_by_kernel(
    grid_angstrom: np.ndarray, flux: np.ndarray, kernel_fwhm_angstrom: np.ndarray
) -> np.ndarray:
    """Blur a spectrum by a Gaussian whose width changes with wavelength.

    Parameters
    ----------
    grid_angstrom : `numpy.ndarray`
        Evenly spaced wavelengths, in Angstroms.
    flux : `numpy.ndarray`
        The spectrum on that grid.
    kernel_fwhm_angstrom : `numpy.ndarray`
        The FWHM of the blur at each grid point, in Angstroms. Zero means no
        blur.

    Returns
    -------
    blurred : `numpy.ndarray`
        The blurred spectrum. The input itself when no point needs a blur.
    """
    if float(kernel_fwhm_angstrom.max()) < MINIMUM_BLUR_FWHM_ANGSTROM:
        return flux
    floored = np.maximum(kernel_fwhm_angstrom, MINIMUM_BLUR_FWHM_ANGSTROM)
    return blur_to_resolution_profile(grid_angstrom, flux, ResolutionProfile(grid_angstrom, floored))


def _kernel_widths(
    grid_angstrom: np.ndarray, line_spread: ResolutionProfile | None, fallback_angstrom: float
) -> tuple[np.ndarray, np.ndarray]:
    """Work out how much to blur each spectrum to match their resolutions.

    Parameters
    ----------
    grid_angstrom : `numpy.ndarray`
        The wavelengths, in Angstroms.
    line_spread : `ResolutionProfile` or `None`
        The instrument's line spread, or `None` to use one width everywhere.
    fallback_angstrom : `float`
        The instrument's line spread to use when there is no profile.

    Returns
    -------
    observed_kernel, xp_kernel : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The FWHM of the blur to apply to the observed spectrum and to the XP
        spectrum at each wavelength. At each wavelength one of them is zero.
    """
    instrument = (
        line_spread.at(grid_angstrom)
        if line_spread is not None
        else np.full(grid_angstrom.shape, float(fallback_angstrom))
    )
    xp = xp_resolution_fwhm_angstrom(grid_angstrom)
    observed_kernel = np.sqrt(np.maximum(xp**2 - instrument**2, 0.0))
    xp_kernel = np.sqrt(np.maximum(instrument**2 - xp**2, 0.0))
    return observed_kernel, xp_kernel


def _wavelength_shift(
    grid_angstrom: np.ndarray,
    observed: np.ndarray,
    xp: np.ndarray,
    usable: np.ndarray,
) -> tuple[float | None, float | None]:
    """Find the wavelength shift between two spectra on the same grid.

    The shift is the lag that best correlates the two spectra's logarithmic
    derivatives, d ln F / d lambda, after a smooth trend is removed from each.
    A logarithmic derivative does not depend on brightness, and a tilt of the
    ratio only adds a smooth trend to it.

    Parameters
    ----------
    grid_angstrom : `numpy.ndarray`
        Evenly spaced wavelengths, in Angstroms.
    observed, xp : `numpy.ndarray`
        The two spectra on the grid, already at the same resolution.
    usable : `numpy.ndarray`
        `True` where the comparison may use the grid point (inside the
        compared range and the observed spectrum, outside the atmospheric
        bands).

    Returns
    -------
    shift_angstrom, correlation : `tuple`
        The shift (positive when the observed features sit at longer
        wavelengths than XP's) and the correlation at the peak. Both `None`
        when there are too few usable points. The shift is `None` alone when
        the peak is weaker than `MINIMUM_SHIFT_CORRELATION` or sits at the edge
        of the search range.
    """

    def high_pass_log_derivative(spectrum: np.ndarray) -> np.ndarray:
        """Take the trend-free logarithmic derivative of a spectrum.

        Returns
        -------
        derivative : `numpy.ndarray`
            d ln F / d lambda on the grid, with the running mean removed.
            NaN where the spectrum is not positive.
        """
        positive = spectrum > 0
        logarithm = np.full(spectrum.shape, np.nan)
        logarithm[positive] = np.log(spectrum[positive])
        # Fill the gaps (atmospheric bands) by a straight line so the
        # derivative and the filter run on a continuous curve.
        good = np.isfinite(logarithm) & usable
        if good.sum() < 10:
            return np.full(spectrum.shape, np.nan)
        filled = np.interp(grid_angstrom, grid_angstrom[good], logarithm[good])
        smoothed = gaussian_filter1d(
            filled, SHIFT_SMOOTHING_SIGMA_ANGSTROM / GRID_STEP_ANGSTROM, mode="nearest"
        )
        derivative = np.gradient(smoothed, grid_angstrom)
        sigma = SHIFT_HIGH_PASS_SIGMA_ANGSTROM / GRID_STEP_ANGSTROM
        return derivative - gaussian_filter1d(derivative, sigma, mode="nearest")

    observed_derivative = high_pass_log_derivative(observed)
    xp_derivative = high_pass_log_derivative(xp)
    maximum_lag = round(MAXIMUM_SEARCH_SHIFT_ANGSTROM / GRID_STEP_ANGSTROM)
    lags = np.arange(-maximum_lag, maximum_lag + 1)
    correlations = np.full(lags.shape, np.nan)
    count = usable.size
    for position, lag in enumerate(lags):
        # A feature at lambda + shift in the observed spectrum is at lambda in
        # XP, so observed[i] is compared with xp[i - lag].
        index = np.arange(max(lag, 0), min(count, count + lag))
        shifted = index - lag
        keep = usable[index] & usable[shifted]
        keep &= np.isfinite(observed_derivative[index]) & np.isfinite(xp_derivative[shifted])
        if keep.sum() < 20:
            continue
        a = observed_derivative[index][keep]
        b = xp_derivative[shifted][keep]
        denominator = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
        if denominator > 0:
            correlations[position] = float(np.sum(a * b) / denominator)
    if not np.isfinite(correlations).any():
        return None, None
    peak = int(np.nanargmax(correlations))
    peak_correlation = float(correlations[peak])
    if peak_correlation < MINIMUM_SHIFT_CORRELATION or peak in (0, lags.size - 1):
        return None, peak_correlation
    left, centre, right = correlations[peak - 1], correlations[peak], correlations[peak + 1]
    # The vertex of the parabola through the peak and its two neighbours.
    curvature = left - 2.0 * centre + right
    offset = 0.5 * (left - right) / curvature if np.isfinite(curvature) and curvature < 0 else 0.0
    return float((lags[peak] + offset) * GRID_STEP_ANGSTROM), peak_correlation


def _straight_line_fit(x: np.ndarray, y: np.ndarray, sigma: np.ndarray) -> tuple[float, float, float]:
    """Fit y = a + b x by weighted least squares.

    Parameters
    ----------
    x, y : `numpy.ndarray`
        The points.
    sigma : `numpy.ndarray`
        Each point's one-sigma error.

    Returns
    -------
    intercept, slope, slope_error : `tuple` [`float`, `float`, `float`]
        The fit and the slope's error. The error is scaled up by the square
        root of the reduced chi-squared when that is above 1, because the
        residuals of a spectrum are lines and tilts, not noise.
    """
    weight = 1.0 / sigma**2
    normal = np.array([[weight.sum(), (weight * x).sum()], [(weight * x).sum(), (weight * x * x).sum()]])
    right_hand_side = np.array([(weight * y).sum(), (weight * x * y).sum()])
    covariance = np.linalg.inv(normal)
    intercept, slope = covariance @ right_hand_side
    chi_squared = float(np.sum(weight * (y - intercept - slope * x) ** 2))
    scale = max(1.0, chi_squared / max(x.size - 2, 1))
    return float(intercept), float(slope), float(np.sqrt(covariance[1, 1] * scale))


def compare_spectrum_to_xp(
    wavelength_angstrom: Sequence[float] | np.ndarray,
    intensity: Sequence[float] | np.ndarray,
    xp_spectrum: tuple[np.ndarray, np.ndarray, np.ndarray],
    *,
    gaia_source_id: int | None = None,
    intensity_errors: Sequence[float] | np.ndarray | None = None,
    line_spread: ResolutionProfile | None = None,
    fallback_resolution_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
) -> GaiaXpComparison:
    """Compare a response-corrected spectrum with a Gaia XP spectrum.

    Parameters
    ----------
    wavelength_angstrom : `array-like`
        The spectrum's wavelengths, in Angstroms.
    intensity : `array-like`
        The response-corrected brightness, in any units that are proportional
        to F_lambda. NaN where the response is not valid.
    xp_spectrum : `tuple` [`numpy.ndarray`, `numpy.ndarray`, `numpy.ndarray`]
        The XP wavelengths (Angstroms), flux (W m^-2 nm^-1) and flux error,
        as a `GaiaXpDriver` gives them.
    gaia_source_id : `int`, optional
        The Gaia source id, stored in the record.
    intensity_errors : `array-like`, optional
        The one-sigma errors of `intensity`, in the same units. When given,
        they join XP's errors in weighting the straight-line fit.
    line_spread : `ResolutionProfile`, optional
        The instrument's line spread (FWHM, Angstroms) at each wavelength.
    fallback_resolution_angstrom : `float`, optional
        The instrument's line spread to use at every wavelength when
        `line_spread` is `None`.

    Returns
    -------
    comparison : `GaiaXpComparison`
        The result. ``status`` is ``"not_checked"`` with a reason when fewer
        than `MINIMUM_COMPARED_SAMPLES` samples can be compared or the
        normalisation window holds no usable sample.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    brightness = np.asarray(intensity, dtype=float)
    errors = None if intensity_errors is None else np.asarray(intensity_errors, dtype=float)
    if errors is not None and errors.shape != brightness.shape:
        errors = None
    xp_wavelength, xp_flux, xp_flux_error = (np.asarray(array, dtype=float) for array in xp_spectrum)

    low, high = COMPARISON_RANGE_ANGSTROM
    in_range = np.isfinite(wavelength) & np.isfinite(brightness) & (wavelength >= low) & (wavelength <= high)
    clear_of_air = in_range & ~atmospheric_band_mask(wavelength)
    if int(clear_of_air.sum()) < MINIMUM_COMPARED_SAMPLES:
        return _not_checked(
            f"fewer than {MINIMUM_COMPARED_SAMPLES} usable samples "
            f"between {low:.0f} and {high:.0f} Angstroms",
            gaia_source_id,
        )
    order = np.argsort(wavelength[in_range])
    in_range_wavelength = wavelength[in_range][order]
    in_range_brightness = brightness[in_range][order]
    # The observed spectrum on the grid. The atmospheric bands are replaced by
    # a straight line across them, so they do not smear into the blur, and the
    # ends are held at the nearest value.
    grid = np.arange(
        low - GRID_PADDING_ANGSTROM,
        high + GRID_PADDING_ANGSTROM + 0.5 * GRID_STEP_ANGSTROM,
        GRID_STEP_ANGSTROM,
    )
    clear_of_air_in_order = ~atmospheric_band_mask(in_range_wavelength)
    observed_grid = np.interp(
        grid, in_range_wavelength[clear_of_air_in_order], in_range_brightness[clear_of_air_in_order]
    )
    xp_grid = PchipInterpolator(xp_wavelength, xp_flux, extrapolate=True)(grid)

    observed_kernel, xp_kernel = _kernel_widths(grid, line_spread, fallback_resolution_angstrom)
    observed_was_blurred = bool(
        float(observed_kernel[(grid >= low) & (grid <= high)].max()) >= MINIMUM_BLUR_FWHM_ANGSTROM
    )
    xp_was_blurred = bool(
        float(xp_kernel[(grid >= low) & (grid <= high)].max()) >= MINIMUM_BLUR_FWHM_ANGSTROM
    )
    observed_blurred = _blur_by_kernel(grid, observed_grid, observed_kernel)
    xp_blurred = _blur_by_kernel(grid, xp_grid, xp_kernel)

    # Read both at the observed samples that are inside the range and clear of
    # the atmospheric bands.
    sample_wavelength = wavelength[clear_of_air]
    observed_at_samples = np.interp(sample_wavelength, grid, observed_blurred)
    xp_at_samples = np.interp(sample_wavelength, grid, xp_blurred)
    xp_error_at_samples = np.interp(sample_wavelength, xp_wavelength, xp_flux_error)
    window_low, window_high = NORMALIZATION_WINDOW_ANGSTROM
    in_window = (sample_wavelength >= window_low) & (sample_wavelength <= window_high)
    if int(in_window.sum()) < MINIMUM_BAND_SAMPLES:
        return _not_checked(
            f"fewer than {MINIMUM_BAND_SAMPLES} usable samples "
            f"between {window_low:.0f} and {window_high:.0f} Angstroms",
            gaia_source_id,
        )
    observed_level = float(np.median(observed_at_samples[in_window]))
    xp_level = float(np.median(xp_at_samples[in_window]))
    if not (observed_level > 0 and xp_level > 0):
        return _not_checked("a spectrum is not positive in the normalisation window", gaia_source_id)

    usable = (xp_at_samples > 0) & (observed_at_samples > 0)
    sample_wavelength = sample_wavelength[usable]
    ratio = (observed_at_samples[usable] / observed_level) / (xp_at_samples[usable] / xp_level)
    if sample_wavelength.size < MINIMUM_COMPARED_SAMPLES:
        return _not_checked(
            f"fewer than {MINIMUM_COMPARED_SAMPLES} positive samples to compare", gaia_source_id
        )

    # The ratio's one-sigma error: the errors of the two spectra added in
    # quadrature (as fractions of each), never below the error floor.
    relative_xp_error = xp_error_at_samples[usable] / xp_at_samples[usable]
    relative_observed_error = np.zeros(ratio.size)
    used_observed_errors = errors is not None
    if errors is not None:
        relative_observed_error = np.abs(errors[clear_of_air][usable] / observed_at_samples[usable])
        relative_observed_error = np.where(np.isfinite(relative_observed_error), relative_observed_error, 0.0)
    sigma = ratio * np.sqrt(relative_xp_error**2 + relative_observed_error**2 + RATIO_ERROR_FLOOR_FRACTION**2)

    scaled_wavelength = (sample_wavelength - SLOPE_REFERENCE_ANGSTROM) / 1000.0
    _intercept, slope, slope_error = _straight_line_fit(scaled_wavelength, ratio, sigma)

    bands = []
    for band_index, (band_start, band_end) in enumerate(COMPARISON_BANDS_ANGSTROM):
        last_band = band_index == len(COMPARISON_BANDS_ANGSTROM) - 1
        in_band = (sample_wavelength >= band_start) & (
            (sample_wavelength <= band_end) if last_band else (sample_wavelength < band_end)
        )
        band_ratio = ratio[in_band]
        enough = band_ratio.size >= MINIMUM_BAND_SAMPLES
        bands.append(
            GaiaXpBandResidual(
                start_angstrom=band_start,
                end_angstrom=band_end,
                median_ratio=float(np.median(band_ratio)) if enough else None,
                rms_fraction=float(np.sqrt(np.mean((band_ratio - 1.0) ** 2))) if enough else None,
                sample_count=int(band_ratio.size),
            )
        )

    in_grid_range = (grid >= low) & (grid <= high)
    covered = in_grid_range & (grid >= in_range_wavelength[0]) & (grid <= in_range_wavelength[-1])
    covered &= ~atmospheric_band_mask(grid)
    shift, shift_correlation = _wavelength_shift(grid, observed_blurred, xp_blurred, covered)

    return GaiaXpComparison(
        status="compared",
        gaia_source_id=gaia_source_id,
        sample_count=int(ratio.size),
        residual_rms_fraction=float(np.sqrt(np.mean((ratio - 1.0) ** 2))),
        slope_percent_per_1000_angstrom=100.0 * slope,
        slope_error_percent_per_1000_angstrom=100.0 * slope_error,
        wavelength_shift_angstrom=shift,
        wavelength_shift_correlation=shift_correlation,
        bands=bands,
        normalization_window_angstrom=[window_low, window_high],
        observed_normalization=observed_level,
        xp_normalization=xp_level,
        xp_was_blurred=xp_was_blurred,
        observed_was_blurred=observed_was_blurred,
        used_observed_errors=used_observed_errors,
    )


def gaia_xp_precheck(
    gaia_source_id: int | None, response_corrected_intensity: Sequence[float] | np.ndarray | None
) -> GaiaXpComparison | None:
    """Say early whether a star cannot be compared, before any download.

    Parameters
    ----------
    gaia_source_id : `int` or `None`
        The star's Gaia DR3 source id.
    response_corrected_intensity : `array-like` or `None`
        The star's response-corrected brightness.

    Returns
    -------
    not_checked : `GaiaXpComparison` or `None`
        A ``not_checked`` record with the reason when the star has no Gaia id
        or no response-corrected spectrum, else `None`, meaning the XP
        spectrum should be fetched.
    """
    if gaia_source_id is None:
        return _not_checked(REASON_NO_SOURCE_ID, None)
    if (
        response_corrected_intensity is None
        or not np.isfinite(np.asarray(response_corrected_intensity, dtype=float)).any()
    ):
        return _not_checked(REASON_NO_SPECTRUM, gaia_source_id)
    return None


def compare_to_gaia_xp(
    driver: GaiaXpDriver,
    gaia_source_id: int | None,
    wavelength_angstrom: Sequence[float] | np.ndarray,
    response_corrected_intensity: Sequence[float] | np.ndarray | None,
    *,
    intensity_errors: Sequence[float] | np.ndarray | None = None,
    line_spread: ResolutionProfile | None = None,
    fallback_resolution_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
) -> GaiaXpComparison:
    """Check one star's response-corrected spectrum against Gaia XP.

    This asks the driver for the XP spectrum, so it does no work, and does not
    touch the network, for a star with no known Gaia id or no
    response-corrected spectrum.

    Parameters
    ----------
    driver : `GaiaXpDriver`
        Where the XP spectrum comes from.
    gaia_source_id : `int` or `None`
        The star's Gaia DR3 source id (see
        `catalog_star_identity.gaia_dr3_source_id_of`).
    wavelength_angstrom : `array-like`
        The spectrum's wavelengths, in Angstroms.
    response_corrected_intensity : `array-like` or `None`
        The response-corrected brightness, or `None` when the camera has no
        stored instrument response.
    intensity_errors : `array-like`, optional
        The one-sigma errors of the brightness, when the pipeline has them.
    line_spread : `ResolutionProfile`, optional
        The instrument's line spread at each wavelength.
    fallback_resolution_angstrom : `float`, optional
        The line spread to use when there is no profile.

    Returns
    -------
    comparison : `GaiaXpComparison`
        The comparison, or a ``not_checked`` record that says why not.
    """
    not_checked = gaia_xp_precheck(gaia_source_id, response_corrected_intensity)
    if not_checked is not None:
        return not_checked
    xp_spectrum = driver.sampled_spectrum(gaia_source_id)
    if xp_spectrum is None:
        return _not_checked(REASON_NO_XP, gaia_source_id)
    try:
        return compare_spectrum_to_xp(
            wavelength_angstrom,
            response_corrected_intensity,
            xp_spectrum,
            gaia_source_id=gaia_source_id,
            intensity_errors=intensity_errors,
            line_spread=line_spread,
            fallback_resolution_angstrom=fallback_resolution_angstrom,
        )
    except DATA_ERRORS as comparison_error:
        # The check is an extra test; it must never stop a spectrum.
        logger.warning("Gaia XP comparison failed for source %s: %s", gaia_source_id, comparison_error)
        return _not_checked(f"the comparison failed: {comparison_error}", gaia_source_id)


def gaia_xp_metrics(comparison: GaiaXpComparison | None) -> list[StageQualityMetric]:
    """Build the Gaia XP metrics of quality checkpoint 3.

    Parameters
    ----------
    comparison : `GaiaXpComparison` or `None`
        The comparison, or `None` when none was made.

    Returns
    -------
    metrics : `list` [`StageQualityMetric`]
        Four metrics. ``gaia_xp_available`` is 1.0 when the spectrum was
        compared and 0.0 when not, with the reason as its note, and has no
        limit. The other three have no value when not compared. The slope and
        the shift are reported as absolute values, because their limits apply
        both ways; the signed values are in the comparison. All three limits
        are designed, not fitted (see the module description).
    """
    compared = comparison is not None and comparison.status == "compared"
    slope = comparison.slope_percent_per_1000_angstrom if compared else None
    shift = comparison.wavelength_shift_angstrom if compared else None
    rms = comparison.residual_rms_fraction if compared else None
    if comparison is None:
        availability_note = "the spectrum was not compared with Gaia XP"
    elif compared:
        availability_note = "compared with the star's Gaia DR3 XP spectrum"
    else:
        availability_note = comparison.not_checked_reason
    return [
        metric(
            "gaia_xp_residual_rms_fraction",
            rms,
            "fraction",
            limit=GAIA_XP_RESIDUAL_RMS_LIMIT,
            higher_is_better=False,
            note="RMS of observed / Gaia XP - 1 over 4200-8000 A; the limit is a designed value",
        ),
        metric(
            "gaia_xp_slope_percent_per_1000_angstrom",
            abs(slope) if slope is not None else None,
            "percent per 1000 A",
            limit=GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM,
            higher_is_better=False,
            note="absolute tilt of observed / Gaia XP; the limit is a designed value",
        ),
        metric(
            "gaia_xp_wavelength_shift_angstrom",
            abs(shift) if shift is not None else None,
            "angstrom",
            limit=GAIA_XP_WAVELENGTH_SHIFT_LIMIT_ANGSTROM,
            higher_is_better=False,
            note="absolute feature shift against Gaia XP; the limit, one pixel of dispersion, is designed",
        ),
        metric("gaia_xp_available", float(compared), "flag", note=availability_note),
    ]


def gaia_xp_rows(stellar_objects: Iterable[Any]) -> list[dict[str, Any]]:
    """Collect the compared spectra's Gaia XP records as plain dictionaries.

    A batch worker returns these to the parent process, which cannot receive
    whole star objects.

    Parameters
    ----------
    stellar_objects : `Iterable`
        Stars carrying a ``spectroscopy`` result (or `None`).

    Returns
    -------
    rows : `list` [`dict`]
        One entry per spectrum that was compared: the `GaiaXpComparison` as a
        dictionary plus ``"star_id"``.
    """
    rows = []
    for star in stellar_objects:
        spectroscopy = getattr(star, "spectroscopy", None)
        comparison = getattr(spectroscopy, "gaia_xp_comparison", None) if spectroscopy is not None else None
        if comparison is not None and comparison.status == "compared":
            rows.append({"star_id": str(getattr(star, "id", "")), **comparison.model_dump()})
    return rows


def _per_star_values(rows: Iterable[Mapping[str, Any]], read: Any) -> list[float]:
    """Give one value per star: the median over each star's spectra.

    Parameters
    ----------
    rows : `Iterable` [`Mapping`]
        The records from `gaia_xp_rows`.
    read : `Callable`
        Takes a record and gives a number or `None`.

    Returns
    -------
    values : `list` [`float`]
        One value per star that has at least one number. A record with no
        ``star_id`` counts as its own star.
    """
    by_star: dict[str, list[float]] = {}
    for position, row in enumerate(rows):
        value = read(row)
        if value is None:
            continue
        star_key = str(row.get("star_id") or f"unnamed-{position}")
        by_star.setdefault(star_key, []).append(float(value))
    return [float(statistics.median(values)) for values in by_star.values()]


def _star_count(rows: Sequence[Mapping[str, Any]]) -> int:
    """Count the stars in a run's Gaia XP records.

    Returns
    -------
    count : `int`
        The number of different stars, counting a record with no id as one.
    """
    return len({str(row.get("star_id") or f"unnamed-{position}") for position, row in enumerate(rows)})


def summarize_gaia_xp(rows: Iterable[Mapping[str, Any]]) -> GaiaXpRunSummary | None:
    """Roll a run's Gaia XP comparisons up into one record.

    Parameters
    ----------
    rows : `Iterable` [`Mapping`]
        The records from `gaia_xp_rows`, one per compared spectrum.

    Returns
    -------
    summary : `GaiaXpRunSummary` or `None`
        For each of the four bands, the median over the stars of the ratio
        observed / XP (the measured residual instrument response) and its
        scatter, plus the median signed and absolute tilt. Each star counts
        once, with the median of its own spectra. `None` when there are no
        records.
    """
    records = list(rows)
    if not records:
        return None
    band_summaries = []
    for band_index, (band_start, band_end) in enumerate(COMPARISON_BANDS_ANGSTROM):

        def band_ratio(row: Mapping[str, Any], index: int = band_index) -> float | None:
            """Read one record's median ratio in this band.

            Returns
            -------
            ratio : `float` or `None`
                The ratio, or `None` when the record has no value for the band.
            """
            bands = row.get("bands") or []
            return bands[index].get("median_ratio") if index < len(bands) else None

        ratios = _per_star_values(records, band_ratio)
        median_ratio = float(statistics.median(ratios)) if ratios else None
        scatter = (
            float(1.4826 * statistics.median(abs(value - median_ratio) for value in ratios))
            if median_ratio is not None and len(ratios) >= 2
            else None
        )
        band_summaries.append(
            GaiaXpBandSummary(
                start_angstrom=band_start,
                end_angstrom=band_end,
                median_ratio=median_ratio,
                scatter=scatter,
                star_count=len(ratios),
            )
        )
    slopes = _per_star_values(records, lambda row: row.get("slope_percent_per_1000_angstrom"))
    return GaiaXpRunSummary(
        compared_star_count=_star_count(records),
        bands=band_summaries,
        median_slope_percent_per_1000_angstrom=float(statistics.median(slopes)) if slopes else None,
        median_absolute_slope_percent_per_1000_angstrom=(
            float(statistics.median(abs(value) for value in slopes)) if slopes else None
        ),
    )


def gaia_xp_gate(rows: Iterable[Mapping[str, Any]]) -> GateResult:
    """Build the run's `gaia_xp_agreement` gate.

    Parameters
    ----------
    rows : `Iterable` [`Mapping`]
        The records from `gaia_xp_rows`.

    Returns
    -------
    gate : `GateResult`
        ``not_checked`` with fewer than `MINIMUM_STARS_FOR_GATE` compared
        stars. ``failed`` when the median absolute tilt of the compared stars
        is above `GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM`, which points
        at the instrument response or the airmass correction rather than at
        one star. ``passed`` otherwise.
    """
    source = (
        f"median absolute tilt of observed / Gaia XP across compared stars within "
        f"{GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM:.0f} percent per 1000 A (designed, not yet "
        f"checked against real spectra); needs at least {MINIMUM_STARS_FOR_GATE} stars"
    )
    summary = summarize_gaia_xp(rows)
    compared = summary.compared_star_count if summary is not None else 0
    if summary is None or compared < MINIMUM_STARS_FOR_GATE:
        return unchecked_gate(
            GAIA_XP_GATE_NAME,
            f"only {compared} star(s) were compared with Gaia XP; "
            f"at least {MINIMUM_STARS_FOR_GATE} are needed",
            source,
        )
    median_absolute_slope = summary.median_absolute_slope_percent_per_1000_angstrom
    if median_absolute_slope is None:
        return unchecked_gate(GAIA_XP_GATE_NAME, "no compared star had a measurable tilt", source)
    median_slope = summary.median_slope_percent_per_1000_angstrom
    detail = f"median tilt {median_slope:+.2f} percent per 1000 A over {compared} stars"
    if median_absolute_slope > GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM:
        return failed_gate(
            GAIA_XP_GATE_NAME,
            f"the spectra disagree with Gaia XP: {detail}",
            median_absolute_slope,
            GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM,
            source,
        )
    return passed_gate(
        GAIA_XP_GATE_NAME,
        median_absolute_slope,
        GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM,
        source,
        detail,
    )
