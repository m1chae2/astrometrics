"""Purpose: Measure the equivalent width of a spectral feature, with its error.

Description: The feature detector (`spectral_feature_detector`) reports how
deep a dip is at its core. Depth depends on how much the instrument blurred
the line, so it is hard to compare between spectra. The equivalent width does
not depend on the blur. It is the width, in Angstroms, of a rectangle that
reaches from zero up to the continuum and holds the same area as the line's
deficit:

    EW = integral of (1 - F / C) d(lambda)

Here `F` is the spectrum and `C` is the continuum, the brightness the
spectrum would have without the line. An absorption line has a positive EW
and an emission line a negative one. The EW of a Gaussian dip with depth `d`
(a fraction of the continuum) and standard deviation `s` is
``d * s * sqrt(2 * pi)``.

How `C` is found: the same way the feature detector finds it. A polynomial
of degree `CONTINUUM_POLYNOMIAL_DEGREE` (a quadratic) is fitted, without
weights, to two bands on either side of the feature. The detector starts its
bands 1.5 resolution elements from the line's center. The EW integrates over
1.5 line-spread widths (full widths at half maximum) either side, which in the
red is wider than 1.5 resolution elements because the blur grows with
wavelength. A band that starts inside the integration window would hold the
line's own wings, so the bands start at the window edge when that is farther
out than the detector's inner edge. Where the line spread equals the resolution
element (the blue end), the bands are exactly the detector's.

How the error is found, from two independent parts:

* The noise in the samples inside the window. Each sample `i` adds
  ``d(lambda_i) * sigma_i / C_i`` to the EW error, where `sigma_i` is the
  sample's 1-sigma error from the extractor (see `intensity_variance`).
* The error of the continuum. The polynomial's coefficients come from noisy
  samples, so they carry a covariance matrix. For an unweighted fit with
  independent sample errors, that matrix is ``(A^T A)^-1 A^T V A (A^T A)^-1``
  with `A` the matrix of powers of the wavelength and `V` the diagonal matrix
  of sample variances. The EW changes with coefficient `k` by
  ``g_k = sum(d(lambda_i) * F_i / C_i**2 * x_i**k)``, so the continuum part
  of the variance is ``g^T Cov g``.

The two parts add, because the band samples and the window samples are
different samples.

Three limits to keep in mind:

* The feature detector picks the center of the dip that is most significant
  within 30 Angstroms of the rest wavelength, and the EW is measured there.
  Choosing the center that way biases the EW of a faint line slightly high. A
  synthetic check with a Gaussian line of EW 9.6 Angstroms and an error of
  1.4 Angstroms gave a mean of 9.9 Angstroms (3 percent high) with the center
  chosen this way, and 9.4 Angstroms with the center fixed at the true line.
* The samples are treated as independent. The extractor blends two columns
  for most samples (see `intensity_variance`), so neighbouring samples are
  correlated, and the window part of the error is a little too small.
* If the band samples scatter about the fitted curve by clearly more than their
  errors say (structure the model does not know, such as the response of the
  instrument), the continuum part is multiplied by the square root of the
  reduced chi-square of the fit. "Clearly more" means more than 3 standard
  deviations above 1, so that ordinary noise never inflates the error.
"""

import math
from dataclasses import dataclass

import numpy as np

# How many line-spread widths (full widths at half maximum) either side of
# the line's center the EW integrates over. A Gaussian line is down to 0.04
# percent of its area outside this window (erf(2.5) = 0.99959).
EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS = 1.5

# The fewest samples in the continuum bands, and in the window, for a
# measurement. The continuum has three coefficients, and the same minimum as
# the feature detector's bands keeps the fit steady.
_MINIMUM_BAND_POINTS = 8
_MINIMUM_WINDOW_POINTS = 3

# The window must be covered by samples to this fraction of its width, so a
# line at the edge of the spectrum does not report the part it can see.
_MINIMUM_WINDOW_COVERAGE = 0.9

# The reduced chi-square of the continuum fit must exceed 1 by this many
# standard deviations before it inflates the continuum error. The standard
# deviation of a reduced chi-square with `n` degrees of freedom is
# sqrt(2 / n).
_EXCESS_SCATTER_SIGMA = 3.0


@dataclass(frozen=True)
class EquivalentWidthMeasurement:
    """An equivalent width and what it was measured with.

    Attributes
    ----------
    equivalent_width_angstrom : `float`
        The EW in Angstroms. Positive for a dip below the continuum.
    equivalent_width_error_angstrom : `float`
        The 1-sigma error of the EW, from the sample noise and the continuum
        fit together.
    window_half_width_angstrom : `float`
        How far either side of the center the integral reached.
    continuum_scatter_inflation : `float`
        The factor the continuum error was multiplied by for excess scatter
        in the bands (1.0 when none was found).
    """

    equivalent_width_angstrom: float
    equivalent_width_error_angstrom: float
    window_half_width_angstrom: float
    continuum_scatter_inflation: float = 1.0


def measure_equivalent_width(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    errors: np.ndarray,
    center_angstrom: float,
    line_spread_angstrom: float,
    inner_band_edge_angstrom: float,
    band_width_angstrom: float,
    polynomial_degree: int = 2,
) -> EquivalentWidthMeasurement | None:
    """Measure the equivalent width of one feature and its error.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths, in Angstroms, sorted in increasing order.
    intensity : `numpy.ndarray`
        The brightness at each wavelength.
    errors : `numpy.ndarray`
        The 1-sigma error of each brightness, in the same units.
    center_angstrom : `float`
        The center of the feature, in Angstroms.
    line_spread_angstrom : `float`
        The full width at half maximum of the line spread at the feature, in
        Angstroms. The window reaches `EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS`
        of these either side of `center_angstrom`.
    inner_band_edge_angstrom : `float`
        How far from the center the detector starts its continuum bands, in
        Angstroms. The bands start at the larger of this and the window
        edge.
    band_width_angstrom : `float`
        How wide each continuum band is, in Angstroms.
    polynomial_degree : `int`, optional
        The degree of the continuum polynomial (default 2).

    Returns
    -------
    measurement : `EquivalentWidthMeasurement` or `None`
        The measurement, or `None` when the bands or the window hold too few
        samples, the bands lie on one side only, the window is not covered,
        or the continuum is not above zero across the window.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    values = np.asarray(intensity, dtype=float)
    sigma = np.asarray(errors, dtype=float)
    usable = np.isfinite(wavelength) & np.isfinite(values) & np.isfinite(sigma) & (sigma >= 0)
    wavelength, values, sigma = wavelength[usable], values[usable], sigma[usable]

    window_half_width = EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS * line_spread_angstrom
    inner = max(inner_band_edge_angstrom, window_half_width)
    outer = inner + band_width_angstrom
    distance = np.abs(wavelength - center_angstrom)
    in_window = distance <= window_half_width
    in_band = (distance > inner) & (distance <= outer)
    if in_window.sum() < _MINIMUM_WINDOW_POINTS or in_band.sum() < _MINIMUM_BAND_POINTS:
        return None
    band_wavelength = wavelength[in_band]
    if not ((band_wavelength < center_angstrom).any() and (band_wavelength > center_angstrom).any()):
        return None

    window_wavelength = wavelength[in_window]
    spacing = np.gradient(window_wavelength)
    covered = float(np.sum(spacing))
    if covered < _MINIMUM_WINDOW_COVERAGE * 2.0 * window_half_width:
        return None

    # Centering the wavelengths on the feature keeps the fit well behaved,
    # and a scale of one line spread keeps the powers near 1.
    scale = max(line_spread_angstrom, 1.0)
    band_x = (band_wavelength - center_angstrom) / scale
    window_x = (window_wavelength - center_angstrom) / scale
    band_design = np.vander(band_x, polynomial_degree + 1)
    window_design = np.vander(window_x, polynomial_degree + 1)

    coefficients, *_ = np.linalg.lstsq(band_design, values[in_band], rcond=None)
    continuum = window_design @ coefficients
    if np.any(continuum <= 0):
        return None

    window_values = values[in_window]
    ratio = window_values / continuum
    equivalent_width = float(np.sum(spacing * (1.0 - ratio)))

    # Part 1: the noise of the samples inside the window.
    window_variance = float(np.sum((spacing * sigma[in_window] / continuum) ** 2))

    # Part 2: the covariance of the continuum coefficients.
    normal_inverse = np.linalg.pinv(band_design.T @ band_design)
    coefficient_covariance = (
        normal_inverse @ (band_design.T * sigma[in_band] ** 2) @ band_design @ normal_inverse
    )
    sensitivity = window_design.T @ (spacing * window_values / continuum**2)
    continuum_variance = float(sensitivity @ coefficient_covariance @ sensitivity)

    residuals = values[in_band] - band_design @ coefficients
    degrees_of_freedom = int(in_band.sum()) - (polynomial_degree + 1)
    inflation = 1.0
    positive_sigma = sigma[in_band] > 0
    if degrees_of_freedom > 0 and positive_sigma.all():
        reduced_chi_square = float(np.sum((residuals / sigma[in_band]) ** 2)) / degrees_of_freedom
        if reduced_chi_square > 1.0 + _EXCESS_SCATTER_SIGMA * math.sqrt(2.0 / degrees_of_freedom):
            inflation = math.sqrt(reduced_chi_square)

    total_variance = window_variance + inflation**2 * continuum_variance
    return EquivalentWidthMeasurement(
        equivalent_width_angstrom=equivalent_width,
        equivalent_width_error_angstrom=math.sqrt(total_variance),
        window_half_width_angstrom=float(window_half_width),
        continuum_scatter_inflation=inflation,
    )
