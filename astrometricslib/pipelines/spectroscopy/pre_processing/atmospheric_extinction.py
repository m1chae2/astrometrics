"""Corrects a spectrum for how the air's dimming changes with airmass.

Air absorbs and scatters starlight, and it dims blue light much more than
red. How much it dims depends on airmass, a measure of how much air the
light crossed (1.0 straight overhead, 2.0 about 60 degrees from overhead).
The dimming in magnitudes (a logarithmic brightness scale where 1 magnitude
is a factor of 2.512 in flux) is the extinction coefficient `k(wavelength)`,
in magnitudes per airmass, times the airmass.

The instrument response (see `instrument_response`) is fitted to a standard
star at one airmass, so it removes the air's dimming at that airmass along
with the instrument's own tilt. A target observed at another airmass is
dimmed by a different amount, and the leftover is a blue-red tilt of the
same kind the response was meant to remove. From 4200 A to 8000 A the
difference in `k` is about 0.26 magnitudes per airmass, so a change of 0.35
airmass tilts the spectrum by about 0.09 magnitudes, as large as the gap
between neighbouring spectral types in the classifier.

This module scales the response-corrected flux by
`10 ** (0.4 * k * (target_airmass - reference_airmass))`. The scaling puts
back the light that the extra (or fewer) airmasses took out, relative to the
reference. The extinction curve is a table of `k` against wavelength, stored
beside the reference spectra (`atmospheric_extinction_kpno.txt`, with its
source in the data directory's README).

The correction is skipped, and the skip is recorded, when the target's
airmass or the response's reference airmass is unknown.
"""

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

_DATA_DIR = Path(__file__).parent.parent / "data"

# The stored extinction table: wavelength in Angstroms, extinction
# coefficient in magnitudes per airmass.
EXTINCTION_CURVE_FILE = _DATA_DIR / "atmospheric_extinction_kpno.txt"

# A short name for the stored curve, recorded with every correction.
EXTINCTION_CURVE_NAME = "Kitt Peak mean extinction (IRAF kpnoextinct.dat)"

# The range of airmass the correction accepts. An airmass below 1.0 cannot
# happen (it is the value at the zenith, straight overhead). Above 10 the
# plane-parallel airmass in a header is no longer reliable (that is about 6
# degrees above the horizon), and the target would be too faint to use.
MINIMUM_VALID_AIRMASS = 1.0
MAXIMUM_VALID_AIRMASS = 10.0

# Why a correction was skipped; see `ExtinctionCorrection.reason`.
REASON_NO_TARGET_AIRMASS = "target airmass unknown"
REASON_NO_REFERENCE_AIRMASS = "response reference airmass unknown"
REASON_INVALID_AIRMASS = "airmass outside 1-10"


@dataclass(frozen=True)
class ExtinctionCurve:
    """The air's extinction coefficient at a set of wavelengths.

    Attributes
    ----------
    wavelength_angstrom : `numpy.ndarray`
        Table wavelengths, in Angstroms, in increasing order.
    extinction_mag_per_airmass : `numpy.ndarray`
        The extinction coefficient `k` at each wavelength, in magnitudes
        per airmass.
    """

    wavelength_angstrom: np.ndarray
    extinction_mag_per_airmass: np.ndarray

    def value_at(self, wavelength_angstrom: np.ndarray) -> np.ndarray:
        """Give the extinction coefficient at any wavelengths.

        Parameters
        ----------
        wavelength_angstrom : `numpy.ndarray`
            Wavelengths, in Angstroms. Beyond the table's first and last
            wavelength the nearest table value is used.

        Returns
        -------
        extinction_mag_per_airmass : `numpy.ndarray`
            `k` at each wavelength, in magnitudes per airmass.
        """
        return np.interp(wavelength_angstrom, self.wavelength_angstrom, self.extinction_mag_per_airmass)


@dataclass(frozen=True)
class ExtinctionCorrection:
    """A record of whether an extinction correction was applied, and with what.

    Attributes
    ----------
    is_applied : `bool`
        `True` when the correction scaled the spectrum.
    target_airmass : `float` or `None`
        The target's airmass, or `None` when unknown.
    reference_airmass : `float` or `None`
        The airmass of the standard star the response was fitted to, or
        `None` when unknown.
    curve_name : `str`
        The extinction curve used (or that would have been used).
    reason : `str` or `None`
        Why the correction was skipped; `None` when it was applied.
    """

    is_applied: bool
    target_airmass: float | None
    reference_airmass: float | None
    curve_name: str
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Give the record as plain values for JSON.

        Returns
        -------
        record : `dict`
            The fields of this record, by name.
        """
        return {
            "is_applied": self.is_applied,
            "target_airmass": self.target_airmass,
            "reference_airmass": self.reference_airmass,
            "curve_name": self.curve_name,
            "reason": self.reason,
        }


@lru_cache(maxsize=1)
def load_extinction_curve() -> ExtinctionCurve:
    """Read the stored extinction table.

    Returns
    -------
    curve : `ExtinctionCurve`
        The table in `EXTINCTION_CURVE_FILE`. The result is cached, so
        the file is read once.
    """
    table = np.loadtxt(EXTINCTION_CURVE_FILE, delimiter=",", skiprows=1)
    return ExtinctionCurve(table[:, 0], table[:, 1])


def extinction_correction_factor(
    wavelength_angstrom: np.ndarray,
    target_airmass: float,
    reference_airmass: float,
    curve: ExtinctionCurve | None = None,
) -> np.ndarray:
    """Give the factor that puts a spectrum on the reference airmass.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        Wavelengths, in Angstroms.
    target_airmass : `float`
        The airmass the target was observed at.
    reference_airmass : `float`
        The airmass the response's standard star was observed at.
    curve : `ExtinctionCurve`, optional
        The extinction curve. Defaults to the stored one.

    Returns
    -------
    factor : `numpy.ndarray`
        `10 ** (0.4 * k * (target_airmass - reference_airmass))` at each
        wavelength. It is 1.0 when the two airmasses are equal, and above
        1.0 (more at blue wavelengths) when the target is at the higher
        airmass.
    """
    curve = curve if curve is not None else load_extinction_curve()
    k = curve.value_at(np.asarray(wavelength_angstrom, dtype=float))
    return 10.0 ** (0.4 * k * (target_airmass - reference_airmass))


def _is_valid_airmass(airmass: float | None) -> bool:
    """Say whether a value is a usable airmass.

    Parameters
    ----------
    airmass : `float` or `None`
        The value to check.

    Returns
    -------
    is_valid : `bool`
        `True` for a finite number from `MINIMUM_VALID_AIRMASS` to
        `MAXIMUM_VALID_AIRMASS`.
    """
    return (
        airmass is not None
        and math.isfinite(airmass)
        and MINIMUM_VALID_AIRMASS <= airmass <= MAXIMUM_VALID_AIRMASS
    )


def apply_extinction_correction(
    wavelength_angstrom: np.ndarray,
    flux: np.ndarray,
    target_airmass: float | None,
    reference_airmass: float | None,
    curve: ExtinctionCurve | None = None,
) -> tuple[np.ndarray, ExtinctionCorrection]:
    """Scale a response-corrected spectrum to the response's airmass.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelengths, in Angstroms.
    flux : `numpy.ndarray`
        The spectrum's brightness after the instrument response was
        removed. NaN values stay NaN.
    target_airmass : `float` or `None`
        The airmass the target was observed at, from the frame header, or
        `None` when unknown.
    reference_airmass : `float` or `None`
        The airmass of the response's standard star, or `None` when the
        response does not record one.
    curve : `ExtinctionCurve`, optional
        The extinction curve. Defaults to the stored one.

    Returns
    -------
    corrected : `numpy.ndarray`
        The scaled brightness, or a copy of `flux` when the correction was
        skipped.
    record : `ExtinctionCorrection`
        Whether the correction was applied, the two airmasses and, when it
        was skipped, why.
    """
    flux = np.asarray(flux, dtype=float)

    def skipped(reason: str) -> tuple[np.ndarray, ExtinctionCorrection]:
        """Build the result for a skipped correction.

        Parameters
        ----------
        reason : `str`
            Why the correction was skipped.

        Returns
        -------
        result : `tuple` [`numpy.ndarray`, `ExtinctionCorrection`]
            An unchanged copy of the flux and the record.
        """
        return flux.copy(), ExtinctionCorrection(
            False, target_airmass, reference_airmass, EXTINCTION_CURVE_NAME, reason
        )

    if target_airmass is None:
        return skipped(REASON_NO_TARGET_AIRMASS)
    if reference_airmass is None:
        return skipped(REASON_NO_REFERENCE_AIRMASS)
    if not (_is_valid_airmass(target_airmass) and _is_valid_airmass(reference_airmass)):
        return skipped(REASON_INVALID_AIRMASS)

    factor = extinction_correction_factor(wavelength_angstrom, target_airmass, reference_airmass, curve)
    record = ExtinctionCorrection(
        True, float(target_airmass), float(reference_airmass), EXTINCTION_CURVE_NAME
    )
    return flux * factor, record
