"""Tells whether an extracted spectrum contains a measurable star at all.

A star that is too faint for the frames gives a "spectrum" that is only sky
noise: after the sky is subtracted it sits on zero with random spikes above
it. The classifier and the feature detector will still return an answer for
such a spectrum (a best-matching spectral type, a "possible" line), because
they are built to rank whatever they are given. Their statistics are
calibrated for a spectrum that has a continuum to test lines against, so
this module is the check that comes first: is there a continuum at all?
"""

import math

import numpy as np

# A spectrum whose typical resolution element is less than this many times
# the scatter between neighbouring elements has no measurable continuum. The
# depth of a dip is measured against the continuum, so its uncertainty is
# about 1 / (signal-to-noise): at 2 that is 50%, which is as deep as any real
# absorption line gets, so no verdict about a line could be honest.
#
# Measured on the 170 spectra stored in the catalog on 2026-09-25 (137 on
# 2026-09-21): the two stars that are only noise (Gaia DR3
# 2097952543255224448 and 2098050434148460544, magnitude 13.6-13.7, 79-81% of
# samples at or below zero) score 0.4 and 0.3; the faintest spectra score 0.9,
# 1.4 and 2.1, then 2.3 and 2.7, and the median is 3.8. A cut of 1.5 gates the
# same four faintest stars that the earlier cut of 2.0 gated with the earlier
# estimate (which scored these 0.5, 0.7, 1.2 and 1.9, and the next ones 2.6,
# 3.1 and 3.7; the new estimate reads about 0.66 of the old one on noisy
# spectra). Only two pure-noise spectra exist in that set, so the cut is not
# tested against a broader mix of faint stars.
MINIMUM_SPECTRUM_SIGNAL_TO_NOISE = 1.5

# The scatter between neighbouring elements is measured from their second
# difference (an element minus the mean of its two neighbours), which is zero
# for any straight stretch of continuum and, for white noise of standard
# deviation s, has a standard deviation of s * sqrt(1.5) (its variance is
# s^2 * (1 + 1/4 + 1/4)). Dividing by this recovers s. The scatter used to be
# what was left after subtracting a running median of five elements, but a
# median of five values that rise or fall steadily is the middle value itself,
# so on any smooth, well-measured spectrum most residuals were exactly zero,
# their median absolute deviation was zero and the result was infinite: 23 of
# the 170 stored spectra scored "infinite" and no bright star could ever be
# gated.
_SECOND_DIFFERENCE_NOISE_FACTOR = math.sqrt(1.5)

# Fewest resolution elements needed to say anything about the signal. With
# fewer, no verdict is given (the spectrum is not called empty).
_MINIMUM_ELEMENTS = 8


def estimate_spectrum_signal_to_noise(
    wavelength_angstrom: np.ndarray, intensity: np.ndarray, resolution_element_angstrom: float
) -> float | None:
    """Measure how strongly a spectrum stands out from its own scatter.

    The spectrum is averaged into resolution elements. The scatter is the
    robust spread of each element's second difference (its departure from
    the mean of its two neighbours, which a smooth continuum does not
    produce), scaled to the standard deviation of the noise, and the signal
    is the middle element. The result is the ratio of the two.

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
        middle element is not above zero, infinite only when the elements
        have no scatter at all (a perfectly straight continuum), `None` when
        there are too few elements to tell.
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

    second_difference = elements[1:-1] - 0.5 * (elements[:-2] + elements[2:])
    scatter = (
        1.4826
        * float(np.median(np.abs(second_difference - np.median(second_difference))))
        / _SECOND_DIFFERENCE_NOISE_FACTOR
    )
    signal = float(np.median(elements))
    if signal <= 0:
        return 0.0
    if scatter <= 0:
        return float("inf")
    return signal / scatter
