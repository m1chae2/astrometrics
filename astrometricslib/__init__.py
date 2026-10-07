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
from astrometricslib.drivers.calibration_library import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.drivers.job_logging import (
    background_job,
    capture_job_logs,
    get_current_job,
    registered_job,
    run_as_background_job,
)
from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
from astrometricslib.drivers.provenance_store import ProvenanceStore, export_target_lineage_as_prov_xml
from astrometricslib.foundation.astropy_setup import configure_offline_iers, warm_earth_orientation_data
from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.errors import (
    RPC_CODES,
    AstrometricsError,
    ConfigurationError,
    ConflictError,
    ErrorInfo,
    ExternalServiceError,
    HardwareError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    ProcessingError,
    StorageError,
    error_from_info,
    to_error_info,
)
from astrometricslib.foundation.logging import configure_logging, get_log_context, log_context, new_request_id
from astrometricslib.foundation.paths import is_path_inside, resolve_mounted_path
from astrometricslib.foundation.storage import (
    AbstractButler,
    Butler,
    DatasetSpec,
    DeviceInUseError,
    NumpyEncoder,
    StorageNotMountedError,
    acquire_resource_slot,
    connect_db,
    file_lock,
    require_mounted_storage,
    safe_json_dumps,
)
from astrometricslib.models.catalog_queries import (
    CalibrationQueryResult,
    OverlayStar,
    ReindexReport,
    StarQueryResult,
    TargetQueryResult,
    TargetReindexChange,
    TargetStarCount,
)
from astrometricslib.models.excluded_frames import QuarantinePreview, RestoreReport, SetAsideFrame
from astrometricslib.models.moving_object import AsteroidDetectionCandidate
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.models.processing_results import PreviewRemakeResult, ProcessTargetResult, StackResult
from astrometricslib.models.provenance import (
    Activity,
    ActivityDescription,
    Agent,
    AgentType,
    Collection,
    ConfigFile,
    ConfigFileDescription,
    DatasetDescription,
    DatasetEntity,
    Entity,
    EntityDescription,
    GenerationDescription,
    Parameter,
    ParameterDescription,
    UsageDescription,
    Used,
    ValueDescription,
    ValueEntity,
    WasAssociatedWith,
    WasAttributedTo,
    WasConfiguredBy,
    WasGeneratedBy,
)
from astrometricslib.models.quality_reports import (
    InputQualityReport,
    RawFrameCheckReport,
    SpectralFrameCheckReport,
    StackQualityReport,
    StackSummary,
)
from astrometricslib.models.quality_summary import (
    AppliedCameraProfile,
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    ExposureGroupSummary,
    TargetSessionContribution,
)
from astrometricslib.models.stellar_source import (
    AnalysisResult,
    FileItem,
    GroupedFrameStat,
    PhotometryResult,
    PlotData,
    SpectroscopyResult,
    StellarObject,
    TargetFilesResponse,
    VariableCandidate,
    has_catalog_magnitude,
)
from astrometricslib.models.target import (
    FitsHeaderEntry,
    FrameRecord,
    RenderedImage,
    Target,
    ViewableImage,
)
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files
from astrometricslib.pipelines.shared.interrupted_jobs import close_interrupted_jobs
from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions
from astrometricslib.pipelines.stacking.post_processing.exposure_saturation import (
    SATURATED_BLOB_MINIMUM_PIXELS,
    SATURATED_FRAME_FRACTION,
)
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.exceptions import DATA_ERRORS, ONLINE_QUERY_ERRORS, PlateSolveFailedError
from astrometricslib.utilities.observing_night import observing_night_id
from astrometricslib.utilities.parallel_batch import BatchRunSummary
from astrometricslib.utilities.pipeline_models import ProcessingJob

if TYPE_CHECKING:
    from astrometricslib.api.jobs import Jobs
    from astrometricslib.api.processing import CalibrationCatalog, ProcessingPipelines, QualityDiagnostics
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.api.targets import TargetCatalog
    from astrometricslib.api.visualization import Visualization
    from astrometricslib.drivers.siril_interface import ImageProcessing
    from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
    from astrometricslib.pipelines.astrometry.utilities.catalog_seeding import (
        derive_field_centers,
    )
    from astrometricslib.pipelines.shared.api_arguments import (
        check_choice,
        check_include,
        reject_unused_arguments,
        resolve_target,
        to_epoch_seconds,
    )
    from astrometricslib.pipelines.shared.quality.frame_selection import (
        FrameSelection,
        parse_iso_time,
        select_library_frames,
    )

# A library only writes log messages. A program decides where they go, by
# calling `configure_logging`. The null handler stops Python from printing a
# "no handlers" warning when no program has done so.
logging.getLogger(__name__).addHandler(logging.NullHandler())

# Every program that uses the library works offline: astropy uses its bundled
# Earth-rotation table instead of downloading a new one.
configure_offline_iers()

_DEFERRED_EXPORTS = {
    "ImageProcessing": "astrometricslib.drivers.siril_interface",
    "StarIdentifier": "astrometricslib.pipelines.astrometry.processing.star_identifier",
    "derive_field_centers": "astrometricslib.pipelines.astrometry.utilities.catalog_seeding",
    "CalibrationCatalog": "astrometricslib.api.processing",
    "ProcessingPipelines": "astrometricslib.api.processing",
    "QualityDiagnostics": "astrometricslib.api.processing",
    "Jobs": "astrometricslib.api.jobs",
    "FrameSelection": "astrometricslib.pipelines.shared.quality.frame_selection",
    "parse_iso_time": "astrometricslib.pipelines.shared.quality.frame_selection",
    "check_choice": "astrometricslib.pipelines.shared.api_arguments",
    "check_include": "astrometricslib.pipelines.shared.api_arguments",
    "reject_unused_arguments": "astrometricslib.pipelines.shared.api_arguments",
    "resolve_target": "astrometricslib.pipelines.shared.api_arguments",
    "to_epoch_seconds": "astrometricslib.pipelines.shared.api_arguments",
    "select_library_frames": "astrometricslib.pipelines.shared.quality.frame_selection",
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
    `stars`, pictures and plots on `visualization`, and the job history on
    `jobs`. Each sub-API is built once here and shared, so they all read
    and write the same storage.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The application settings. If not given, the default settings are
        loaded.
    catalog_access : `AbstractCatalogAccess`, optional
        The database used to save and load data. If not given, a default
        one is built from ``config``.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        catalog_access: AbstractCatalogAccess | None = None,
    ) -> None:
        from astrometricslib.api.jobs import Jobs
        from astrometricslib.api.processing import ProcessingPipelines
        from astrometricslib.api.stars import StellarCatalog
        from astrometricslib.api.targets import TargetCatalog
        from astrometricslib.api.visualization import Visualization

        self.config = config or get_configuration()
        self.catalog_access = catalog_access or CatalogAccess(self.config)

        # There is deliberately no in-memory copy of the star catalog here.
        # The database is the one copy, reached through `self.stars`, which
        # answers each question with a query for just the stars it needs.
        self.targets = TargetCatalog(self.config, self.catalog_access)
        self.stars = StellarCatalog(self.config, self.catalog_access)
        self.processing = ProcessingPipelines(self.config, self.catalog_access, targets=self.targets)
        self.visualization = Visualization(
            self.config, self.catalog_access, targets=self.targets, stars=self.stars
        )
        self.jobs = Jobs(self.config, self.catalog_access)


__all__ = [
    "DATA_ERRORS",
    "DEFAULT_DARK_TEMPERATURE_TOLERANCE_C",
    "FITS_READ_ERRORS",
    "ONLINE_QUERY_ERRORS",
    "RPC_CODES",
    "SATURATED_BLOB_MINIMUM_PIXELS",
    "SATURATED_FRAME_FRACTION",
    "AbstractButler",
    "AbstractCatalogAccess",
    "Activity",
    "ActivityDescription",
    "Agent",
    "AgentType",
    "AnalysisResult",
    "AppConfiguration",
    "AppliedCameraProfile",
    "AsteroidDetectionCandidate",
    "Astrometrics",
    "AstrometricsError",
    "AstrometryPipelineQualityMetrics",
    "AstrometryQualitySummary",
    "BatchRunSummary",
    "Butler",
    "CalibrationCatalog",
    "CalibrationQueryResult",
    "CatalogAccess",
    "Collection",
    "ConfigFile",
    "ConfigFileDescription",
    "ConfigurationError",
    "ConflictError",
    "DatasetDescription",
    "DatasetEntity",
    "DatasetSpec",
    "DbLogHandler",
    "DeviceInUseError",
    "Entity",
    "EntityDescription",
    "ErrorInfo",
    "ExposureGroupSummary",
    "ExternalServiceError",
    "FileItem",
    "FilterType",
    "FitsHeaderEntry",
    "FrameRecord",
    "FrameSelection",
    "GenerationDescription",
    "GroupedFrameStat",
    "HardwareError",
    "ImageProcessing",
    "InputQualityReport",
    "InvalidArgumentError",
    "Jobs",
    "LoggerInterface",
    "MovingObjectConfig",
    "NotFoundError",
    "NumpyEncoder",
    "OverlayStar",
    "Parameter",
    "ParameterDescription",
    "PermissionDeniedError",
    "PhotometryResult",
    "PlateSolveFailedError",
    "PlotData",
    "PreviewRemakeResult",
    "ProcessTargetResult",
    "ProcessingError",
    "ProcessingJob",
    "ProcessingPipelines",
    "ProvenanceStore",
    "QualityDiagnostics",
    "QuarantinePreview",
    "RawFrameCheckReport",
    "ReindexReport",
    "RenderedImage",
    "RestoreReport",
    "SetAsideFrame",
    "SpectralFrameCheckReport",
    "SpectroscopyResult",
    "StackQualityReport",
    "StackResult",
    "StackSummary",
    "StarIdentifier",
    "StarQueryResult",
    "StellarCatalog",
    "StellarObject",
    "StorageError",
    "StorageNotMountedError",
    "Target",
    "TargetCatalog",
    "TargetFilesResponse",
    "TargetQueryResult",
    "TargetReindexChange",
    "TargetSessionContribution",
    "TargetStarCount",
    "UsageDescription",
    "Used",
    "ValueDescription",
    "ValueEntity",
    "VariableCandidate",
    "ViewableImage",
    "Visualization",
    "WasAssociatedWith",
    "WasAttributedTo",
    "WasConfiguredBy",
    "WasGeneratedBy",
    "acquire_resource_slot",
    "background_job",
    "capture_job_logs",
    "check_choice",
    "check_include",
    "classify_and_sort_fits_files",
    "close_interrupted_jobs",
    "configure_logging",
    "configure_offline_iers",
    "connect_db",
    "derive_field_centers",
    "derive_target_sessions",
    "error_from_info",
    "export_target_lineage_as_prov_xml",
    "file_lock",
    "frame_is_spectral",
    "get_configuration",
    "get_current_job",
    "get_log_context",
    "has_catalog_magnitude",
    "is_path_inside",
    "log_context",
    "new_request_id",
    "observing_night_id",
    "parse_coordinate_string",
    "parse_iso_time",
    "registered_job",
    "reject_unused_arguments",
    "require_mounted_storage",
    "resolve_camera_profile",
    "resolve_mounted_path",
    "resolve_target",
    "run_as_background_job",
    "safe_json_dumps",
    "select_library_frames",
    "to_epoch_seconds",
    "to_error_info",
    "warm_earth_orientation_data",
]
