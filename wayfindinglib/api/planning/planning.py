"""Purpose: `ObservationPlanning`, the entry point for deciding what to see.

Description: `Wayfinder.planning` is an `ObservationPlanning`. It holds four
objects, each in a module of its own:

* ``sessions`` (`PlanningSessions`, ``sessions.py``): the night plans, one
  per observing night: its queue, checklist and framing.
* ``packages`` (`PlanningPackages`, ``packages.py``): the reusable imaging
  requests, the library's suggestions, depth goals and mosaics.
* ``sky`` (`PlanningSky`, ``sky.py``): where objects are, tonight's ranking,
  the constellation lines and mosaic panel centers.
* ``catalogs`` (`PlanningCatalogs`, ``catalogs.py``): the online catalog
  drivers, the sources around a point and the deep-star catalog.

Planning never talks to hardware. A plan made by hand and one placed
automatically write the same queue, so the execution runs either one. Each
method is a thin wrapper of one owner function under
`wayfindinglib.tasks.planning_tasks`, which its docstring names.
"""

from astrometricslib import AppConfiguration, Astrometrics
from wayfindinglib._internal.renames import MovedNames
from wayfindinglib.api.planning.catalogs import PlanningCatalogs
from wayfindinglib.api.planning.packages import PlanningPackages
from wayfindinglib.api.planning.sessions import PlanningSessions
from wayfindinglib.api.planning.sky import PlanningSky
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.tasks.planning_tasks.planning_context import PlanningContext

__all__ = ["ObservationPlanning"]


class ObservationPlanning(MovedNames):
    """Decide what to observe: night plans, packages, the sky and catalogs.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The application configuration. Taken from ``butler``, or loaded,
        when omitted.
    butler : `DiskButler`, optional
        Stores packages and sessions. Built over ``config`` when omitted.
    astrometrics : `Astrometrics`, optional
        The science library handle shared with the rest of the `Wayfinder`.
        Built over ``config`` on first use when omitted.
    planning_config : `PlanningConfig`, optional
        Settings for automatic placement, such as the twilight used to bound
        the night.

    Attributes
    ----------
    sessions : `PlanningSessions`
        The night plans.
    packages : `PlanningPackages`
        The observation packages.
    sky : `PlanningSky`
        Where things are in the sky.
    catalogs : `PlanningCatalogs`
        The sky catalogs.
    """

    sessions: PlanningSessions
    packages: PlanningPackages
    sky: PlanningSky
    catalogs: PlanningCatalogs

    def __init__(
        self,
        config: AppConfiguration | None = None,
        butler: DiskButler | None = None,
        *,
        astrometrics: Astrometrics | None = None,
        planning_config: PlanningConfig | None = None,
    ) -> None:
        if config is None:
            if butler is not None:
                config = butler.config
            else:
                from astrometricslib import get_configuration

                config = get_configuration()
        self._context = PlanningContext(
            config, butler or DiskButler(app_config=config), astrometrics, planning_config
        )
        self.sessions = PlanningSessions(self._context)
        self.packages = PlanningPackages(self._context)
        self.sky = PlanningSky(self._context)
        self.catalogs = PlanningCatalogs(self._context)
