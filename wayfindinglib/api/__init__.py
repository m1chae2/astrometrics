"""Purpose: The three sub-APIs of the Wayfinder.

Description: `ObservatoryControl` (operate the observatory),
`ObservationPlanning` (decide what to observe) and
`ObservationExecution` (run and recover an observing session) live here,
in `control/`, `planning.py` and `execution.py`. Import them from the
package root, `wayfindinglib`, not from this subpackage; never import
`wayfindinglib.tasks` or `wayfindinglib.drivers` directly.

Each name is loaded on first use (module `__getattr__`, PEP 562) from the
lookup table below. Loading `control` eagerly would make importing
`planning` also import the hardware tasks, and planning must stay free
of hardware.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wayfindinglib.api.control import ObservatoryControl
    from wayfindinglib.api.execution import ObservationExecution
    from wayfindinglib.api.planning import ObservationPlanning

__all__ = [
    "ObservationExecution",
    "ObservationPlanning",
    "ObservatoryControl",
]


def __getattr__(name: str) -> type:
    """Load a sub-API class on first use.

    Parameters
    ----------
    name : `str`
        The attribute asked for.

    Returns
    -------
    resolved : `type`
        The class.

    Raises
    ------
    AttributeError
        If `name` is not an export of this package.
    """
    lazy_exports = {
        "ObservatoryControl": "wayfindinglib.api.control",
        "ObservationPlanning": "wayfindinglib.api.planning",
        "ObservationExecution": "wayfindinglib.api.execution",
    }
    if name not in lazy_exports:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(lazy_exports[name]), name)
