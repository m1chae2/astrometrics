"""Warns where a spectrum's red end could be polluted by second-order light.

A grism sends light of wavelength L to a second position as well, the one
where first-order light of wavelength 2 x L lands (see
`instrument_response` for the full story). So a spectrum's brightness at
9000 A can include a little blue light that started out at 4500 A. How much
depends on the star: a hot star is far brighter in the blue than in the red,
so even a tiny amount of second-order light can be a big share of its red
signal, while a cool star is not affected much.

This module measures only the "could it be a problem?" side. For each
wavelength it reports how many times brighter the star is at half that
wavelength. It does not change the spectrum, and it does not say how much
second-order light really arrived, because that amount could not be
measured from the library's existing stacks (the estimates ranged from
about 0% to 2% and could not be told apart from zero). It is an audit
field: a person reading a spectrum can see which red wavelengths to distrust.
"""

import numpy as np

# The most second-order light we cannot rule out, as a fraction of the
# first-order brightness at half the wavelength. Measured on 2026-09-23 by
# fitting the stored spectra of catalog-typed stars in the Albireo and Vega
# stacks: the estimates were between -0.7% and +2.0%, and almost every
# uncertainty range included zero. A pooled fit over five stacks was even
# less certain (4% to 8% half-widths, driven by giants matched to dwarf
# templates), so it gives no tighter limit. 2% is therefore the largest
# value seen, not a measured value. Validated on those two stacks only.
SECOND_ORDER_MAXIMUM_PLAUSIBLE_FRACTION = 0.02

# The share of a wavelength's brightness that second-order light must be
# able to add before that wavelength is called risky. 10% is a judgement
# call, not a derived number: it is roughly where an added signal starts to
# change a spectrum's shape visibly. Not validated against any data.
SECOND_ORDER_RISKY_SHARE_OF_BRIGHTNESS = 0.10

# The largest ratio that is ever stored. Where the star is dim or
# negative at a red wavelength the true ratio is huge or undefined, so the
# stored value stops here. 1000 is far above any real risky case (the risk
# threshold is reached at a ratio of 5).
MAXIMUM_STORED_RATIO = 1000.0


def compute_second_order_blue_to_red_ratio(
    wavelength_angstrom: np.ndarray, intensity: np.ndarray
) -> np.ndarray:
    """Say how many times brighter a star is at half each wavelength.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms, in increasing order.
    intensity : `numpy.ndarray`
        The spectrum's brightness at those wavelengths (the raw counts
        the extraction measured, since that is what second-order light
        adds to).

    Returns
    -------
    ratio : `numpy.ndarray`
        For each wavelength L, the brightness at L / 2 divided by the
        brightness at L. It is 0.0 where L / 2 is shorter than the
        spectrum's first wavelength, because nothing was measured there
        to compare with, and it never goes above `MAXIMUM_STORED_RATIO`.
    """
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    brightness = np.asarray(intensity, dtype=float)
    ratio = np.zeros(wavelengths.shape, dtype=float)
    if wavelengths.size == 0:
        return ratio

    half_wavelength = wavelengths / 2.0
    is_measured_at_half = half_wavelength >= wavelengths[0]
    blue_brightness = np.interp(half_wavelength, wavelengths, brightness)

    # A dim or negative red value would make the ratio huge or undefined, so
    # the red brightness is never allowed below a thousandth of the blue.
    smallest_red = np.where(blue_brightness > 0.0, blue_brightness / MAXIMUM_STORED_RATIO, 1.0)
    red_brightness = np.maximum(brightness, smallest_red)
    ratio = np.where(
        is_measured_at_half & (blue_brightness > 0.0),
        np.minimum(blue_brightness / red_brightness, MAXIMUM_STORED_RATIO),
        0.0,
    )
    return ratio


def is_second_order_risky(ratio: np.ndarray) -> np.ndarray:
    """Find the wavelengths where second-order light could matter.

    Parameters
    ----------
    ratio : `numpy.ndarray`
        The values from `compute_second_order_blue_to_red_ratio`.

    Returns
    -------
    is_risky : `numpy.ndarray`
        `True` where the largest second-order light we cannot rule out
        (`SECOND_ORDER_MAXIMUM_PLAUSIBLE_FRACTION` of the blue brightness)
        could add at least `SECOND_ORDER_RISKY_SHARE_OF_BRIGHTNESS` of the
        red brightness.
    """
    possible_share = SECOND_ORDER_MAXIMUM_PLAUSIBLE_FRACTION * np.asarray(ratio, dtype=float)
    return possible_share >= SECOND_ORDER_RISKY_SHARE_OF_BRIGHTNESS
