"""Process, measure and catalog astronomical images.

`astrometricslib` provides utilities for a target's stack and performs
astrometry (finding where the image points), photometry (measuring star
brightness) and spectroscopy (splitting star light by color). It also
identifies moving objects. It keeps the database of the targets that have
been imaged and of the stellar objects that have been identified. It manages
the calibration images (darks, flats and bias) that improve the analysis. For
every major processing stage it gives quality metrics that help you judge how
useful the images are. It measures and reports; it gives no advice about what
to image next. That is the job of `wayfindinglib`.

To use the library, build the entry point with no arguments. It loads the
settings by itself::

    from astrometricslib import Astrometrics

    astrometrics = Astrometrics()

`Astrometrics` has one attribute for each sub-API:

- `targets` (`TargetCatalog`): create, read and save targets, and keep their
  frame lists current.
- `stars` (`StellarCatalog`): the library's star records, catalog queries
  and light-curve (brightness over time) analysis.
- `processing` (`ProcessingPipelines`): stack a target, remake its preview
  and run the analysis stages and noise measurements. Its `calibration`
  child (`CalibrationCatalog`) manages darks, biases and flats.
- `quality` (`QualityRecords`): stored quality measurements, the metric list
  and live checks of raw frames and stacks.
- `visualization` (`Visualization`): draw frames and stacks as pictures and
  plot results.
- `jobs` (`Jobs`): read the history of running and finished jobs.

The sub-APIs work on three core records: a `Target` (a place on the sky,
with its frames and stacks), the `FrameRecord`s that are part of it, and a
`StellarObject` for each identified star.

What to import from where
-------------------------
The package root holds 16 names:

- the entry point and setup: `Astrometrics`, `AppConfiguration`,
  `get_configuration`, `configure_logging`, `configure_offline_iers` and
  `warm_earth_orientation_data`;
- the sub-API classes, which you normally reach as attributes of
  `Astrometrics` (`TargetCatalog`, `StellarCatalog`, `ProcessingPipelines`,
  `CalibrationCatalog`, `QualityRecords` and `Visualization`);
- the core records: `Target`, `FrameRecord`, `StellarObject` and
  `FilterType`.

Four public modules hold the rest:

- `astrometricslib.exceptions`: the errors a script catches, and the helpers
  that turn an error into a plain record and back;
- `astrometricslib.reports`: the quality reports and row shapes that checks
  return, plus `CaptureSetting` and `observing_night_id`;
- `astrometricslib.jobs`: the job history, and the tools that run a function
  as a tracked background job;
- `astrometricslib.drivers`: the data store and file tools that other apps
  share.

Everything else is private, including `astrometricslib._internal`. Only
`astrometricslib` itself may import it. The result models that the calls
return are private too: use their fields, as listed on the API reference
page, and do not import the classes.
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
from astrometricslib.foundation.astropy_setup import configure_offline_iers, warm_earth_orientation_data
from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.jobs import register_interrupted_job_cleanup
from astrometricslib.foundation.logging import configure_logging
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking.post_processing.interrupted_jobs import restore_interrupted_stacks

if TYPE_CHECKING:
    from astrometricslib._internal.drivers.driver_set import Drivers
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

    Attributes
    ----------
    targets : `TargetCatalog`
        Observation targets, folder rescanning, and frame records.
    stars : `StellarCatalog`
        Identified stars, catalog queries, and light-curve analysis.
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
    The outside programs and services the library talks to (the plate
    solver, Siril, SIMBAD and the Gaia services) cannot be swapped through
    this signature. A test swaps them through
    `astrometricslib._internal.testing.astrometrics_with_drivers`.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        catalog_access: AbstractCatalogAccess | None = None,
    ) -> None:
        from astrometricslib._internal.drivers.driver_set import Drivers

        self._build(config, catalog_access, Drivers())

    def _build(
        self,
        config: AppConfiguration | None,
        catalog_access: AbstractCatalogAccess | None,
        drivers: Drivers,
    ) -> None:
        """Build the sub-APIs over the given drivers.

        Parameters
        ----------
        config : `AppConfiguration` or `None`
            The application settings. The default settings are loaded if
            `None`.
        catalog_access : `AbstractCatalogAccess` or `None`
            The database. A default one is built if `None`.
        drivers : `Drivers`
            The outside programs and services to use. `Drivers()` means the
            built-in ones.
        """
        from astrometricslib.api.jobs import Jobs
        from astrometricslib.api.processing import ProcessingPipelines
        from astrometricslib.api.quality import QualityRecords
        from astrometricslib.api.stars import StellarCatalog
        from astrometricslib.api.targets import TargetCatalog
        from astrometricslib.api.visualization import Visualization

        self.config = config or get_configuration()
        self._catalog_access = catalog_access or CatalogAccess(self.config)

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
