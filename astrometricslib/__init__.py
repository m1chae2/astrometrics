"""The main Astrometrics software library.

This library is a collection of tools for processing astronomical images.
It handles everything from aligning and stacking images (calibration),
to figuring out exactly what stars are in the picture (plate-solving),
to measuring star brightness and colors (photometry and spectroscopy).

To use the library, just import `Astrometrics` from here. It acts as the
main control panel, giving you access to all the sub-tools like targets,
stars, and image processing.
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
from astrometricslib.drivers.job_logging import JobHandle, capture_job_logs, registered_job
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
from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions
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
    from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
    from astrometricslib.pipelines.astrometry.star_identifier import StarIdentifier

_LAZY_EXPORTS = {
    "AstrometryPipeline": "astrometricslib.pipelines.astrometry.pipeline",
    "StarIdentifier": "astrometricslib.pipelines.astrometry.star_identifier",
    "CalibrationCatalog": "astrometricslib.api.processing",
    "ProcessingPipelines": "astrometricslib.api.processing",
    "QualityDiagnostics": "astrometricslib.api.processing",
    "MovingObjectRecovery": "astrometricslib.api.moving_objects",
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
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


class Astrometrics:
    """The main control panel for the library.

    This class groups all the different tools (like image processing,
    star tracking, and data visualization) together in one place.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        config: AppConfiguration | None = None,
        app_config: AppConfiguration | None = None,
        butler: AbstractButler | None = None,
    ):
        """Set up the main Astrometrics tools.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The application settings. If not provided, it will load the
            default settings.
        app_config : `AppConfiguration`, optional
            Another way to provide the settings.
        butler : `AbstractButler`, optional
            The database tool used to save and load data. If not provided,
            it will create a default one.
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

    def process_all_targets(
        self,
        target_ids: list[str] | None = None,
        *,
        camera_name: str,
        focal_length_mm: float | None = None,
    ) -> Any:
        """Run the full image processing pipeline for multiple targets.

        This runs the image stacking and analysis for many targets at the
        same time, which is much faster than doing them one by one.

        Parameters
        ----------
        target_ids : `list` of `str`, optional
            A list of specific target IDs to process. If not provided,
            it processes every target in the database.
        camera_name : `str`
            The name of the camera used to take the pictures. It will only
            process images taken with this specific camera.

        Returns
        -------
        summary : `BatchRunSummary`
            A report showing which targets succeeded and which failed.
        """
        from astrometricslib.api import batch as batch_processing_operations

        return batch_processing_operations.process_all_targets(
            self, target_ids, camera_name=camera_name, focal_length_mm=focal_length_mm
        )


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
    "JobHandle",
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
    "capture_job_logs",
    "classify_and_sort_fits_files",
    "derive_target_sessions",
    "get_configuration",
    "parse_coordinate_string",
    "registered_job",
    "resolve_worker_counts",
    "run_parallel_batch",
]
