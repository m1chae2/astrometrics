"""Purpose: Remove (or add) interstellar reddening from a spectrum.

Description: Dust between us and a star absorbs blue light more than red
light. The star then looks redder than it is, and its spectrum is tilted
toward the red. This is called interstellar extinction (dimming) and
reddening (the colour change). The size of the reddening is given by the
colour excess E(B-V): how many magnitudes redder the star's B-V colour is
than the colour it would have without dust.

This module turns a colour excess into a dimming at each wavelength with the
Cardelli, Clayton and Mathis (1989) law, and uses that to undo the reddening
of a spectrum. The law describes the average dust in our galaxy with one
number, R_V = A(V) / E(B-V), the ratio of the dimming in the visual (V) band
to the colour excess. The common value is R_V = 3.1.

Formula (Cardelli, Clayton and Mathis 1989, ApJ 345, 245, equations 1 to 3).
With x = 1 / (wavelength in micrometres), the dimming in magnitudes at any
wavelength is A(wavelength) = A(V) * (a(x) + b(x) / R_V). The two functions
a(x) and b(x) are fixed polynomials and rational terms in x:

* Infrared, 0.3 <= x <= 1.1: a = 0.574 x^1.61 and b = -0.527 x^1.61.
* Optical and near infrared, 1.1 <= x <= 3.3, with y = x - 1.82:
  a = 1 + 0.17699 y - 0.50447 y^2 - 0.02427 y^3 + 0.72085 y^4
  + 0.01979 y^5 - 0.77530 y^6 + 0.32999 y^7, and
  b = 1.41338 y + 2.28305 y^2 + 1.07233 y^3 - 5.38434 y^4
  - 0.62251 y^5 + 5.30260 y^6 - 2.09002 y^7.
* Ultraviolet, 3.3 <= x <= 8: a = 1.752 - 0.316 x
  - 0.104 / ((x - 4.67)^2 + 0.341) + F_a(x) and b = -3.090 + 1.825 x
  + 1.206 / ((x - 4.62)^2 + 0.263) + F_b(x), where F_a and F_b are zero for
  x < 5.9 and otherwise F_a = -0.04473 (x - 5.9)^2 - 0.009779 (x - 5.9)^3
  and F_b = 0.2130 (x - 5.9)^2 + 0.1207 (x - 5.9)^3.

The law holds from 1250 Angstroms (x = 8) to 3.33 micrometres (x = 0.3). The
instrument here sees 3000 to 10000 Angstroms, so only the infrared piece, the
optical piece and the first part of the ultraviolet piece are used.

This is an average law. Real dust differs from line of sight to line of
sight, so a dereddened spectrum is a better estimate of the star's own
colour, not an exact one.
"""

import numpy as np

__all__ = [
    "CCM89_MAXIMUM_WAVELENGTH_ANGSTROM",
    "CCM89_MINIMUM_WAVELENGTH_ANGSTROM",
    "STANDARD_R_V",
    "ccm89_extinction_ratio",
    "deredden_spectrum",
    "extinction_magnitudes",
    "redden_spectrum",
]

STANDARD_R_V = 3.1
"""The ratio A(V) / E(B-V) for average galactic dust (Cardelli, Clayton and
Mathis 1989). A(V) is the dimming in magnitudes in the V band."""

CCM89_MINIMUM_WAVELENGTH_ANGSTROM = 1250.0
"""The shortest wavelength the law covers, in Angstroms (x = 8)."""

CCM89_MAXIMUM_WAVELENGTH_ANGSTROM = 33333.0
"""The longest wavelength the law covers, in Angstroms (x = 0.3)."""

# Edges of the three pieces, as x = 1 / (wavelength in micrometres).
_X_INFRARED_TO_OPTICAL = 1.1
_X_OPTICAL_TO_ULTRAVIOLET = 3.3
_X_ULTRAVIOLET_BUMP_END = 5.9

# Coefficients of the optical polynomial in y = x - 1.82, lowest power
# first, from Cardelli, Clayton and Mathis (1989) equation 3a.
_OPTICAL_A = (1.0, 0.17699, -0.50447, -0.02427, 0.72085, 0.01979, -0.77530, 0.32999)
_OPTICAL_B = (0.0, 1.41338, 2.28305, 1.07233, -5.38434, -0.62251, 5.30260, -2.09002)
_OPTICAL_Y_OFFSET = 1.82


def _polynomial(coefficients: tuple[float, ...], y: np.ndarray) -> np.ndarray:
    """Evaluate a polynomial whose lowest-power coefficient comes first.

    Parameters
    ----------
    coefficients : `tuple` [`float`]
        The coefficients, constant term first.
    y : `numpy.ndarray`
        The values to evaluate at.

    Returns
    -------
    values : `numpy.ndarray`
        The polynomial at each `y`.
    """
    return np.polynomial.polynomial.polyval(y, coefficients)


def ccm89_extinction_ratio(wavelength_angstrom: np.ndarray, r_v: float = STANDARD_R_V) -> np.ndarray:
    """Give the dimming at each wavelength as a multiple of the V-band dimming.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        Wavelengths in Angstroms, from `CCM89_MINIMUM_WAVELENGTH_ANGSTROM`
        to `CCM89_MAXIMUM_WAVELENGTH_ANGSTROM`.
    r_v : `float`, optional
        The ratio A(V) / E(B-V) of the dust. Defaults to `STANDARD_R_V`.

    Returns
    -------
    ratio : `numpy.ndarray`
        A(wavelength) / A(V) at each wavelength. It is 1.0 near 5500
        Angstroms, larger in the blue and smaller in the red.

    Raises
    ------
    ValueError
        If a wavelength lies outside the range the law covers, or `r_v` is
        not positive.
    """
    if r_v <= 0:
        raise ValueError(f"R_V must be positive, got {r_v}.")
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    if wavelengths.size and (
        np.nanmin(wavelengths) < CCM89_MINIMUM_WAVELENGTH_ANGSTROM
        or np.nanmax(wavelengths) > CCM89_MAXIMUM_WAVELENGTH_ANGSTROM
    ):
        raise ValueError(
            "The Cardelli, Clayton and Mathis law covers "
            f"{CCM89_MINIMUM_WAVELENGTH_ANGSTROM:.0f} to {CCM89_MAXIMUM_WAVELENGTH_ANGSTROM:.0f} Angstroms."
        )
    x = 1.0e4 / wavelengths
    a = np.zeros_like(x)
    b = np.zeros_like(x)

    infrared = x < _X_INFRARED_TO_OPTICAL
    a[infrared] = 0.574 * x[infrared] ** 1.61
    b[infrared] = -0.527 * x[infrared] ** 1.61

    optical = (x >= _X_INFRARED_TO_OPTICAL) & (x <= _X_OPTICAL_TO_ULTRAVIOLET)
    y = x[optical] - _OPTICAL_Y_OFFSET
    a[optical] = _polynomial(_OPTICAL_A, y)
    b[optical] = _polynomial(_OPTICAL_B, y)

    ultraviolet = x > _X_OPTICAL_TO_ULTRAVIOLET
    xu = x[ultraviolet]
    beyond_bump = np.clip(xu - _X_ULTRAVIOLET_BUMP_END, 0.0, None)
    a[ultraviolet] = (
        1.752
        - 0.316 * xu
        - 0.104 / ((xu - 4.67) ** 2 + 0.341)
        + (-0.04473 * beyond_bump**2 - 0.009779 * beyond_bump**3)
    )
    b[ultraviolet] = (
        -3.090
        + 1.825 * xu
        + 1.206 / ((xu - 4.62) ** 2 + 0.263)
        + (0.2130 * beyond_bump**2 + 0.1207 * beyond_bump**3)
    )
    return a + b / r_v


def extinction_magnitudes(
    wavelength_angstrom: np.ndarray, ebv: float, r_v: float = STANDARD_R_V
) -> np.ndarray:
    """Give the dimming, in magnitudes, that dust of a given reddening causes.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        Wavelengths in Angstroms.
    ebv : `float`
        The colour excess E(B-V), in magnitudes. Zero means no dust.
    r_v : `float`, optional
        The ratio A(V) / E(B-V). Defaults to `STANDARD_R_V`.

    Returns
    -------
    dimming : `numpy.ndarray`
        A(wavelength) in magnitudes, equal to `r_v` * `ebv` times the ratio
        from `ccm89_extinction_ratio`.
    """
    return r_v * ebv * ccm89_extinction_ratio(wavelength_angstrom, r_v)


def redden_spectrum(
    wavelength_angstrom: np.ndarray, flux: np.ndarray, ebv: float, r_v: float = STANDARD_R_V
) -> np.ndarray:
    """Apply interstellar dimming to a spectrum.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        Wavelengths in Angstroms.
    flux : `numpy.ndarray`
        The brightness at each wavelength.
    ebv : `float`
        The colour excess E(B-V), in magnitudes.
    r_v : `float`, optional
        The ratio A(V) / E(B-V). Defaults to `STANDARD_R_V`.

    Returns
    -------
    reddened_flux : `numpy.ndarray`
        `flux` times 10 to the power of -0.4 A(wavelength).
    """
    dimming = extinction_magnitudes(wavelength_angstrom, ebv, r_v)
    return np.asarray(flux, dtype=float) * 10.0 ** (-0.4 * dimming)


def deredden_spectrum(
    wavelength_angstrom: np.ndarray, flux: np.ndarray, ebv: float, r_v: float = STANDARD_R_V
) -> np.ndarray:
    """Remove interstellar dimming from a spectrum.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        Wavelengths in Angstroms.
    flux : `numpy.ndarray`
        The observed brightness at each wavelength.
    ebv : `float`
        The colour excess E(B-V), in magnitudes.
    r_v : `float`, optional
        The ratio A(V) / E(B-V). Defaults to `STANDARD_R_V`.

    Returns
    -------
    intrinsic_flux : `numpy.ndarray`
        `flux` times 10 to the power of +0.4 A(wavelength): the brightness
        the star would show without the dust.
    """
    dimming = extinction_magnitudes(wavelength_angstrom, ebv, r_v)
    return np.asarray(flux, dtype=float) * 10.0 ** (0.4 * dimming)
