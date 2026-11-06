"""Purpose: The simulated observatory shared by the app and the TPM harness.

Description: Local stand-ins for the telescope computer, so the app can run
its real INDI, PHD2 and download code without a telescope. The pieces are an
INDI server with simulators, a stand-in PHD2 (`mock_phd2`), an exposure driver
(`exposure_driver`), a folder and an ssh stand-in that play the telescope
computer, and a replay of a past night (`night_replay`, `replay_driver`).
`observatory_guard` checks that nothing points beyond this computer, and
`library_copy` gives the app a temporary, copy-on-write view of the real
library. `SimulatedObservatory` starts and stops all of it.

The names below load on first use (PEP 562). The stand-ins are also run as
programs with ``python -m wayfindinglib.simulation.<module>``, and an eager
import here would load the module before it runs.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wayfindinglib.simulation.library_copy import read_report as read_library_copy_report
    from wayfindinglib.simulation.library_copy import teardown as remove_library_copy
    from wayfindinglib.simulation.night_replay import list_nights
    from wayfindinglib.simulation.observatory import (
        SimulatedObservatory,
        SimulationKind,
        SimulationUnavailableError,
        choose_free_port,
        missing_programs,
        offline_environment,
        prepare_offline_world,
        unavailable_reason,
    )
    from wayfindinglib.simulation.observatory_guard import (
        GuardError,
        check_offline_settings_update,
        check_settings_update,
    )

#: Each public name and where it comes from: the module, and the name there.
_LOCATIONS = {
    "GuardError": ("observatory_guard", "GuardError"),
    "SimulatedObservatory": ("observatory", "SimulatedObservatory"),
    "SimulationKind": ("observatory", "SimulationKind"),
    "SimulationUnavailableError": ("observatory", "SimulationUnavailableError"),
    "check_offline_settings_update": ("observatory_guard", "check_offline_settings_update"),
    "check_settings_update": ("observatory_guard", "check_settings_update"),
    "choose_free_port": ("observatory", "choose_free_port"),
    "list_nights": ("night_replay", "list_nights"),
    "missing_programs": ("observatory", "missing_programs"),
    "offline_environment": ("observatory", "offline_environment"),
    "prepare_offline_world": ("observatory", "prepare_offline_world"),
    "read_library_copy_report": ("library_copy", "read_report"),
    "remove_library_copy": ("library_copy", "teardown"),
    "unavailable_reason": ("observatory", "unavailable_reason"),
}

__all__ = sorted(_LOCATIONS)


def __getattr__(name: str) -> object:
    """Load a public name from its module the first time it is used.

    Parameters
    ----------
    name : `str`
        The attribute asked for.

    Returns
    -------
    value : `object`
        The class or function.

    Raises
    ------
    AttributeError
        If `name` is not a public name of this package.
    """
    location = _LOCATIONS.get(name)
    if location is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = location
    value = getattr(importlib.import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value
