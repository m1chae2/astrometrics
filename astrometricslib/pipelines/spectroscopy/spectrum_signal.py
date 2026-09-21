"""Tells whether an extracted spectrum contains a measurable star at all.

A star that is too faint for the frames gives a "spectrum" that is only sky
noise: after the sky is subtracted it sits on zero with random spikes above
it. The classifier and the feature detector will still return an answer for
such a spectrum (a best-matching spectral type, a "possible" line), because
they are built to rank whatever they are given. Their statistics are
calibrated for a spectrum that has a continuum to test lines against, so
this module is the check that comes first: is there a continuum at all?
"""

import numpy as np
from scipy.ndimage import median_filter

# A spectrum whose typical resolution element is less than this many times
# the scatter between neighbouring elements has no measurable continuum. The
# depth of a dip is measured against the continuum, so its uncertainty is
# about 1 / (signal-to-noise): at 2 that is 50%, which is as deep as any real
# absorption line gets, so no verdict about a line could be honest.
#
# Measured on the 137 spectra stored in the catalog on 2026-09-21: the two
# stars that are only noise (Gaia DR3 2097952543255224448 and
# 2098050434148460544, magnitude 13.6-13.7, 79-81% of samples at or below
# zero) scored 0.7 and 0.5; the faintest real spectrum scored 3.3 and the
# median was 5.6. Any cut between 1 and 3 separates them there, and 2 is
# chosen as the middle. Only two noise spectra exist in that set, so the cut
# is not tested against a broader mix of faint stars.
MINIMUM_SPECTRUM_SIGNAL_TO_NOISE = 2.0

# The running median that stands for the smooth continuum spans this many
# resolution elements. It has to be wider than the widest line, so a line does
# not count as noise, and narrower than the continuum's own curve.
_TREND_WIDTH_ELEMENTS = 5

# Fewest resolution elements needed to say anything about the signal. With
# fewer, no verdict is given (the spectrum is not called empty).
_MINIMUM_ELEMENTS = 8


def estimate_spectrum_signal_to_noise(
    wavelength_angstrom: np.ndarray, intensity: np.ndarray, resolution_element_angstrom: float
) -> float | None:
    """Measure how strongly a spectrum stands out from its own scatter.

    The spectrum is averaged into resolution elements. The scatter is what is
    left of each element after a running median (the smooth continuum) is
    taken away, and the signal is the middle element. The result is the
    ratio of the two.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The spectrum's brightness.
    resolution_element_angstrom : `float`
        The width of one resolution element, in Angstroms.

    Returns
    -------
    signal_to_noise : `float` or `None`
        The typical signal-to-noise of one resolution element: 0 when the
        middle element is not above zero, infinite when there is no scatter,
        `None` when there are too few elements to tell.
    """
    wavelength = np.asarray(wavelength_angstrom, dtype=float)
    values = np.asarray(intensity, dtype=float)
    finite = np.isfinite(wavelength) & np.isfinite(values)
    wavelength, values = wavelength[finite], values[finite]
    if wavelength.size == 0 or resolution_element_angstrom <= 0:
        return None
    order = np.argsort(wavelength)
    wavelength, values = wavelength[order], values[order]

    element_index = np.floor((wavelength - wavelength[0]) / resolution_element_angstrom).astype(int)
    elements = np.array([values[element_index == index].mean() for index in np.unique(element_index)])
    if elements.size < _MINIMUM_ELEMENTS:
        return None

    trend = median_filter(elements, size=_TREND_WIDTH_ELEMENTS, mode="nearest")
    residual = elements - trend
    scatter = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    signal = float(np.median(elements))
    if signal <= 0:
        return 0.0
    if scatter <= 0:
        return float("inf")
    return signal / scatter
