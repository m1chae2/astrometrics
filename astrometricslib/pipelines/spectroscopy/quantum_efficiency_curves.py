"""A database of how sensitive different cameras are to different colors.

We read these numbers off the graphs the camera manufacturers provide.
They aren't perfectly exact, but they are close enough to fix our spectra.
If a camera isn't listed here, we just don't try to fix it.
"""

from typing import NamedTuple

import numpy as np


class QuantumEfficiencyCurve(NamedTuple):
    """The sensitivity data for a specific camera.

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


# ZWO ASI533MM Pro sensitivity data.
# We read this off the graph provided by ZWO. It peaks around blue/green
# (92% sensitive) and drops off heavily into the deep red/infrared
# (only 6% sensitive at 1000nm).
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
    """Look up the sensitivity data for a specific camera.

    Parameters
    ----------
    camera_name : `str`
        The name of the camera.

    Returns
    -------
    curve : `Optional[QuantumEfficiencyCurve]`
        The sensitivity data, or None if we don't have data for this camera.
    """
    return _QUANTUM_EFFICIENCY_CURVES_BY_CAMERA_NAME.get(camera_name)
