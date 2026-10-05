"""Purpose: The Wayfinder, the entry point to the wayfinding library.

Description: `Wayfinder` composes the library's three sub-APIs:
`control` (operate the observatory), `planning` (decide what to
observe) and `execution` (run and recover an observing session).
`control` has seven children named by topic, such as `control.mount`
and `control.history`.

Every name below except `Wayfinder` is loaded on first use (module
`__getattr__`, PEP 562) from one lookup table. Importing a submodule
such as `wayfindinglib.api.planning` runs this file first, so an eager
import of the INDI drivers here would make every import of the library
load hardware code. The `TYPE_CHECKING` imports never run; they only let
type checkers see each name in `__all__`.
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import TYPE_CHECKING

# A library only writes log messages. A program decides where they go, by
# calling `configure_logging`. The null handler stops Python from printing a
# "no handlers" warning when no program has done so.
logging.getLogger(__name__).addHandler(logging.NullHandler())

if TYPE_CHECKING:
    from astrometricslib import AppConfiguration, Astrometrics
    from wayfindinglib.api.control import ObservatoryControl
    from wayfindinglib.api.execution import ObservationExecution
    from wayfindinglib.api.planning import ObservationPlanning
    from wayfindinglib.drivers.indi_interface import IndiInterface
    from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface
    from wayfindinglib.models.control_status import (
        EquipmentStatus,
        GuidingStatus,
        ImagingStatus,
        SafetyStatus,
    )
    from wayfindinglib.models.session.telemetry import MountPointingModel
    from wayfindinglib.models.sky_position import SkyPosition

try:
    # Single source of truth is pyproject.toml; both libraries ship from the
    # same `astrometrics` distribution, so neither carries its own literal.
    __version__ = _distribution_version("astrometrics")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = [
    "EquipmentStatus",
    "GuidingStatus",
    "ImagingStatus",
    "IndiInterface",
    "MountPointingModel",
    "ObservationExecution",
    "ObservationPlanning",
    "ObservatoryControl",
    "SafetyStatus",
    "SimulatorIndiInterface",
    "SkyPosition",
    "Wayfinder",
]

_LAZY_EXPORTS = {
    "IndiInterface": "wayfindinglib.drivers.indi_interface",
    "SimulatorIndiInterface": "wayfindinglib.drivers.simulators.indi_simulator",
    "ObservatoryControl": "wayfindinglib.api.control",
    "ObservationPlanning": "wayfindinglib.api.planning",
    "ObservationExecution": "wayfindinglib.api.execution",
    "SkyPosition": "wayfindinglib.models.sky_position",
    "MountPointingModel": "wayfindinglib.models.session.telemetry",
    "ImagingStatus": "wayfindinglib.models.control_status",
    "GuidingStatus": "wayfindinglib.models.control_status",
    "SafetyStatus": "wayfindinglib.models.control_status",
    "EquipmentStatus": "wayfindinglib.models.control_status",
}
"""Export name -> the module that defines it."""


def __getattr__(name: str) -> type:
    """Load an export on first use, so no hardware code loads early.

    Parameters
    ----------
    name : `str`
        The attribute asked for.

    Returns
    -------
    resolved : `type`
        The exported class.

    Raises
    ------
    AttributeError
        If `name` is not an export of this package.
    """
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(_LAZY_EXPORTS[name]), name)


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

    def __init__(
        self,
        config: AppConfiguration | None = None,
        app_config: AppConfiguration | None = None,
        astrometrics: Astrometrics | None = None,
    ) -> None:
        """Initialize the Wayfinder high-level interface.

        Description: Composes the three root-function high-level interfaces
        (control, planning, execution) over a shared config, recording
        butler, and `Astrometrics` handle.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The application configuration. Loaded when omitted.
        app_config : `AppConfiguration`, optional
            Another way to pass the configuration.
        astrometrics : `Astrometrics`, optional
            The science library handle every part of the Wayfinder uses, so
            all of them see the same target catalog. A new one is built over
            the configuration when omitted.
        """
        from astrometricslib import Astrometrics, get_configuration
        from wayfindinglib.api.control import ObservatoryControl
        from wayfindinglib.api.execution import ObservationExecution
        from wayfindinglib.api.planning import ObservationPlanning
        from wayfindinglib.drivers.butler import DiskButler

        self.config = config or app_config or get_configuration()

        self.astrometrics = astrometrics or Astrometrics(self.config)

        butler = DiskButler(app_config=self.config)
        self.control = ObservatoryControl(config=self.config, butler=butler, astrometrics=self.astrometrics)
        self.planning = ObservationPlanning(butler=butler, astrometrics=self.astrometrics)
        self.execution = ObservationExecution(butler=butler, astrometrics=self.astrometrics)
