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
import os
from collections.abc import Callable
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
from astrometricslib.api.processing import (
    DbLogHandler,
    ImageProcessing,
    JobHandle,
    LoggerInterface,
    capture_job_logs,
    registered_job,
    run_siril_stack,
    stack_frames,
)
from astrometricslib.api.targets import (
    classify_and_sort_fits_files,
    derive_target_sessions,
    frame_is_spectral,
)
from astrometricslib.drivers.calibration_library import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.job_logging import background_job, get_current_job
from astrometricslib.drivers.provenance_store import ProvenanceStore, export_target_lineage_as_prov_xml
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
    to_error_info,
)
from astrometricslib.foundation.logging import configure_logging, get_log_context, log_context, new_request_id
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
from astrometricslib.models.moving_object import AsteroidDetectionCandidate
from astrometricslib.models.moving_object_config import MovingObjectConfig
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
)
from astrometricslib.models.target import (
    FitsHeaderEntry,
    FrameRecord,
    RenderedImage,
    Target,
)
from astrometricslib.pipelines.shared.interrupted_jobs import close_interrupted_jobs
from astrometricslib.pipelines.stacking.post_processing.exposure_saturation import (
    SATURATED_BLOB_MINIMUM_PIXELS,
    SATURATED_FRAME_FRACTION,
)
from astrometricslib.utilities.concurrency import resolve_worker_counts
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.exceptions import PlateSolveFailedError
from astrometricslib.utilities.observing_night import observing_night_id
from astrometricslib.utilities.parallel_batch import BatchRunSummary, run_parallel_batch
from astrometricslib.utilities.pipeline_models import ProcessingJob

if TYPE_CHECKING:
    from astrometricslib.api.jobs import Jobs
    from astrometricslib.api.moving_objects import MovingObjectRecovery
    from astrometricslib.api.processing import CalibrationCatalog, ProcessingPipelines, QualityDiagnostics
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.api.targets import TargetCatalog
    from astrometricslib.api.visualization import Visualization
    from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
    from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
    from astrometricslib.pipelines.astrometry.utilities.catalog_seeding import (
        derive_field_centers,
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

_DEFERRED_EXPORTS = {
    "AstrometryPipeline": "astrometricslib.pipelines.astrometry.pipeline",
    "StarIdentifier": "astrometricslib.pipelines.astrometry.processing.star_identifier",
    "derive_field_centers": "astrometricslib.pipelines.astrometry.utilities.catalog_seeding",
    "CalibrationCatalog": "astrometricslib.api.processing",
    "ProcessingPipelines": "astrometricslib.api.processing",
    "QualityDiagnostics": "astrometricslib.api.processing",
    "Jobs": "astrometricslib.api.jobs",
    "FrameSelection": "astrometricslib.pipelines.shared.quality.frame_selection",
    "parse_iso_time": "astrometricslib.pipelines.shared.quality.frame_selection",
    "select_library_frames": "astrometricslib.pipelines.shared.quality.frame_selection",
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
    module_name = _DEFERRED_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


class Astrometrics:
    """The main control panel for the library.

    This class groups all the different tools (like image processing,
    star tracking, and data visualization) together in one place.
    """

    def __init__(
        self,
        config: AppConfiguration | None = None,
        app_config: AppConfiguration | None = None,
        catalog_access: AbstractCatalogAccess | None = None,
    ) -> None:
        """Set up the main Astrometrics tools.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The application settings. If not provided, it will load the
            default settings.
        app_config : `AppConfiguration`, optional
            Another way to provide the settings.
        catalog_access : `AbstractCatalogAccess`, optional
            The database tool used to save and load data. If not provided,
            it will create a default one.
        """
        from astrometricslib.api.jobs import Jobs
        from astrometricslib.api.moving_objects import MovingObjectRecovery
        from astrometricslib.api.processing import ProcessingPipelines
        from astrometricslib.api.stars import StellarCatalog
        from astrometricslib.api.targets import TargetCatalog
        from astrometricslib.api.visualization import Visualization
        from astrometricslib.drivers.catalog_access import CatalogAccess
        from astrometricslib.foundation.config import get_configuration

        self.config = config or app_config or get_configuration()
        self.catalog_access = catalog_access or CatalogAccess(self.config)

        # There is deliberately no in-memory copy of the star catalog here.
        # The database is the one copy, reached through `self.stars`, which
        # answers each question with a query for just the stars it needs.
        # A target's own data is loaded separately, since TargetCatalog owns
        # that (see its docstring).
        self.targets = TargetCatalog(self.config, self.catalog_access)
        self.stars = StellarCatalog(self.config, catalog_access=self.catalog_access)
        self.moving_objects = MovingObjectRecovery()
        self.processing = ProcessingPipelines(self.config)
        self.visualization = Visualization(self)
        self.jobs = Jobs(self.config)

    @background_job("stacking", grace_period_seconds=8.0)
    def stack(
        self,
        target: Target,
        frame_type: str = "imaging",
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: str | None = None,
        until: str | None = None,
        force: bool = False,
        plan_only: bool = False,
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
        camera: str | None = None,
    ) -> dict[str, Any]:
        """Stack a target's light frames, choosing which frames go in.

        This runs the same stacking stage as the Stack button in the app:
        it sets aside bad frames, stacks, trims the noisy edges, records the
        quality summary and the preview, and saves the target. Imaging and
        spectroscopy frames are never mixed, so ``frame_type`` picks one.
        Through the MCP server a slow stack returns a job id; follow it with
        ``jobs_query``. An unchanged stack is kept unless ``force`` is set.

        Parameters
        ----------
        target : `Target`
            The target to stack.
        frame_type : `str`, optional
            ``"imaging"`` (default) or ``"spectral"``.
        filter_name : `str`, optional
            Only frames of this filter, such as ``"L"`` or ``"Luminance"``.
        first_file : `str`, optional
            Only frames from this file onward. A bare number such as
            ``"013"`` means frame 013.
        last_file : `str`, optional
            Only frames up to this file or number.
        since : `str`, optional
            Only frames taken at or after this ISO 8601 time (UTC if no
            offset).
        until : `str`, optional
            Only frames taken at or before this ISO 8601 time.
        force : `bool`, optional
            Rebuild even if nothing changed since the stack on disk.
        plan_only : `bool`, optional
            Only report which frames would be stacked. Nothing is stacked
            or saved.
        denoise : `bool`, optional
            For this run's preview picture: `False` skips Cosmic Clarity,
            `True` runs it even if the settings have it switched off. Left
            out, the settings apply. Never written to the settings. It has
            no effect when an unchanged stack is kept (see ``force``).
        denoise_strength : `float`, optional
            Cosmic Clarity's denoise strength for this run, from 0 to 1.
        star_toning : `bool`, optional
            Turn the preview's star toning on or off for this run.

        camera : `str`, optional
            Only frames from this camera, such as ``"ASI 533MM"``. Chosen
            before any other frame check. Frames from different cameras are
            never stacked together, so a target that has frames from more
            than one camera needs this; without it the answer is an error
            that names the cameras and how many frames each took.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``frames_selected``, the first and last file, and, unless
            ``plan_only``, the ``stacked_path`` and the stack's summary. A
            problem comes back under ``error``.
        """
        from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
        from astrometricslib.pipelines.shared.quality.frame_selection import (
            FrameSelection,
            parse_iso_time,
            select_library_frames,
        )
        from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings

        if frame_type not in ("imaging", "spectral"):
            return {"error": "frame_type must be 'imaging' or 'spectral'."}
        if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
            return {"error": "denoise_strength must be between 0 and 1."}
        preview_settings = PreviewSettings(
            denoise=denoise, denoise_strength=denoise_strength, star_toning=star_toning
        )
        spectral = frame_type == "spectral"
        try:
            selection = FrameSelection(
                filter_name=filter_name,
                first_file=first_file,
                last_file=last_file,
                since=parse_iso_time(since),
                until=parse_iso_time(until),
                include_spectra=spectral,
            )
        except ValueError as error:
            return {"error": f"since and until must be ISO 8601 times: {error}"}
        lights = [
            frame
            for frame in target.frames
            if str(frame.role).upper() == "LIGHT"
            and not any(word in frame.path.lower() for word in ("_stacked", "starless", "starmask"))
            and frame_is_spectral(frame) == spectral
        ]
        from astrometricslib.pipelines.stacking.pre_processing.camera_selection import (
            choose_camera_frames,
            describe_cameras,
            split_frames_by_camera,
        )

        if camera:
            lights, camera_problem = choose_camera_frames(lights, camera)
            if camera_problem:
                return {"error": camera_problem}
        chosen = select_library_frames(lights, selection)
        if not chosen:
            return {"error": f"No {frame_type} light frames of {target.id!r} match that selection."}
        cameras_chosen = split_frames_by_camera(chosen)
        if len(cameras_chosen) > 1:
            return {
                "error": (
                    f"The selected frames of {target.id!r} come from more than one camera: "
                    f"{describe_cameras(cameras_chosen)}. Frames from different cameras are never "
                    "stacked together. Pass camera to choose one."
                )
            }
        result: dict[str, Any] = {
            "target_id": target.id,
            "frame_type": frame_type,
            "camera": next(iter(cameras_chosen), None),
            "frames_selected": len(chosen),
            "first_file": os.path.basename(chosen[0].path),
            "last_file": os.path.basename(chosen[-1].path),
        }
        if plan_only:
            return {**result, "plan_only": True, "note": "Nothing was stacked or saved."}
        with self.processing.acquire_stacking_slot():
            stacked_path = self.processing.run_stacking(
                target, frames_to_stack=chosen, force=force, preview_settings=preview_settings
            )
        if stacked_path:
            self.targets.save()
        stacking = target.spectral_stacking if spectral else target.stacking
        quality = getattr(stacking, "quality_summary", None)
        return {
            **result,
            "stacked_path": stacked_path,
            "flagged": getattr(quality, "flagged", None),
            "flag_reasons": getattr(quality, "flag_reasons", []),
            "note": "For the numbers behind this stack, call processing_stack_summary.",
        }

    @background_job("preview", grace_period_seconds=20.0)
    def remake_preview(
        self,
        target: Target,
        frame_type: str = "imaging",
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
        keep_previous: bool = True,
    ) -> dict[str, Any]:
        """Make a target's preview picture again from its existing stack.

        Runs only the preview step (GraXpert, Siril stretch, Cosmic Clarity,
        star toning), so a changed post-processing setting shows up without a
        restack. The stack file is not changed. The overrides apply to this
        run only and are never written to the configuration. By default the
        old pictures are copied into a ``_previous_preview`` folder beside
        the stack first, so the two can be compared; a failed run puts them
        back. Through the MCP server a slow run returns a job id; follow it
        with ``jobs_query``.

        Parameters
        ----------
        target : `Target`
            The target whose stack to make a picture of.
        frame_type : `str`, optional
            ``"imaging"`` (default) or ``"spectral"``.
        denoise : `bool`, optional
            `False` skips Cosmic Clarity for this run. `True` runs it even if
            the settings have it switched off. Left out, the settings apply.
        denoise_strength : `float`, optional
            Cosmic Clarity's denoise strength for this run, from 0 to 1.
        star_toning : `bool`, optional
            Turn the star toning on or off for this run.
        keep_previous : `bool`, optional
            Copy the current pictures aside first. On by default.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``preview_path``, ``processed_fits_path``, ``steps_run``,
            ``shown_in_viewer``, ``previous_pictures`` and
            ``stack_file_unchanged``. A problem comes back under ``error``.
        """
        from astrometricslib.pipelines.stacking.post_processing.preview_remake import remake_stack_preview
        from astrometricslib.pipelines.stacking.post_processing.stack_preview import PreviewSettings

        if frame_type not in ("imaging", "spectral"):
            return {"error": "frame_type must be 'imaging' or 'spectral'."}
        if denoise_strength is not None and not 0.0 <= denoise_strength <= 1.0:
            return {"error": "denoise_strength must be between 0 and 1."}
        settings = PreviewSettings(
            denoise=denoise, denoise_strength=denoise_strength, star_toning=star_toning
        )
        with self.processing.acquire_stacking_slot():
            result = remake_stack_preview(target, frame_type == "spectral", settings, keep_previous)
        if result.get("shown_in_viewer"):
            self.targets.save()
        return result

    @background_job("batch_processing", grace_period_seconds=8.0)
    def process_all_targets(
        self,
        target_ids: list[str] | None = None,
        *,
        camera_name: str,
        focal_length_mm: float | None = None,
        on_item_complete: Callable[[str, dict, int, int], None] | None = None,
    ) -> Any:
        """Run the full image processing pipeline for multiple targets.

        This runs the image stacking and analysis for many targets at the
        same time, which is much faster than doing them one by one.

        Called through the MCP server, this runs as a background job (see
        `astrometricslib.drivers.job_logging.background_job`) rather than
        blocking the caller for the whole batch -- called directly, as
        here, it behaves exactly as before: it blocks until every target
        is done and returns the summary.

        Parameters
        ----------
        target_ids : `list` of `str`, optional
            A list of specific target IDs to process. If not provided,
            it processes every target in the database.
        camera_name : `str`
            The name of the camera used to take the pictures. It will only
            process images taken with this specific camera.
        on_item_complete : `Callable`, optional
            Called as `(target_id, result, completed_count, total_count)`
            after each target finishes, for a caller that wants live
            progress rather than waiting for the whole batch.

        Returns
        -------
        summary : `BatchRunSummary`
            A report showing which targets succeeded and which failed.
        """
        from astrometricslib.api import batch as batch_processing_operations

        return batch_processing_operations.process_all_targets(
            self,
            target_ids,
            camera_name=camera_name,
            focal_length_mm=focal_length_mm,
            on_item_complete=on_item_complete,
        )


__all__ = [
    "DEFAULT_DARK_TEMPERATURE_TOLERANCE_C",
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
    "AstrometryPipeline",
    "AstrometryPipelineQualityMetrics",
    "AstrometryQualitySummary",
    "BatchRunSummary",
    "Butler",
    "CalibrationCatalog",
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
    "InvalidArgumentError",
    "JobHandle",
    "Jobs",
    "LoggerInterface",
    "MovingObjectConfig",
    "MovingObjectRecovery",
    "NotFoundError",
    "NumpyEncoder",
    "Parameter",
    "ParameterDescription",
    "PermissionDeniedError",
    "PhotometryResult",
    "PlateSolveFailedError",
    "PlotData",
    "ProcessingError",
    "ProcessingJob",
    "ProcessingPipelines",
    "ProvenanceStore",
    "QualityDiagnostics",
    "RenderedImage",
    "SpectroscopyResult",
    "StarIdentifier",
    "StellarCatalog",
    "StellarObject",
    "StorageError",
    "StorageNotMountedError",
    "Target",
    "TargetCatalog",
    "TargetFilesResponse",
    "TargetSessionContribution",
    "UsageDescription",
    "Used",
    "ValueDescription",
    "ValueEntity",
    "VariableCandidate",
    "Visualization",
    "WasAssociatedWith",
    "WasAttributedTo",
    "WasConfiguredBy",
    "WasGeneratedBy",
    "acquire_resource_slot",
    "capture_job_logs",
    "classify_and_sort_fits_files",
    "close_interrupted_jobs",
    "configure_logging",
    "connect_db",
    "derive_field_centers",
    "derive_target_sessions",
    "export_target_lineage_as_prov_xml",
    "file_lock",
    "frame_is_spectral",
    "get_configuration",
    "get_log_context",
    "log_context",
    "new_request_id",
    "observing_night_id",
    "parse_coordinate_string",
    "parse_iso_time",
    "registered_job",
    "require_mounted_storage",
    "resolve_camera_profile",
    "resolve_worker_counts",
    "run_parallel_batch",
    "run_siril_stack",
    "safe_json_dumps",
    "select_library_frames",
    "stack_frames",
    "to_error_info",
]
