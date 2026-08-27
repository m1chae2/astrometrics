"""Fixes the spectrum to account for the camera sensor's sensitivity.

Camera sensors aren't equally sensitive to all colors (they usually see
green better than deep red). This math boosts the signal for the colors
the camera is bad at seeing, so the final graph shows the true shape of
the star's light.
"""

import numpy as np

from astrometricslib.pipelines.spectroscopy.quantum_efficiency_curves import (
    QuantumEfficiencyCurve,
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


def apply_quantum_efficiency_correction(
    wavelength_nm: np.ndarray,
    intensity: np.ndarray,
    curve: QuantumEfficiencyCurve,
    minimum_quantum_efficiency_fraction: float = 0.01,
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
        at least 1% so our math doesn't explode (divide by zero).

    Returns
    -------
    quantum_efficiency_corrected_intensity : `np.ndarray`
        The corrected brightness values.
    """
    quantum_efficiency_fraction = interpolate_quantum_efficiency(wavelength_nm, curve)
    quantum_efficiency_fraction = np.clip(
        quantum_efficiency_fraction, minimum_quantum_efficiency_fraction, None
    )
    return intensity / quantum_efficiency_fraction
