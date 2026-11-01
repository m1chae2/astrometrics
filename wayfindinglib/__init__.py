"""Purpose: The Wayfinder, the entry point to the wayfinding library.

Description: `Wayfinder` composes the library's four sub-APIs:
`control` (operate the observatory), `planning` (decide what to
observe), `execution` (run and recover an observing session) and
`review` (how the nights went). `control` has six children named by
topic: `mount`, `imaging`, `guiding`, `safety`, `equipment` and `remote`.

What to import from where
-------------------------
The package root holds 6 names: `Wayfinder`, the three sub-API classes
(`ObservatoryControl`, `ObservationPlanning` and `ObservationExecution`),
and the two records a script builds or reads most (`ObservationSession` and
`ObservationPackage`). The review sub-API is reached as `Wayfinder.review`.
Two public modules hold the rest:

- `wayfindinglib.exceptions`: the errors that belong to this library. The
  shared categories (`NotFoundError`, `InvalidArgumentError` and the rest)
  come from `astrometricslib.exceptions`.
- `wayfindinglib.reports`: the states and levels that results report
  (`RunState`, `PackageStatus`, `DepthGoalState` and `DepthLevel`).

Everything else is private. The result models that the calls return are
private too: use their fields, as listed on the API reference page, and do
not import the classes. This library uses only the public names of
`astrometricslib`.

Every name below except `Wayfinder` is loaded on first use (module
`__getattr__`, PEP 562) from one lookup table. Importing a submodule
such as `wayfindinglib.api.planning` runs this file first, so an eager
import of the INDI drivers here would make every import of the library
load hardware code. The `TYPE_CHECKING` imports never run; they only let
type checkers see each name in `__all__`.
"""

import importlib
import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import TYPE_CHECKING

from astrometricslib import configure_offline_iers

# A library only writes log messages. A program decides where they go, by
# calling `configure_logging`. The null handler stops Python from printing a
# "no handlers" warning when no program has done so.
logging.getLogger(__name__).addHandler(logging.NullHandler())

# Every program that uses the library works offline: astropy uses its bundled
# Earth-rotation table instead of downloading a new one.
configure_offline_iers()

if TYPE_CHECKING:
    from astrometricslib import AppConfiguration, Astrometrics
    from wayfindinglib.api.control import ObservatoryControl
    from wayfindinglib.api.execution import ObservationExecution
    from wayfindinglib.api.planning import ObservationPlanning
    from wayfindinglib.models.planning.observation_package import ObservationPackage
    from wayfindinglib.models.session.observation_session import ObservationSession

try:
    # Single source of truth is pyproject.toml; both libraries ship from the
    # same `astrometrics` distribution, so neither carries its own literal.
    __version__ = _distribution_version("astrometrics")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

_LAZY_EXPORTS = {
    "ObservatoryControl": "wayfindinglib.api.control",
    "ObservationPlanning": "wayfindinglib.api.planning",
    "ObservationExecution": "wayfindinglib.api.execution",
    "ObservationPackage": "wayfindinglib.models.planning.observation_package",
    "ObservationSession": "wayfindinglib.models.session.observation_session",
}
"""Export name -> the module that defines it."""

__all__ = sorted([*_LAZY_EXPORTS, "Wayfinder"])


def __getattr__(name: str) -> object:
    """Load an export on first use, so no hardware code loads early.

    Parameters
    ----------
    name : `str`
        The attribute asked for.

    Returns
    -------
    resolved : `object`
        The exported class or function.

    Raises
    ------
    AttributeError
        If `name` is not an export of this package.
    """
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(_LAZY_EXPORTS[name]), name)


class Wayfinder:
    """The entry point to the wayfinding library.

    Builds and holds the four sub-APIs, `control`, `planning`,
    `execution` and `review`, over one configuration, one `DiskButler` and
    one `Astrometrics` handle, so all four see the same records. It has no
    methods of its own.

    The watchdog (`wayfindinglib/watchdog/`) is not built here on
    purpose: it must not be importable by the code it watches.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        astrometrics: Astrometrics | None = None,
        *,
        simulated: bool | None = None,
        separate_process: bool = False,
    ) -> None:
        """Build the four sub-APIs over a shared configuration and storage.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The application configuration. Loaded when omitted.
        astrometrics : `Astrometrics`, optional
            The science library handle every part of the Wayfinder uses, so
            all of them see the same target catalog. A new one is built over
            the configuration when omitted.
        simulated : `bool`, optional
            Use the simulated INDI session instead of the real hardware.
            When omitted, the simulator is used if ``ASTROMETRICS_TESTING``
            is set in the environment.
        separate_process : `bool`, optional
            Run the INDI session in a process of its own. The library
            starts it on the first call that needs INDI, and
            ``control.close()`` stops it. A program with a busy event loop,
            such as the backend, should ask for it: the INDI client
            library holds Python's global lock during its blocking calls
            and would freeze everything else. `False` by default.

        Attributes
        ----------
        control : `ObservatoryControl`
            Direct hardware commanding across mount, imaging, guiding,
            remote, safety, and equipment.
        planning : `ObservationPlanning`
            Target visibility, sky catalogs, mosaic calculation, and queue
            sequence planning.
        execution : `ObservationExecution`
            Session queue advancement, meridian flip automation, and fault
            recovery.
        review : `ObservationReview`
            Past and current observing nights, alignment sessions and the
            equipment's performance limits.
        """
        from astrometricslib import Astrometrics, get_configuration
        from wayfindinglib.api.control import ObservatoryControl
        from wayfindinglib.api.execution import ObservationExecution
        from wayfindinglib.api.planning import ObservationPlanning
        from wayfindinglib.api.review import ObservationReview
        from wayfindinglib.drivers.butler import DiskButler

        self.config = config or get_configuration()

        self.astrometrics = astrometrics or Astrometrics(self.config)

        butler = DiskButler(app_config=self.config)
        self.control = ObservatoryControl(
            config=self.config,
            butler=butler,
            astrometrics=self.astrometrics,
            simulated=simulated,
            separate_process=separate_process,
        )
        self.planning = ObservationPlanning(self.config, butler, astrometrics=self.astrometrics)
        self.review = ObservationReview(self.control)
        self.execution = ObservationExecution(
            self.config, butler, astrometrics=self.astrometrics, control=self.control
        )
