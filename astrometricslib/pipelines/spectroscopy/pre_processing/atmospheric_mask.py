"""Wavelengths where Earth's air, not the star, dims the light.

Starlight crosses the whole atmosphere before it reaches the telescope.
Oxygen and water vapour in the air soak up light at certain wavelengths, so
every star seen from the ground has dark bands at the same places, whatever
the star is. These are called atmospheric (or "telluric") absorption bands.

Two things go wrong if they are left in:

* The classifier compares a star's spectrum with reference spectra that were
  measured from above the air. A dip that comes from the air is counted as
  a difference between the star and the reference.
* The bands change from night to night, because they depend on how much
  air the light crossed. Cool stars also have their own broad dark bands
  (from titanium oxide) in the same red part of the spectrum, so an
  atmospheric dip can be mistaken for a sign of a cooler star.

The fix here is the simple one: leave these wavelengths out of the
comparison. The rest of the spectrum is unchanged. This does not repair the
bands, it only stops them counting.

This is the "atmospheric mask" stage of the spectroscopy pipeline. It does
not remove the other thing that goes wrong at the red end, second-order
light (see `instrument_response`), and it is not a correction of the
star's brightness for how much air it was seen through.

Where it is used: the spectral-type classifier (`spectral_classifier`)
leaves the bands out of its comparison. The absorption-feature detector
(`spectral_feature_detector`) does NOT use it. None of the named features
sit in a band, and blanking samples changes the control measurements that
detector's p-values are calibrated on: on the 131 stored spectra it changed
the verdict for 12.5% of the feature tests (127 of 1016), including lines
far from any band such as H-alpha and Na D, and that calibration has only
been validated on complete spectra. If the detector ever needs the mask, it
must be re-validated first.

What it does and does not do for classification, measured on 11 stored
stars with a catalog type (2026-09-19, see
logs/atmospheric_mask_validation_20260919.json): at the classifier's usual
8000 A upper limit no star gets worse, and a few get slightly better (mean
distance from the true type 11.3 -> 11.0 spectral-type steps; the number of
true types in the top three, the letter matches and the good matches did
not change). It does NOT fix the largest error seen there, K stars matched
as M stars: those stay 11-13 steps off with the mask, so that error is not
caused by the atmosphere.
"""

import numpy as np

# Atmospheric bands to leave out, as `(name, start, end)` in Angstroms.
#
# These are the extents measured in real spectra from this setup, not
# textbook values. On 2026-09-19 the Vega master stack (A0V) and the second
# star of the Alcor pair (A5V) were each divided by the matching reference
# spectrum blurred to this instrument's resolution (about 43 A), and the
# smooth instrument shape was taken out with a 600 A running median. A dip
# that appears in both stars, which are different types on different
# nights, cannot belong to the star. Dips deeper than 3% between 5000 and
# 8400 A:
#
#   O2 A band   Vega 7543-7686 A (16% deep); Alcor star 7521-7697 A (14%)
#   O2 B band   Vega 6882-6915 A (4.6%); the Alcor star's is under 3%
#   water       Vega 8123-8298 A (11.5%); Alcor star 8134-8265 A (4.7%)
#
# Each band below is the widest measured extent plus about 20-30 A of
# margin, because the exact edge moves with the night's air and with the
# focus. The water band lies where hydrogen's Paschen lines are also found
# in A stars, so it is the least certain of the three; it is outside the
# 4200-8000 A range the classifier uses, and matters only to spectra that
# reach further.
#
# Checked and NOT included, because no dip above 3% was seen in either
# star: the oxygen band near 6280 A and the water band near 7160-7340 A.
# Both are real bands, and could show up on a night with more air in the
# way (a star low in the sky). Only two stars were checked, and it is not
# known how high in the sky either one was, so revisit this list if a
# spectrum shows dips there.
#
# See logs/atmospheric_mask_validation_20260919.json for the raw numbers.
ATMOSPHERIC_BANDS_ANGSTROM: tuple[tuple[str, float, float], ...] = (
    ("oxygen B band", 6860.0, 6940.0),
    ("oxygen A band", 7490.0, 7720.0),
    ("water band", 8100.0, 8320.0),
)


def atmospheric_band_mask(wavelength_angstrom: np.ndarray) -> np.ndarray:
    """Find which wavelengths fall inside an atmospheric absorption band.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths to test, in Angstroms.

    Returns
    -------
    is_in_band : `numpy.ndarray`
        A boolean array, `True` for each wavelength inside a band (to be
        left out), `False` elsewhere. Same shape as the input.
    """
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    is_in_band = np.zeros(wavelengths.shape, dtype=bool)
    for _name, band_start, band_end in ATMOSPHERIC_BANDS_ANGSTROM:
        is_in_band |= (wavelengths >= band_start) & (wavelengths <= band_end)
    return is_in_band


def mask_atmospheric_bands(wavelength_angstrom: np.ndarray, intensity: np.ndarray) -> np.ndarray:
    """Blank out a spectrum's brightness inside the atmospheric bands.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The spectrum's brightness at each wavelength.

    Returns
    -------
    masked_intensity : `numpy.ndarray`
        A copy of `intensity` with `NaN` (not a number) inside each band.
        Later stages already skip `NaN` samples.
    """
    masked_intensity = np.array(intensity, dtype=float, copy=True)
    masked_intensity[atmospheric_band_mask(wavelength_angstrom)] = np.nan
    return masked_intensity
