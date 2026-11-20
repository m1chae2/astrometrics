"""Fixes the spectrum to account for the camera sensor's sensitivity.

Camera sensors aren't equally sensitive to all colors (they usually see
green better than deep red). This math boosts the signal for the colors
the camera is bad at seeing, so the final graph shows the true shape of
the star's light.

The sensitivity data itself is not kept here. It is stored in each camera's
profile (see `astrometricslib.models.camera_profile`), and the caller turns
it into a `QuantumEfficiencyCurve` with `curve_from_profile_record`.
"""

from typing import NamedTuple

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.camera_profile import QuantumEfficiencyRecord
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

# The least sensitivity the correction divides by. The correction multiplies
# the brightness by one over the sensitivity, so a sensitivity of zero would
# divide by zero. Below this floor the curve is clamped to it, which caps the
# correction at 100 times (1 / 0.01). It is a guard against dividing by
# zero, not a measured limit: the stored ASI533MM Pro curve is 0.06 at its
# red end (1000 nm), so the floor is not reached on that camera.
MINIMUM_QUANTUM_EFFICIENCY_FRACTION = TunedParameter(
    name="spectroscopy.minimum_quantum_efficiency_fraction",
    default=0.01,
    unit="fraction",
    description="The least camera sensitivity the quantum-efficiency correction divides by.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="SPE-D59",
)


class QuantumEfficiencyCurve(NamedTuple):
    """The sensitivity data for a specific camera, as arrays.

    Attributes
    ----------
    wavelength_nm : `np.ndarray`
        The list of colors (in nanometers).
    quantum_efficiency_fraction : `np.ndarray`
        How sensitive the camera is to that color (0.0 means completely blind,
        1.0 means perfect).
    """

    wavelength_nm: np.ndarray
    quantum_efficiency_fraction: np.ndarray


def curve_from_profile_record(record: QuantumEfficiencyRecord) -> QuantumEfficiencyCurve:
    """Turn a camera profile's stored sensitivity data into arrays.

    Parameters
    ----------
    record : `QuantumEfficiencyRecord`
        The sensitivity data from a `CameraProfile`.

    Returns
    -------
    curve : `QuantumEfficiencyCurve`
        The same numbers as arrays, ready for interpolation.
    """
    return QuantumEfficiencyCurve(
        wavelength_nm=np.array(record.wavelength_nm, dtype=float),
        quantum_efficiency_fraction=np.array(record.quantum_efficiency_fraction, dtype=float),
    )


def interpolate_quantum_efficiency(wavelength_nm: np.ndarray, curve: QuantumEfficiencyCurve) -> np.ndarray:
    """Figure out the camera's exact sensitivity for any specific color.

    We only have a few data points from the manufacturer, so this draws
    a line between those dots to estimate the sensitivity everywhere else.

    Parameters
    ----------
    wavelength_nm : `np.ndarray`
        The colors we want to know the sensitivity for.
    curve : `QuantumEfficiencyCurve`
        The data points provided by the camera manufacturer.

    Returns
    -------
    quantum_efficiency_fraction : `np.ndarray`
        The estimated sensitivity (from 0.0 to 1.0) for each color.
    """
    return np.interp(wavelength_nm, curve.wavelength_nm, curve.quantum_efficiency_fraction)


def quantum_efficiency_correction_factor(
    wavelength_nm: np.ndarray,
    curve: QuantumEfficiencyCurve,
    minimum_quantum_efficiency_fraction: float | None = None,
) -> np.ndarray:
    """Give the number the quantum-efficiency correction multiplies by.

    The correction divides the brightness by the camera's sensitivity, so it
    multiplies by one over the sensitivity. A brightness error is multiplied by
    the same number (see `intensity_variance`).

    Parameters
    ----------
    wavelength_nm : `np.ndarray`
        The colors we captured.
    curve : `QuantumEfficiencyCurve`
        The camera's sensitivity data.
    minimum_quantum_efficiency_fraction : `float`, optional
        The least sensitivity used. The default `None` takes the value of
        `MINIMUM_QUANTUM_EFFICIENCY_FRACTION`.

    Returns
    -------
    factor : `np.ndarray`
        One over the sensitivity, at each wavelength.
    """
    if minimum_quantum_efficiency_fraction is None:
        minimum_quantum_efficiency_fraction = float(resolve(MINIMUM_QUANTUM_EFFICIENCY_FRACTION).value)
    quantum_efficiency_fraction = np.clip(
        interpolate_quantum_efficiency(wavelength_nm, curve), minimum_quantum_efficiency_fraction, None
    )
    return 1.0 / quantum_efficiency_fraction


def apply_quantum_efficiency_correction(
    wavelength_nm: np.ndarray,
    intensity: np.ndarray,
    curve: QuantumEfficiencyCurve,
    minimum_quantum_efficiency_fraction: float | None = None,
) -> np.ndarray:
    """Boost the weak parts of the spectrum so the graph is accurate.

    Parameters
    ----------
    wavelength_nm : `np.ndarray`
        The colors we captured.
    intensity : `np.ndarray`
        How much light we captured for each color.
    curve : `QuantumEfficiencyCurve`
        The camera's sensitivity data.
    minimum_quantum_efficiency_fraction : `float`, optional
        If the camera is completely blind to a color (0%), we pretend it's
        at least this sensitive so our math doesn't explode (divide by
        zero). The default `None` takes the value of
        `MINIMUM_QUANTUM_EFFICIENCY_FRACTION` (1%, which limits the
        correction to 100 times).

    Returns
    -------
    quantum_efficiency_corrected_intensity : `np.ndarray`
        The corrected brightness values.
    """
    return intensity * quantum_efficiency_correction_factor(
        wavelength_nm, curve, minimum_quantum_efficiency_fraction
    )
