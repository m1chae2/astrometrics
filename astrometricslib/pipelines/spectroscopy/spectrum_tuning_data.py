"""Purpose: Read the stored star spectra that the instrument tuners measure.

Description: Issue #175 Phase D. The instrument and classification tuners
(`instrument_tuning.py` and `classification_tuning.py`) derive their values
from the star spectra already in the library. This module reads those spectra
once into small plain records and offers the few statistics the tuners share.
Keeping the loading and the statistics here, apart from the tuners, lets the
statistics be tested on plain numbers without a library.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.models.stellar_source import ladder_position
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

# How many stars are loaded from the library at once.
_LOAD_CHUNK_SIZE = 200

# The names a frame's filter takes when it is the spectroscopy grating.
_SPECTRAL_FILTER_NAMES = ("SPEC", "STAR ANALYZER 200", "SPECTROSCOPY")

# The scale that turns the median absolute deviation into the standard
# deviation of a normal distribution.
MEDIAN_ABSOLUTE_DEVIATION_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class StoredStarSpectrum:
    """One star's stored spectrum and the catalog facts to compare with.

    Parameters
    ----------
    star_id : `str`
        The star's id.
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths, in Angstroms.
    intensity : `numpy.ndarray`
        The extracted brightness.
    intensity_error : `numpy.ndarray` or `None`
        The 1-sigma error of each brightness, or `None` when none was stored.
    quantum_efficiency_corrected : `numpy.ndarray` or `None`
        The brightness corrected for the sensor's quantum efficiency.
    response_corrected : `numpy.ndarray` or `None`
        The brightness with the instrument response removed (NaN outside the
        response's range).
    resolution_element_angstrom : `float` or `None`
        The spectrum's own resolution element, in Angstroms.
    trail_width_px : `numpy.ndarray` or `None`
        The trail's sigma at each step, in pixels (0.0 marks a failed fit).
    catalog_type : `str`
        The catalog spectral type, or an empty string.
    catalog_b_minus_v : `float` or `None`
        The catalog B-V colour.
    own_rms : `float` or `None`
        The relative RMS of the stored match to the references.
    gaia_residual_rms : `float` or `None`
        The residual RMS against Gaia XP, when the comparison ran.
    gaia_slope_percent : `float` or `None`
        The slope against Gaia XP, in percent per 1000 Angstroms.
    """

    star_id: str
    wavelength_angstrom: np.ndarray
    intensity: np.ndarray
    intensity_error: np.ndarray | None
    quantum_efficiency_corrected: np.ndarray | None
    response_corrected: np.ndarray | None
    resolution_element_angstrom: float | None
    trail_width_px: np.ndarray | None
    catalog_type: str
    catalog_b_minus_v: float | None
    own_rms: float | None
    gaia_residual_rms: float | None
    gaia_slope_percent: float | None


def require_spectral_filter(inputs: TuningInputs) -> None:
    """Skip a tuning run for a filter that is not the spectroscopy grating.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner was given.

    Raises
    ------
    TuningSkippedError
        If the filter is not the spectroscopy grating.
    """
    if str(inputs.filter_name).upper() not in _SPECTRAL_FILTER_NAMES:
        raise TuningSkippedError(f"{inputs.filter_name!r} is not the spectroscopy grating")


def _optional_float(value: Any) -> float | None:
    """Read a stored number that may be missing or not a number.

    Parameters
    ----------
    value : `Any`
        The stored value.

    Returns
    -------
    number : `float` or `None`
        The value as a finite float, or `None`.
    """
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if np.isfinite(number) else None


def stored_star_spectrum(star: Any) -> StoredStarSpectrum | None:
    """Make a record from one library star.

    Parameters
    ----------
    star : `StellarObject`
        A star from the library.

    Returns
    -------
    record : `StoredStarSpectrum` or `None`
        The record, or `None` when the star has no usable spectrum.
    """
    spectrum = star.spectroscopy
    if spectrum is None or not spectrum.wavelengths_angstrom or not spectrum.intensities:
        return None
    wavelengths = np.asarray(spectrum.wavelengths_angstrom, dtype=float)
    intensity = np.asarray(spectrum.intensities, dtype=float)
    if wavelengths.shape != intensity.shape:
        return None

    def matching(values: Sequence[float] | None) -> np.ndarray | None:
        """Keep a stored list only when it lines up with the wavelengths.

        Parameters
        ----------
        values : `Sequence` [`float`] or `None`
            The stored list.

        Returns
        -------
        array : `numpy.ndarray` or `None`
            The list as an array, or `None` when missing or misaligned.
        """
        if not values:
            return None
        array = np.asarray(values, dtype=float)
        return array if array.shape == wavelengths.shape else None

    comparison = spectrum.gaia_xp_comparison
    compared = comparison is not None and comparison.status == "compared"
    return StoredStarSpectrum(
        star_id=str(star.id),
        wavelength_angstrom=wavelengths,
        intensity=intensity,
        intensity_error=matching(spectrum.intensity_errors),
        quantum_efficiency_corrected=matching(spectrum.quantum_efficiency_corrected_intensities),
        response_corrected=matching(spectrum.response_corrected_intensities),
        resolution_element_angstrom=_optional_float(spectrum.resolution_element_angstrom),
        trail_width_px=matching(spectrum.trail_width_px),
        catalog_type=str(star.spectral_type or ""),
        catalog_b_minus_v=_optional_float(star.b_minus_v),
        own_rms=_optional_float(spectrum.self_determined_spectral_type_rms),
        gaia_residual_rms=_optional_float(comparison.residual_rms_fraction) if compared else None,
        gaia_slope_percent=_optional_float(comparison.slope_percent_per_1000_angstrom) if compared else None,
    )


def load_stored_star_spectra(astrometrics: Any) -> list[StoredStarSpectrum]:
    """Read every stored star spectrum from the library.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library.

    Returns
    -------
    spectra : `list` [`StoredStarSpectrum`]
        One record for each star that has a spectrum.

    Raises
    ------
    TuningSkippedError
        If the library holds no star spectra.
    """
    catalog_access = astrometrics._catalog_access
    summaries = [summary for summary in catalog_access.list_star_summaries() if summary.has_spectra]
    spectra: list[StoredStarSpectrum] = []
    for start in range(0, len(summaries), _LOAD_CHUNK_SIZE):
        chunk = summaries[start : start + _LOAD_CHUNK_SIZE]
        for star in catalog_access.get_by_ids("stellar_catalog", [summary.id for summary in chunk]):
            record = stored_star_spectrum(star)
            if record is not None:
                spectra.append(record)
    if not spectra:
        raise TuningSkippedError("the library holds no stored star spectra")
    return spectra


def robust_sigma(values: Sequence[float] | np.ndarray) -> float:
    """Estimate the spread of values that a few outliers cannot inflate.

    Parameters
    ----------
    values : `Sequence` [`float`] or `numpy.ndarray`
        The values.

    Returns
    -------
    sigma : `float`
        1.4826 times the median absolute deviation.
    """
    array = np.asarray(values, dtype=float)
    return MEDIAN_ABSOLUTE_DEVIATION_TO_SIGMA * float(np.median(np.abs(array - np.median(array))))


def upper_limit_from_spread(values: Sequence[float] | np.ndarray, sigma_multiple: float) -> float:
    """Place a limit above the ordinary spread of some values.

    Parameters
    ----------
    values : `Sequence` [`float`] or `numpy.ndarray`
        The values measured on ordinary stars.
    sigma_multiple : `float`
        How many robust standard deviations above the median to put the limit.

    Returns
    -------
    limit : `float`
        The median plus `sigma_multiple` robust standard deviations.
    """
    array = np.asarray(values, dtype=float)
    return float(np.median(array)) + sigma_multiple * robust_sigma(array)


def symmetric_limit_from_spread(values: Sequence[float] | np.ndarray, sigma_multiple: float) -> float:
    """Place a limit on the size of values that scatter around zero.

    Parameters
    ----------
    values : `Sequence` [`float`] or `numpy.ndarray`
        Signed values measured on ordinary stars.
    sigma_multiple : `float`
        How many robust standard deviations to allow.

    Returns
    -------
    limit : `float`
        The size of the median plus `sigma_multiple` robust standard
        deviations. A value is flagged when its size is above this.
    """
    array = np.asarray(values, dtype=float)
    return abs(float(np.median(array))) + sigma_multiple * robust_sigma(array)


def reference_label(catalog_type: str, available_labels: Sequence[str]) -> str | None:
    """Find the bundled reference spectrum that matches a catalog type.

    Parameters
    ----------
    catalog_type : `str`
        A catalog type such as ``"A0V"`` or ``"K2III"``.
    available_labels : `Sequence` [`str`]
        The labels of the bundled references.

    Returns
    -------
    label : `str` or `None`
        The longest label the catalog type starts with, when the type is on
        the ladder; `None` otherwise.
    """
    if ladder_position(catalog_type) is None:
        return None
    trimmed = catalog_type.strip().upper().replace(" ", "")
    for label in sorted(available_labels, key=len, reverse=True):
        if trimmed.startswith(label.upper()):
            return label
    return None


def declare_method_choice(
    name: str, default: float, unit: str, description: str, register_entry: str, kind: HardcodedKind
) -> TunedParameter:
    """Declare a number a tuning method needs, with the reason it is fixed.

    Parameters
    ----------
    name : `str`
        The parameter name.
    default : `float`
        The value.
    unit : `str`
        Its unit.
    description : `str`
        What it is for, in one sentence.
    register_entry : `str`
        The validation register entry of the value the method tunes.
    kind : `HardcodedKind`
        Why it may stay in the code.

    Returns
    -------
    parameter : `TunedParameter`
        The declaration.
    """
    return TunedParameter(
        name=name,
        default=default,
        unit=unit,
        description=description,
        role=ParameterRole.MEASUREMENT,
        source=ParameterSource.HARDCODED,
        kind=kind,
        register_entry=register_entry,
    )
