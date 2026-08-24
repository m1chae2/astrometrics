"""Astrometrics domain library.

A standalone, high-performance library for astronomical image
calibration, plate-solving, photometry, and spectroscopy pipelines.

`astrometricslib.api` and every other subpackage are internal --
import everything from this top-level namespace instead. `Astrometrics`
is the entry point; its five domain namespaces (`targets`, `stars`,
`moving_objects`, `processing`, `visualization`) are where nearly all
work happens.

`AstrometryPipeline` and `StarIdentifier` are resolved lazily (module
`__getattr__`, PEP 562): both transitively import `astroquery`/`pyvo`,
which dominate this package's import cost, and most callers never
touch them directly -- they reach the same functionality through
`Astrometrics.processing.run_astrometry`.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import TYPE_CHECKING, Any

try:
    # Single source of truth is pyproject.toml; both libraries ship from the
    # same `astrometrics` distribution, so neither carries its own literal.
    __version__ = _distribution_version("astrometrics")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

from astrometricslib.data_access.butler import AbstractButler, DiskButler
from astrometricslib.data_access.frame_scanning import classify_and_sort_fits_files
from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
from astrometricslib.drivers.siril_interface import ImageProcessing
from astrometricslib.models.moving_object import AsteroidRecoveryCandidate
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.models.quality_summary import (
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    TargetSessionContribution,
)
from astrometricslib.models.stellar_source import (
    AnalysisResult,
    FileItem,
    GroupedFrameStat,
    LightCurve,
    PlotData,
    SpectralObservation,
    StellarObject,
    TargetFilesResponse,
    VariableCandidate,
)
from astrometricslib.models.target import (
    FitsHeaderEntry,
    FrameRecord,
    MosaicInfo,
    RenderedImage,
    Target,
)
from astrometricslib.tasks.target_tasks.target_session_tasks import derive_target_sessions
from astrometricslib.utilities.concurrency import resolve_worker_counts
from astrometricslib.utilities.config_loader import AppConfiguration, get_configuration
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.enums import FilterType
from astrometricslib.utilities.parallel_batch import BatchRunSummary, run_parallel_batch
from astrometricslib.utilities.pipeline_models import ProcessingJob

if TYPE_CHECKING:
    from astrometricslib.api.moving_objects import MovingObjectRecovery
    from astrometricslib.api.processing import CalibrationCatalog, ProcessingPipelines, QualityDiagnostics
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.api.targets import TargetCatalog
    from astrometricslib.api.visualization import Visualization
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import AstrometryPipeline
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

_LAZY_EXPORTS = {
    "AstrometryPipeline": "astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline",
    "StarIdentifier": "astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier",
    "CalibrationCatalog": "astrometricslib.api.processing",
    "ProcessingPipelines": "astrometricslib.api.processing",
    "QualityDiagnostics": "astrometricslib.api.processing",
    "MovingObjectRecovery": "astrometricslib.api.moving_objects",
    "StellarCatalog": "astrometricslib.api.stars",
    "TargetCatalog": "astrometricslib.api.targets",
    "Visualization": "astrometricslib.api.visualization",
}


def __getattr__(name: str) -> Any:
    """Lazily resolve an export whose module pulls in a heavy dependency.

    Parameters
    ----------
    name : `str`
        The attribute name being resolved.

    Returns
    -------
    resolved : `Any`
        The resolved export.

    Raises
    ------
    AttributeError
        Raised if `name` is not a lazily-resolved export.
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


class Astrometrics:
    """Canonical entry point for the high-level interface domain library.

    Composes the target, stellar, moving-object, processing, and
    visualization domain namespaces directly.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        config: AppConfiguration | None = None,
        app_config: AppConfiguration | None = None,
        butler: AbstractButler | None = None,
    ):
        """Initialize the high-level interface.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            Application configuration. Loaded from the application
            configuration when both `config` and `app_config` are
            omitted.
        app_config : `AppConfiguration`, optional
            Alias for `config`, kept for callers that pass it by name.
        butler : `AbstractButler`, optional
            Storage backend for the target and stellar catalogs. A
            `DiskButler` over `config` is constructed when omitted.
        """
        from astrometricslib.api.moving_objects import MovingObjectRecovery
        from astrometricslib.api.processing import ProcessingPipelines
        from astrometricslib.api.stars import StellarCatalog
        from astrometricslib.api.targets import TargetCatalog
        from astrometricslib.api.visualization import Visualization
        from astrometricslib.data_access.butler import DiskButler
        from astrometricslib.drivers import disk_interface
        from astrometricslib.utilities.config_loader import get_configuration

        self.config = config or app_config or get_configuration()
        self.butler = butler or DiskButler(self.config)

        # Run database upgrade verification on startup
        disk_interface.verify_and_upgrade_database(self.config)

        # Hydrate the stellar object registry via butler; target state
        # is owned by TargetCatalog itself (see its docstring).
        self.stellar_objects: list[StellarObject] = self.butler.get("stellar_catalog", {}) or []

        self.targets = TargetCatalog(self.config, self.butler)
        self.stars = StellarCatalog(self.config, butler=self.butler)
        self.moving_objects = MovingObjectRecovery()
        self.processing = ProcessingPipelines(self.config)
        self.visualization = Visualization(self)

    def process_all_targets(self, target_ids: list[str] | None = None, *, camera_name: str) -> Any:
        """Process many targets' full stacking/analysis pipelines.

        Delegates to `batch_processing_operations`, which wires target
        ids and resolved worker counts into the generic
        `astrometricslib.utilities.parallel_batch` engine and runs the
        targets concurrently.

        Parameters
        ----------
        target_ids : `list` [`str`], optional
            Target ids to process; defaults to every target currently
            in the catalog.
        camera_name : `str`
            Case-insensitive substring matched against each frame's
            camera name; only matching frames are processed for each
            target, and every other frame -- including from a second
            camera the target was also captured with -- is silently
            excluded. Required, keyword-only, and has no default: a
            multi-camera target silently dropping most of its frames
            under an unnoticed fallback is worse than a caller being
            forced to say which camera they mean. Use
            `TargetCatalog.list_camera_names` to see what's actually
            present in the catalog before choosing one.

        Returns
        -------
        summary : `BatchRunSummary`
            Aggregated success/failure/result state across all
            targets.
        """
        from astrometricslib.tasks.target_tasks import batch_processing_tasks as batch_processing_operations

        return batch_processing_operations.process_all_targets(self, target_ids, camera_name=camera_name)


__all__ = [
    "AbstractButler",
    "AnalysisResult",
    "AppConfiguration",
    "AsteroidRecoveryCandidate",
    "Astrometrics",
    "AstrometryPipeline",
    "AstrometryPipelineQualityMetrics",
    "AstrometryQualitySummary",
    "BatchRunSummary",
    "CalibrationCatalog",
    "DbLogHandler",
    "DiskButler",
    "FileItem",
    "FilterType",
    "FitsHeaderEntry",
    "FrameRecord",
    "GroupedFrameStat",
    "ImageProcessing",
    "LightCurve",
    "LoggerInterface",
    "MosaicInfo",
    "MovingObjectConfig",
    "MovingObjectRecovery",
    "PlotData",
    "ProcessingJob",
    "ProcessingPipelines",
    "QualityDiagnostics",
    "RenderedImage",
    "SpectralObservation",
    "StarIdentifier",
    "StellarCatalog",
    "StellarObject",
    "Target",
    "TargetCatalog",
    "TargetFilesResponse",
    "TargetSessionContribution",
    "VariableCandidate",
    "Visualization",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
    "get_configuration",
    "parse_coordinate_string",
    "resolve_worker_counts",
    "run_parallel_batch",
]
