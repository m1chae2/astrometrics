"""Purpose: Wayfinder Navigation high-level interface exports and API.

Description: Exposes the Wayfinder high-level interface class
composing the three root functions -- Observatory Control,
Observation Planning, Observation Execution.

Every name below except `Wayfinder` itself is resolved lazily (module
`__getattr__`, PEP 562) rather than imported at module level.
`wayfindinglib.api.planning_registry` -- and every other submodule --
is a submodule of this package, so Python must execute this file before
any of them are reachable; a top-level `from wayfindinglib.drivers
.indi_interface import IndiInterface` here would mean importing
anything under `wayfindinglib.*` transitively imports INDI hardware
modules, directly undermining "Planning Is Hardware-Free"
(`Wayfinding_Library_Architecture.md` §2.3.4). `Wayfinder.__init__`
itself already imports `ObservatoryControl`/`ObservationPlanning`
/`ObservationExecution` locally inside the method body rather than at
class-definition time, so the class definition alone carries no
hardware import either. The `TYPE_CHECKING`-guarded imports below
never execute at runtime, so they carry none of that cost -- they
exist only so static analysis can see each name as real for `__all__`.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wayfindinglib.api.control_registry import ObservatoryControl
    from wayfindinglib.api.execution_registry import ObservationExecution
    from wayfindinglib.api.planning_registry import ObservationPlanning
    from wayfindinglib.drivers.indi_interface import IndiInterface
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.exceptions import AstrometryHardwareError

try:
    # Single source of truth is pyproject.toml; both libraries ship from the
    # same `astrometrics` distribution, so neither carries its own literal.
    __version__ = _distribution_version("astrometrics")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = [
    "AstrometryHardwareError",
    "IndiInterface",
    "ObservationExecution",
    "ObservationPlanning",
    "ObservatoryControl",
    "SimulatorIndiInterface",
    "Wayfinder",
]


def __getattr__(name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Lazily resolve every export but Wayfinder, avoiding hardware imports.

    Returns
    -------
    resolved : `Any`
        The resolved export.

    Raises
    ------
    AttributeError
        Raised if `name` is not a lazily-resolved export.
    """
    if name == "IndiInterface":
        from wayfindinglib.drivers.indi_interface import IndiInterface

        return IndiInterface
    if name == "SimulatorIndiInterface":
        from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface

        return SimulatorIndiInterface
    if name == "AstrometryHardwareError":
        from wayfindinglib.exceptions import AstrometryHardwareError

        return AstrometryHardwareError
    if name == "ObservatoryControl":
        from wayfindinglib.api.control_registry import ObservatoryControl

        return ObservatoryControl
    if name == "ObservationPlanning":
        from wayfindinglib.api.planning_registry import ObservationPlanning

        return ObservationPlanning
    if name == "ObservationExecution":
        from wayfindinglib.api.execution_registry import ObservationExecution

        return ObservationExecution
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class Wayfinder:
    """Canonical entry point for the Wayfinder Navigation Library.

    Composes the three root functions -- Observatory Control,
    Observation Planning, Observation Execution -- as `.control`,
    `.planning`, `.execution`, per
    `Wayfinding_Library_Architecture.md` §2.1.1. The pre-redesign
    `.observatory`/`.observation`/`.sky` attributes are no longer
    composed here -- every call site has been repointed onto the three
    root-function high-level interfaces, per the migration roadmap's
    M6 wire-up.

    Deliberately excludes the watchdog (`wayfindinglib/watchdog/`),
    which must not be importable by what it watches
    (`Wayfinding_Library_Architecture.md` §2.5.7).
    """

    def __init__(self, config=None, app_config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the Wayfinder high-level interface.

        Description: Composes the three root-function high-level interfaces
        (control, planning, execution) over a shared config and
        recording butler.
        """
        from astrometricslib import get_configuration
        from wayfindinglib.api.control_registry import ObservatoryControl
        from wayfindinglib.api.execution_registry import ObservationExecution
        from wayfindinglib.api.planning_registry import ObservationPlanning
        from wayfindinglib.drivers.butler import DiskButler

        self.config = config or app_config or get_configuration()

        butler = DiskButler(app_config=self.config)
        self.control = ObservatoryControl(config=self.config, butler=butler)
        self.planning = ObservationPlanning(butler=butler)
        self.execution = ObservationExecution(butler=butler)
