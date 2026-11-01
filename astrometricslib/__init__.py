"""The main Astrometrics software library.

This library is a collection of tools for processing astronomical images.
It handles everything from aligning and stacking images (calibration),
to figuring out exactly what stars are in the picture (plate-solving),
to measuring star brightness and colors (photometry and spectroscopy).

To use the library, just import `Astrometrics` from here. It acts as the
main control panel, giving you access to all the sub-tools like targets,
stars, and image processing.
"""

import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import TYPE_CHECKING, Any

try:
    # The version number is only written down once, in pyproject.toml.
    # astrometricslib and wayfindinglib are installed together as one
    # package named "astrometrics", so both read that same version number
    # here instead of each hard-coding their own copy of it.
    __version__ = _distribution_version("astrometrics")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

from astrometricslib.api import AbstractCatalogAccess, CatalogAccess
from astrometricslib.drivers.interfaces import (
    GaiaXpDriver,
    PlateSolveDriver,
    ReddeningDriver,
    SimbadDriver,
    StackingDriver,
)
from astrometricslib.foundation.astropy_setup import configure_offline_iers, warm_earth_orientation_data
from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.jobs import register_interrupted_job_cleanup
from astrometricslib.foundation.logging import configure_logging
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking.post_processing.interrupted_jobs import restore_interrupted_stacks

if TYPE_CHECKING:
    from astrometricslib.api.jobs import Jobs
    from astrometricslib.api.processing import CalibrationCatalog, ProcessingPipelines
    from astrometricslib.api.quality import QualityRecords
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.api.targets import TargetCatalog
    from astrometricslib.api.visualization import Visualization

# A library only writes log messages. A program decides where they go, by
# calling `configure_logging`. The null handler stops Python from printing a
# "no handlers" warning when no program has done so.
logging.getLogger(__name__).addHandler(logging.NullHandler())

# Every program that uses the library works offline: astropy uses its bundled
# Earth-rotation table instead of downloading a new one.
configure_offline_iers()

# A restack that a program left half done has parked the old stack. Closing
# the job puts it back.
register_interrupted_job_cleanup(restore_interrupted_stacks)

_DEFERRED_EXPORTS = {
    "CalibrationCatalog": "astrometricslib.api.processing",
    "ProcessingPipelines": "astrometricslib.api.processing",
    "QualityRecords": "astrometricslib.api.quality",
    "StellarCatalog": "astrometricslib.api.stars",
    "TargetCatalog": "astrometricslib.api.targets",
    "Visualization": "astrometricslib.api.visualization",
}


def __getattr__(name: str) -> Any:
    """Load certain tools only when they are actually needed.

    Some tools take a long time to load. This function makes sure we
    only load them if someone actually tries to use them.

    Parameters
    ----------
    name : `str`
        The name of the tool being requested.

    Returns
    -------
    resolved : `Any`
        The loaded tool.

    Raises
    ------
    AttributeError
        If the tool name is not recognized.
    """
    module_name = _DEFERRED_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


class Astrometrics:
    """The main control panel for the library.

    This class only builds the sub-APIs and holds them. It has no methods
    of its own: stacking, preview pictures and the analysis stages live on
    `processing`, the target list on `targets`, the star catalog on
    `stars`, pictures and plots on `visualization`, the job history on
    `jobs`, and quality reads on `quality`. Each sub-API is built once
    here and shared, so they all read and write the same storage.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The application settings. If not given, the default settings are
        loaded.
    catalog_access : `AbstractCatalogAccess`, optional
        The database used to save and load data. If not given, a default
        one is built from ``config``.
    plate_solve_driver : `PlateSolveDriver`, optional
        The plate solver. If not given, Astrometry.net is used.
    stacking_driver : `StackingDriver`, optional
        The stacking program. If not given, Siril is used.
    simbad_driver : `SimbadDriver`, optional
        The SIMBAD database client. If not given, astroquery is used.
    gaia_xp_driver : `GaiaXpDriver`, optional
        The source of Gaia DR3 XP spectra. If not given, astroquery is used,
        with the spectra cached in the library's data folder.
    reddening_driver : `ReddeningDriver`, optional
        The source of interstellar reddening values. If not given, Gaia DR3
        is queried through astroquery.

    Attributes
    ----------
    targets : `TargetCatalog`
        Observation targets, folder rescanning, and frame records.
    stars : `StellarCatalog`
        Identified stars, catalog queries, and plate solving.
    processing : `ProcessingPipelines`
        Frame calibration, stacking, and scientific analysis stages.
    visualization : `Visualization`
        Rendering FITS images to PNGs/data URLs and diagnostic plots.
    jobs : `Jobs`
        Read-only tracking of running and completed processing jobs
        and lineage.
    quality : `QualityRecords`
        The start of every quality read: stored measurements, the metric
        list, and live checks of raw frames and stacks.

    Notes
    -----
    The drivers are for one process. Processing several targets at once
    starts worker processes that cannot receive them, so that call is
    refused when any driver was given.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        catalog_access: AbstractCatalogAccess | None = None,
        *,
        plate_solve_driver: PlateSolveDriver | None = None,
        stacking_driver: StackingDriver | None = None,
        simbad_driver: SimbadDriver | None = None,
        gaia_xp_driver: GaiaXpDriver | None = None,
        reddening_driver: ReddeningDriver | None = None,
    ) -> None:
        from astrometricslib.api.jobs import Jobs
        from astrometricslib.api.processing import ProcessingPipelines
        from astrometricslib.api.quality import QualityRecords
        from astrometricslib.api.stars import StellarCatalog
        from astrometricslib.api.targets import TargetCatalog
        from astrometricslib.api.visualization import Visualization
        from astrometricslib.drivers.driver_set import Drivers

        self.config = config or get_configuration()
        self._catalog_access = catalog_access or CatalogAccess(self.config)
        drivers = Drivers(
            plate_solve=plate_solve_driver,
            stacking=stacking_driver,
            simbad=simbad_driver,
            gaia_xp=gaia_xp_driver,
            reddening=reddening_driver,
        )

        # There is deliberately no in-memory copy of the star catalog here.
        # The database is the one copy, reached through `self.stars`, which
        # answers each question with a query for just the stars it needs.
        self.targets = TargetCatalog(self.config, self._catalog_access, drivers=drivers)
        self.stars = StellarCatalog(self.config, self._catalog_access)
        self.processing = ProcessingPipelines(
            self.config, self._catalog_access, targets=self.targets, drivers=drivers
        )
        self.visualization = Visualization(
            self.config, self._catalog_access, targets=self.targets, stars=self.stars
        )
        self.quality = QualityRecords(
            self.config,
            self._catalog_access,
            targets=self.targets,
            calibration_library=lambda: self.processing.calibration._calibration_library,
        )
        self.jobs = Jobs(self.config, self._catalog_access)


__all__ = [
    "AppConfiguration",
    "Astrometrics",
    "CalibrationCatalog",
    "FilterType",
    "FrameRecord",
    "ProcessingPipelines",
    "QualityRecords",
    "StellarCatalog",
    "StellarObject",
    "Target",
    "TargetCatalog",
    "Visualization",
    "configure_logging",
    "configure_offline_iers",
    "get_configuration",
    "warm_earth_orientation_data",
]
