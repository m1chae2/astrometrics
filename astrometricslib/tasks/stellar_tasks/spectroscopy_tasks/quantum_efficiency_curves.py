"""Reference quantum-efficiency curves for supported camera sensors.

Provides digitized quantum-efficiency (QE) vs wavelength reference
data for the camera models this library's spectroscopy pipeline
knows about, and a lookup accessor for the pipeline to fetch a
camera's curve by name.

Notes
-----
The curve points below are **visually digitized from manufacturer
datasheet plots**, not exact published tables — precision is limited
by both the source plot's resolution and manual point-picking, and
should not be treated as calibration-grade metrology. Cameras with no
digitized curve here (e.g. the Nikon D5300 and ZWO ASI120MC-S entries
in `astrometrics.config`) simply have no entry in the lookup table,
which is the intended signal for callers to skip QE correction for
that camera rather than guess.
"""

from typing import NamedTuple

import numpy as np


class QuantumEfficiencyCurve(NamedTuple):
    """A camera's digitized quantum-efficiency curve.

    Attributes
    ----------
    wavelength_nm : `np.ndarray`
        Wavelengths (nanometers) of the digitized curve points, sorted
        ascending.
    quantum_efficiency_fraction : `np.ndarray`
        Quantum efficiency at each corresponding wavelength, expressed
        as a fraction (0-1), not a percentage.
    """

    wavelength_nm: np.ndarray
    quantum_efficiency_fraction: np.ndarray


# ZWO ASI533MM Pro (Sony IMX533 mono sensor) quantum-efficiency curve.
#
# Visually digitized from the manufacturer's published QE-vs-wavelength
# datasheet plot (peak ~91-92% at 460-480nm, ~88-90% plateau through
# 500-550nm, then declining smoothly to ~35% at 750nm, ~19% at 850nm,
# ~8% at 950nm, and ~6% by 1000nm). Approximate, not exact manufacturer
# figures. The datasheet plot only covers 400-1000nm; wavelengths below
# 400nm are not measured here and edge-hold to the 400nm value when
# queried (see `quantum_efficiency_correction.interpolate_quantum_efficiency`).
_ZWO_ASI533MM_PRO_QUANTUM_EFFICIENCY_CURVE = QuantumEfficiencyCurve(
    wavelength_nm=np.array([
        400.0,
        420.0,
        440.0,
        460.0,
        480.0,
        500.0,
        520.0,
        550.0,
        600.0,
        650.0,
        700.0,
        750.0,
        800.0,
        850.0,
        900.0,
        950.0,
        1000.0,
    ]),
    quantum_efficiency_fraction=np.array([
        0.70,
        0.82,
        0.88,
        0.91,
        0.92,
        0.90,
        0.89,
        0.88,
        0.78,
        0.62,
        0.48,
        0.36,
        0.27,
        0.19,
        0.13,
        0.08,
        0.06,
    ]),
)

_QUANTUM_EFFICIENCY_CURVES_BY_CAMERA_NAME: dict[str, QuantumEfficiencyCurve] = {
    "ZWO ASI533MM Pro": _ZWO_ASI533MM_PRO_QUANTUM_EFFICIENCY_CURVE,
}


def get_quantum_efficiency_curve(camera_name: str) -> QuantumEfficiencyCurve | None:
    """Return the digitized quantum-efficiency curve for a camera, if known.

    Parameters
    ----------
    camera_name : `str`
        Camera name, expected to match `CameraConfig.name` (e.g. the
        `Observatory.Camera.<name>` section names in
        `astrometrics.config`).

    Returns
    -------
    curve : `Optional[QuantumEfficiencyCurve]`
        The camera's digitized QE curve, or `None` if this camera has
        no digitized curve on file. Callers should treat `None` as
        "skip QE correction for this camera" rather than an error.
    """
    return _QUANTUM_EFFICIENCY_CURVES_BY_CAMERA_NAME.get(camera_name)
