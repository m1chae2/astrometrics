"""Compares a spectrum's colour with the star's catalog colour.

A star's B-V colour (its blue brightness minus its visual brightness) says
how hot it is, and a catalog measured it with a different instrument, so it
does not depend on anything in this pipeline. If the spectrum we extracted
implies a very different colour from the catalog's, the spectrum is probably
not that star's: light from a neighbour or from the star's own zero order got
into the extraction box, or the star was given another star's name.

The spectrum's colour is read from two wavelength windows and turned into an
approximate B-V by calibrating the same two windows on the bundled dwarf
reference spectra, whose B-V is known. The windows are used instead of the
standard Johnson B and V filter curves because the curves are not in this
repository and the instrument response is only valid from 4200 A, where the
real B filter starts to lose most of its blue half. Calibrating on the
references absorbs the difference between the windows and the filters.

Measured on 2026-09-24 on the 36 stars in the library that had a spectrum and
a SIMBAD B-V: the response-corrected spectra gave a B-V that was redder than
the catalog by a median of only 0.02 mag (26 of 30 stars within 0.1 mag), so
this is a coarse check for large disagreements, not a way to measure a
star's colour. It does not correct for interstellar reddening, which in the
fields used so far is about 0.03 to 0.1 mag.
"""

import numpy as np

# The wavelength windows, in Angstroms, that stand in for the B and V filters.
# Both lie inside 4200-8000 A, where the instrument response is valid. The B
# window stops short of H-beta (4861 A) and starts after H-gamma (4340 A), and
# the V window avoids the Na D doublet (5893 A), so a strong line does not
# move the colour. Chosen by judgement, then checked on the library stars.
B_WINDOW_ANGSTROM = (4350.0, 4800.0)
V_WINDOW_ANGSTROM = (5250.0, 5800.0)

# The fewest samples that must fall in each window for a colour to be read.
# One window sample spans about 11 A, so 10 samples is about 110 A: a quarter
# of the B window and a fifth of the V window.
MINIMUM_SAMPLES_PER_WINDOW = 10

# Approximate intrinsic B-V of the bundled dwarf references, from standard
# tabulations of dwarf colours (for example Pecaut and Mamajek 2013), rounded
# to about 0.02 mag. The values were typed in and not checked against the
# published table, so they set the zero point of the calibration and are only
# as good as that. The reference set stops at M4V: later types are redder than
# the calibrated range below and are left out.
DWARF_B_MINUS_V: dict[str, float] = {
    "O5V": -0.33,
    "O9V": -0.31,
    "B0V": -0.30,
    "B1V": -0.27,
    "B3V": -0.20,
    "B8V": -0.11,
    "B9V": -0.07,
    "A0V": 0.00,
    "A2V": 0.05,
    "A3V": 0.08,
    "A5V": 0.15,
    "A7V": 0.20,
    "F0V": 0.30,
    "F2V": 0.35,
    "F5V": 0.44,
    "F6V": 0.48,
    "F8V": 0.53,
    "G0V": 0.58,
    "G2V": 0.63,
    "G5V": 0.68,
    "G8V": 0.74,
    "K0V": 0.81,
    "K2V": 0.91,
    "K3V": 0.99,
    "K4V": 1.05,
    "K5V": 1.15,
    "K7V": 1.33,
    "M0V": 1.40,
    "M1V": 1.47,
    "M2V": 1.49,
    "M3V": 1.51,
    "M4V": 1.64,
}

# A catalog colour redder than this is not compared. The references stop at
# M4V (B-V 1.64), so the map from window colour to B-V is not calibrated for
# later types.
MAXIMUM_CALIBRATED_B_MINUS_V = 1.7

# A spectrum is flagged when its colour differs from the catalog's by more
# than this many magnitudes. On 2026-09-24, 30 of the 36 stars stayed within
# 0.30 mag (the worst, HD 150293, was 0.30 too red). The problem spectra were
# further out: TYC 3105-899-1 -0.58, Elnath +1.04 and HD 172449 +1.0 (which is
# also left unclassified, so gets no note). Arcturus (+0.39 to +0.47 across
# reruns) sits right at the threshold and is not reliably flagged. 0.4 is a
# judgement call from that one data set: it is above every ordinary star seen
# and well above the 0.09 mag median excess for F, G and early K stars, so
# ordinary reddening does not trigger it, but the gap is thin.
COLOUR_DISAGREEMENT_MAGNITUDES = 0.4

_calibration_coefficients: list[float] = []
_calibrated_index_range: list[float] = []


def _window_mean(
    wavelength_angstrom: np.ndarray, flux: np.ndarray, window: tuple[float, float]
) -> float | None:
    """Average a spectrum inside a wavelength window.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    flux : `numpy.ndarray`
        The spectrum's brightness at each wavelength.
    window : `tuple` [`float`, `float`]
        The lowest and highest wavelength to include, in Angstroms.

    Returns
    -------
    mean : `float` or `None`
        The average brightness, or `None` when fewer than
        `MINIMUM_SAMPLES_PER_WINDOW` finite samples lie in the window, or the
        average is not above zero.
    """
    inside = (wavelength_angstrom >= window[0]) & (wavelength_angstrom < window[1]) & np.isfinite(flux)
    if int(inside.sum()) < MINIMUM_SAMPLES_PER_WINDOW:
        return None
    mean = float(np.mean(flux[inside]))
    return mean if mean > 0.0 else None


def window_colour_index(wavelength_angstrom: np.ndarray, flux: np.ndarray) -> float | None:
    """Measure the raw colour between the B and V windows.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    flux : `numpy.ndarray`
        The spectrum's brightness per unit wavelength, with the instrument's
        response already removed.

    Returns
    -------
    index : `float` or `None`
        Minus 2.5 times the base-10 log of the B window's average over the V
        window's, so bluer stars have lower values, or `None` when either
        window is not covered.
    """
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    values = np.asarray(flux, dtype=float)
    blue = _window_mean(wavelengths, values, B_WINDOW_ANGSTROM)
    visual = _window_mean(wavelengths, values, V_WINDOW_ANGSTROM)
    if blue is None or visual is None:
        return None
    return float(-2.5 * np.log10(blue / visual))


def _calibration() -> list[float]:
    """Fit the map from window colour to B-V on the dwarf references.

    Returns
    -------
    coefficients : `list` [`float`]
        A cubic polynomial, highest power first, that turns the window colour
        of a reference spectrum into its B-V. Built once and kept, along with
        the lowest and highest window colour the references cover.
    """
    if not _calibration_coefficients:
        from astrometricslib.pipelines.spectroscopy.spectral_classifier import _get_reference_templates

        templates = _get_reference_templates()
        indices = []
        colours = []
        for reference_type, b_minus_v in DWARF_B_MINUS_V.items():
            index = window_colour_index(*templates[reference_type])
            if index is not None:
                indices.append(index)
                colours.append(b_minus_v)
        _calibration_coefficients.extend(float(value) for value in np.polyfit(indices, colours, 3))
        _calibrated_index_range.extend([min(indices), max(indices)])
    return _calibration_coefficients


def synthetic_b_minus_v(wavelength_angstrom: np.ndarray, corrected_intensity: np.ndarray) -> float | None:
    """Estimate a spectrum's B-V from its two colour windows.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    corrected_intensity : `numpy.ndarray`
        The spectrum's brightness with the instrument's response removed
        (see `instrument_response`); a spectrum that still carries the
        instrument's tilt gives a colour that is too red.

    Returns
    -------
    b_minus_v : `float` or `None`
        The estimated B-V, or `None` when a window is not covered. A
        spectrum whose window colour is bluer or redder than every reference
        is read as the bluest or reddest reference (about 1.6 for the
        reddest), so the value is then a limit, not a measurement, and it
        still shows a large disagreement with a much bluer catalog colour.
    """
    index = window_colour_index(wavelength_angstrom, corrected_intensity)
    if index is None:
        return None
    coefficients = _calibration()
    lowest, highest = _calibrated_index_range
    return float(np.polyval(coefficients, min(max(index, lowest), highest)))


def colour_disagreement_note(catalog_b_minus_v: float | None, synthetic_colour: float | None) -> str:
    """Warn when the spectrum's colour is far from the catalog's.

    Parameters
    ----------
    catalog_b_minus_v : `float`, optional
        The star's catalog B-V, from SIMBAD.
    synthetic_colour : `float`, optional
        The B-V estimated from the spectrum by `synthetic_b_minus_v`.

    Returns
    -------
    note : `str`
        The warning, or an empty string when either colour is missing, or
        they are within `COLOUR_DISAGREEMENT_MAGNITUDES`. Also empty when
        the catalog colour is redder than `MAXIMUM_CALIBRATED_B_MINUS_V`,
        because the map is not calibrated there.
    """
    if catalog_b_minus_v is None or synthetic_colour is None:
        return ""
    if not (np.isfinite(catalog_b_minus_v) and np.isfinite(synthetic_colour)):
        return ""
    if catalog_b_minus_v > MAXIMUM_CALIBRATED_B_MINUS_V:
        return ""
    difference = synthetic_colour - catalog_b_minus_v
    if abs(difference) <= COLOUR_DISAGREEMENT_MAGNITUDES:
        return ""
    direction = "redder" if difference > 0 else "bluer"
    return (
        f"the spectrum is {abs(difference):.1f} mag {direction} than the catalog colour "
        f"(B-V {catalog_b_minus_v:.2f}, spectrum about {synthetic_colour:.2f}): the spectrum may not be "
        "this star's (a bright neighbour, glare from a very bright star, or a wrong name)"
    )
