"""The instrument's resolution element: how much it blurs every spectrum.

A slitless grism spectrum is blurred. Each star is a small disc on the
sensor, not a point, and the grating spreads that disc out into a trail.
So every sharp feature in the star's light, such as an absorption line,
comes out as a wide, shallow dip. The width of that blur, in Angstroms, is
called the resolution element. Anything narrower than it cannot be seen,
and two samples closer together than it are not independent measurements.

Three parts of the pipeline need this same number:

* the classifier blurs the reference spectra to it before comparing them
  with an observation (`spectral_classifier`),
* the instrument response is fitted against a reference spectrum blurred
  to it (`instrument_response`),
* the feature detector blurs its reference spectra to it and uses it to
  count independent measurements (`spectral_feature_detector`).

They all get it from here, so they can never disagree about how blurry the
instrument is.

Where the number comes from: the trail's own width. The extractor already
measures how wide each spectrum's trail is across the dispersion. A
slitless star is close to round (Vega's zero-order star is 4.39 pixels
wide in x and 4.45 in y), so the blur along the spectrum is taken to be the
same as the width across it. Multiplied by how many Angstroms one pixel
covers, that gives the resolution element for this spectrum, on this night,
with this focus. When no trail width is available, a fixed fallback is used.

The result is one typical number for the whole spectrum, not a value at
each wavelength. The blur is not quite the same everywhere along Vega's
trail: the blue half gives 41 A, the middle 38 A and the red half 52 A.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# 45 A: the resolution element to use when the trail width was not
# measured (for example with the fixed-box extraction method, or when too
# few width fits worked). It was measured on the Vega master stack
# (ASI533MM Pro, 405 mm, 200 lines/mm grating, 2026-09-19; see
# logs/spectral_resolution_estimate_vega_20260919.json). One pixel covers
# 11.0-11.4 A. Vega's zero-order star is 4.4 pixels wide (FWHM), about
# 48 A, and the spectrum's own trail is 3.2-3.9 pixels wide across the
# middle of the spectrum, or 35-43 A, so 45 A sits inside both. As a
# separate check, the reference A0V spectrum had to be blurred by 30-60 A
# (depending on the line) before its Balmer line depths matched Vega's.
# The value used before this was 30 A. It was never measured: it came from
# assuming a resolving power of about 150 (5000 / 150 = 33 A), and it made
# the expected line depths too deep (H-beta 19% instead of 14%).
#
# Caveats: one star on one stack. The pixel size and grating fix only the
# Angstroms per pixel. The star's blurred size (seeing, focus, the grating
# in a converging beam) is not in the equipment specs, which is why the
# real value is estimated from the trail width whenever it can be.
FALLBACK_RESOLUTION_ELEMENT_ANGSTROM = 45.0

# A gaussian's full width at half its height (FWHM) is this many times its
# sigma: 2 * sqrt(2 * ln 2) = 2.3548. The trail width is stored as a sigma,
# and a resolution element is quoted as a FWHM.
FWHM_PER_SIGMA = 2.355

# The resolution element is never allowed below this many pixels. A
# spectrum is sampled once per pixel, and it takes at least two samples to
# show one feature (the sampling limit, also called the Nyquist limit), so
# nothing measured from pixels can be sharper than that, whatever a noisy
# width fit says.
MINIMUM_RESOLUTION_ELEMENT_PIXELS = 2.0

# The fewest trail-width fits we need before trusting their median. Below
# this a handful of noisy fits could set the number. Chosen to be about a
# tenth of a typical trail (Vega's has 563 samples). Not yet tested against
# a set of faint stars.
MINIMUM_FITTED_TRAIL_WIDTH_SAMPLES = 50

# The fraction of the trail's samples that must have a working width fit.
# A width fit that failed is stored as 0.0. A faint star's fits often fail,
# and the ones that "work" on pure noise scatter widely, so when most of
# the trail failed the surviving fits are not a fair sample. Half is a
# deliberately plain cutoff. On the Vega master stack every one of the 563
# samples had a working fit, and throwing away a random 30% or 50% of them
# moved the estimate by under 1% (43.3 A to 43.8 A and 43.5 A; 60% left
# too few, so the fallback was used). Random failures are the easy case,
# though: real faint-star failures are not random, and this has not been
# tested on a set of faint stars (2026-09-19, scratch check on the Vega
# stack; see logs/spectral_resolution_estimate_vega_20260919.json).
MINIMUM_FITTED_TRAIL_WIDTH_FRACTION = 0.5


def estimate_resolution_element_angstrom(
    wavelength_angstrom: np.ndarray, trail_width_px: np.ndarray | list[float] | None
) -> float | None:
    """Estimate the resolution element from a spectrum's own trail width.

    The trail width is the sigma of the star's cross-section across the
    dispersion, measured at every step along the spectrum. Its median,
    turned into a FWHM and multiplied by the Angstroms each step covers,
    is the resolution element. The median is used so that the wide, noisy
    fits at the blue and red ends of the trail do not decide the answer.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms, one per step along the
        trail.
    trail_width_px : `numpy.ndarray` or `list` [`float`] or `None`
        The trail's sigma in pixels at each step, lined up one to one with
        `wavelength_angstrom`. `0.0` marks a step whose fit failed.

    Returns
    -------
    resolution_element_angstrom : `float` or `None`
        The estimate, or `None` when the widths are missing, do not line up
        with the wavelengths, or too few of them are usable.
    """
    if trail_width_px is None:
        return None
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    widths = np.asarray(trail_width_px, dtype=float)
    if wavelengths.ndim != 1 or widths.shape != wavelengths.shape or wavelengths.size < 2:
        return None

    fitted = np.isfinite(widths) & (widths > 0)
    if fitted.sum() < MINIMUM_FITTED_TRAIL_WIDTH_SAMPLES:
        return None
    if fitted.mean() < MINIMUM_FITTED_TRAIL_WIDTH_FRACTION:
        return None

    finite_wavelengths = wavelengths[np.isfinite(wavelengths)]
    if finite_wavelengths.size < 2:
        return None
    # Each step along the trail is one pixel, so the gap between
    # neighbouring wavelengths is how many Angstroms one pixel covers.
    angstrom_per_pixel = float(np.median(np.abs(np.diff(np.sort(finite_wavelengths)))))
    if angstrom_per_pixel <= 0:
        return None

    blur_width_px = max(FWHM_PER_SIGMA * float(np.median(widths[fitted])), MINIMUM_RESOLUTION_ELEMENT_PIXELS)
    return blur_width_px * angstrom_per_pixel


def resolve_resolution_element_angstrom(
    wavelength_angstrom: np.ndarray, trail_width_px: np.ndarray | list[float] | None
) -> tuple[float, bool]:
    """Give the resolution element to use for one spectrum.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    trail_width_px : `numpy.ndarray` or `list` [`float`] or `None`
        The trail's sigma in pixels at each step, when known.

    Returns
    -------
    resolution_element_angstrom : `float`
        The estimate from the trail width, or
        `FALLBACK_RESOLUTION_ELEMENT_ANGSTROM` when it could not be made.
    is_measured : `bool`
        `True` when the value came from the trail width, `False` when it
        is the fallback.
    """
    estimate = estimate_resolution_element_angstrom(wavelength_angstrom, trail_width_px)
    if estimate is None:
        return FALLBACK_RESOLUTION_ELEMENT_ANGSTROM, False
    logger.debug("Resolution element measured from the trail width: %.1f A", estimate)
    return estimate, True


def blur_sigma_in_samples(resolution_element_angstrom: float, sample_spacing_angstrom: float) -> float:
    """Turn a resolution element into a gaussian blur width, in samples.

    All three parts of the pipeline blur a reference spectrum to the
    instrument's resolution with a gaussian filter, which wants its width
    as a sigma counted in samples of the spectrum being blurred.

    Parameters
    ----------
    resolution_element_angstrom : `float`
        The resolution element (a FWHM), in Angstroms.
    sample_spacing_angstrom : `float`
        How many Angstroms apart the samples of the spectrum being blurred
        are.

    Returns
    -------
    sigma_samples : `float`
        The gaussian sigma, in samples.
    """
    return resolution_element_angstrom / FWHM_PER_SIGMA / sample_spacing_angstrom
