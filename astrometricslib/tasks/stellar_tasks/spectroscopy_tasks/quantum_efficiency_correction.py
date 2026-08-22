"""Sensor quantum-efficiency correction for extracted spectra.

Divides an extracted spectrum's raw summed-intensity values by the
camera sensor's wavelength-dependent quantum efficiency (QE), so the
sensor's own declining response toward the red end no longer distorts
the plotted spectral shape.

Notes
-----
This is a **QE-only** correction: it removes the sensor's own
wavelength-dependent response, but does not account for grating
diffraction efficiency, telescope/optics throughput, or atmospheric
extinction, all of which also shape the observed spectrum. A fuller
empirical instrument-response function -- capturing all of these at
once -- could be derived later from a calibration star with a known
spectral energy distribution (Vega is already used this way for
wavelength calibration in `calibration_tuner.py`, fitting grating
distance against its known Balmer line rest wavelengths; the same
frame could, in principle, be used to derive a flux response curve
instead of just a wavelength solution). That is future work, not
implemented here.
"""

import numpy as np

from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.quantum_efficiency_curves import (
    QuantumEfficiencyCurve,
)


def interpolate_quantum_efficiency(wavelength_nm: np.ndarray, curve: QuantumEfficiencyCurve) -> np.ndarray:
    """Interpolate a digitized quantum-efficiency curve at wavelengths.

    Parameters
    ----------
    wavelength_nm : `np.ndarray`
        Wavelengths (nanometers) to evaluate the curve at.
    curve : `QuantumEfficiencyCurve`
        The camera's digitized quantum-efficiency curve.

    Returns
    -------
    quantum_efficiency_fraction : `np.ndarray`
        Interpolated quantum efficiency (0-1 fraction) at each
        requested wavelength.

    Notes
    -----
    Uses `np.interp`, which edge-holds: wavelengths below the curve's
    lowest digitized point return that point's QE value, and
    wavelengths above the highest digitized point return that point's
    QE value, rather than extrapolating linearly. This is the intended
    behavior, not an oversight -- it avoids inventing QE values outside
    the range the datasheet plot actually covers.
    """
    return np.interp(wavelength_nm, curve.wavelength_nm, curve.quantum_efficiency_fraction)


def apply_quantum_efficiency_correction(
    wavelength_nm: np.ndarray,
    intensity: np.ndarray,
    curve: QuantumEfficiencyCurve,
    minimum_quantum_efficiency_fraction: float = 0.01,
) -> np.ndarray:
    """Correct summed intensities for the sensor's quantum efficiency.

    Parameters
    ----------
    wavelength_nm : `np.ndarray`
        Wavelength (nanometers) for each intensity sample.
    intensity : `np.ndarray`
        Raw summed-intensity values (ADU) to correct.
    curve : `QuantumEfficiencyCurve`
        The camera's digitized quantum-efficiency curve.
    minimum_quantum_efficiency_fraction : `float`, optional
        Floor applied to the interpolated quantum efficiency before
        dividing, by default 0.01 (1%). Protects against
        divide-by-zero/blow-up at wavelengths where the digitized
        curve's QE approaches zero -- this is a display/analysis aid,
        not a metrology correction, so silently flooring the
        correction magnitude is preferable to raising or returning
        `inf`/`nan`.

    Returns
    -------
    quantum_efficiency_corrected_intensity : `np.ndarray`
        `intensity` divided by the (floored) interpolated quantum
        efficiency at each wavelength.
    """
    quantum_efficiency_fraction = interpolate_quantum_efficiency(wavelength_nm, curve)
    quantum_efficiency_fraction = np.clip(
        quantum_efficiency_fraction, minimum_quantum_efficiency_fraction, None
    )
    return intensity / quantum_efficiency_fraction
