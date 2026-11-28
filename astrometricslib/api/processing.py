"""Purpose: `ProcessingPipelines`, the processing sub-API, by pipeline.

Description: ``Astrometrics.processing`` holds one object for each pipeline:

* ``stacking`` (`StackingPipeline`, in `_processing_stacking.py`): stack a
  target's frames, predict a stack, summarize one, find a stack by its
  observatory configuration.
* ``enhancement`` (`EnhancementPipeline`, in `_processing_preview.py`): make
  a stack's preview picture, list the objects its stretch can protect.
* ``astrometry``, ``photometry``, ``spectroscopy`` and ``asteroids`` (in
  `_processing_analysis.py`): the analysis pipelines.

There is no call that runs them all: a script, and the app's Analyze Target
button, call each pipeline in turn (stacking, enhancement, astrometry,
photometry, spectroscopy). A stack chooses its darks, biases and flats from
the calibration library, `astrometrics.calibration`, which the root object
builds once and shares with this sub-API. Quality reads start at
`astrometrics.quality`.

Each method is a thin wrapper of one owner function in `pipelines/`, which
its docstring names. A method raises an error from
`astrometricslib.exceptions` when a request cannot be met, and returns a
typed result otherwise.
"""

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.renames import MovedNames
from astrometricslib.api import _processing_stacking as _stacking
from astrometricslib.api._processing_analysis import (
    AsteroidPipeline,
    AstrometryPipeline,
    PhotometryPipeline,
    SpectroscopyPipeline,
)
from astrometricslib.api._processing_preview import EnhancementPipeline
from astrometricslib.api._processing_stacking import StackingPipeline
from astrometricslib.api.calibration import CalibrationCatalog
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.api_arguments import resolve_target

if TYPE_CHECKING:
    from astrometricslib.api.targets import TargetCatalog


__all__ = ["ProcessingPipelines"]


class ProcessingPipelines(MovedNames):
    """The pipelines: stacking, enhancement and the analysis pipelines.

    Every method of a pipeline object takes a `Target`; turn an id into one
    with ``targets.get(id)``. A stack or a frame choice that matches nothing
    raises `NotFoundError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes. The analysis pipelines
        record stars in it.
    targets : `TargetCatalog`, optional
        The target catalog, which saves a target after a stack. One is built
        from ``storage`` when omitted.
    calibration : `CalibrationCatalog`, optional
        The calibration library to share (``astrometrics.calibration``). The
        stack chooses its darks, biases and flats from it. One is built when
        omitted.
    drivers : `Drivers`, optional
        The plate solver, stacking program and SIMBAD driver to use. Any
        left out is the built-in one (Astrometry.net, Siril, astroquery).

    Attributes
    ----------
    stacking : `StackingPipeline`
        Stack frames; predict and summarize stacks.
    enhancement : `EnhancementPipeline`
        Make a stack's preview picture.
    astrometry : `AstrometryPipeline`
        Plate solve stacks and images; name their stars.
    photometry : `PhotometryPipeline`
        Measure star brightness across frames.
    spectroscopy : `SpectroscopyPipeline`
        Extract spectra and follow them across frames.
    asteroids : `AsteroidPipeline`
        The experimental search for moving objects.
    """

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        targets: TargetCatalog | None = None,
        calibration: CalibrationCatalog | None = None,
        drivers: Drivers | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._drivers = drivers or Drivers()
        self._target_catalog = targets
        self._calibration = calibration or CalibrationCatalog(config, storage)
        self.stacking = StackingPipeline(self)
        self.enhancement = EnhancementPipeline(self)
        self.astrometry = AstrometryPipeline(self)
        self.photometry = PhotometryPipeline(self)
        self.spectroscopy = SpectroscopyPipeline(self)
        self.asteroids = AsteroidPipeline(self)

    @property
    def _targets(self) -> TargetCatalog:
        """The target catalog, built from the storage when first needed.

        Returns
        -------
        targets : `TargetCatalog`
            The shared target catalog.
        """
        if self._target_catalog is None:
            from astrometricslib.api.targets import TargetCatalog

            self._target_catalog = TargetCatalog(self._config, self._storage)
        return self._target_catalog

    def _resolve(self, target: str | Target) -> Target:
        """Turn a target name into the `Target` it names, for the scripts.

        Returns
        -------
        target : `Target`
            The target.
        """
        if isinstance(target, Target):
            return target
        return resolve_target(self._targets, target)

    def _job_slot(self) -> AbstractContextManager:
        """Take one of the shared job slots (stacking and analysis share them).

        Returns
        -------
        slot : `AbstractContextManager`
            Enter to wait for a free slot, then hold it while the work runs.
        """
        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        return acquire_resource_slot(self._config, "job", self._config.get_max_concurrent_jobs())

    _library_stack_path = _stacking._library_stack_path
    _subset_context = staticmethod(_stacking._subset_context)
