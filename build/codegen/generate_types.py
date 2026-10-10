"""Auto-generate TypeScript interfaces and enums from backend Pydantic models.

Enforces synchronization between backend data models and frontend
TypeScript contracts. It also writes the backend's public interface from
`backend.public_interface`: the list of RPC method names (`RPC_METHODS`,
`RpcMethod`) and the paths of the other routes (`BACKEND_ROUTES`), so the
UI can only name methods and routes the backend serves.
"""

import os
import sys
from datetime import datetime
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel

# Ensure we can import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from astrometricslib import ErrorInfo, FilterType
from astrometricslib.foundation.jobs.models import ProcessingJob, ProcessStatus
from astrometricslib.models.astrometry_quality import CatalogMatchQuality
from astrometricslib.models.calibration_inventory import CalibrationEntry, CalibrationStats
from astrometricslib.models.catalog_queries import OverlayStar, TargetStarCount
from astrometricslib.models.gate_result import GateResult, GateStatus
from astrometricslib.models.moving_object import (
    AsteroidDetectionCandidate,
    CascadeStage,
    EphemerisMatch,
    FrameDetection,
    MovingObjectTrack,
)
from astrometricslib.models.quality_summary import (
    AppliedCameraProfile,
    AsteroidDetectionPipelineQualityMetrics,
    AsteroidDetectionQualitySummary,
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    ExcludedFrame,
    ExposureGroupSummary,
    FrameEnsembleComposition,
    NoiseModelPoint,
    PhotometryPipelineQualityMetrics,
    PhotometryQualitySummary,
    SpectralClassificationConcern,
    SpectroscopyPipelineQualityMetrics,
    SpectroscopyQualitySummary,
    StackingPipelineQualityMetrics,
    StackQualitySummary,
    TargetSessionContribution,
)
from astrometricslib.models.spectroscopy_quality import (
    CatalogComparison,
    InputQualityAssessment,
    OutputQualityAssessment,
    StageQualityCheckpoint,
    StageQualityMetric,
    StageQualityRollup,
)
from astrometricslib.models.stacking_quality import StackingInputQuality, StackingOutputQuality
from astrometricslib.models.stellar_source import (
    AnalysisResult,
    ExtinctionCorrectionRecord,
    FileItem,
    GroupedFrameStat,
    PeriodogramResult,
    PhotometryResult,
    PlotData,
    SessionPhotometrySummary,
    SpectralExtractionDiagnostics,
    SpectroscopyResult,
    StellarObject,
    StellarSessionMatch,
    TargetFilesResponse,
    TransitCandidate,
    VariableCandidate,
)
from astrometricslib.models.target import (
    AsteroidDetectionResult,
    FitsHeaderEntry,
    FrameMeasurements,
    FrameRecord,
    ImageType,
    RenderedImage,
    StackConfigurationResult,
    StretchParameters,
    Target,
    TargetObjectType,
    TargetQualitySummaries,
    TargetStackingResult,
)
from backend.public_interface import ROUTES, RPC_METHODS
from backend.services.infrastructure.system_status_service import (
    IntrospectionEndpoint,
    IntrospectionMethod,
    ProcessingJobPulse,
    SystemHealth,
    SystemPulse,
    TelescopePulse,
)
from wayfindinglib.drivers.indi_interface import TelescopeStatus
from wayfindinglib.models.equipment_and_site.performance_envelope import (
    PerformanceEnvelope,
    PerformanceThreshold,
    ThresholdStatus,
    ThresholdTier,
    TrackingRiskMap,
)
from wayfindinglib.models.planning.mosaic import MosaicPanel
from wayfindinglib.models.planning.sequence_plan import SequenceItem, SequencePlan
from wayfindinglib.models.planning.sky_source import SkySource
from wayfindinglib.models.planning.visibility import (
    MeridianCrossing,
    MeridianStatus,
    ObjectVisibility,
    SeparationRange,
    TimeSpan,
    VisibilitySample,
    VisibilitySpan,
)
from wayfindinglib.models.session.observation_session import WeatherSample
from wayfindinglib.models.session.telemetry import (
    AlignmentAttempt,
    AlignmentSessionSummary,
    AlignmentTargetSession,
    AlignmentTrackPoint,
    GuidingSample,
    GuidingSpectrumAnalysis,
    GuidingSpectrumPeak,
    GuidingStats,
    IndiStatus,
    LiveGuidingStatus,
    MountPointingModel,
    PolarAlignmentStatus,
)


def get_ts_type(py_type: Any) -> str:
    """Map a Python type to its equivalent TypeScript type.

    Recursively handles standard collection types, Unions, Enums, and
    Pydantic models.

    Parameters
    ----------
    py_type : `Any`
        The Python type annotation to translate.

    Returns
    -------
    ts_type : `str`
        The corresponding TypeScript type string.
    """
    # Base types
    if py_type is str:
        return "string"
    if py_type in (int, float):
        return "number"
    if py_type is bool:
        return "boolean"
    if py_type is Any:
        return "any"
    if py_type is datetime:
        return "string"
    if py_type is type(None) or py_type is None:
        return "null"

    # Enums
    if isinstance(py_type, type) and issubclass(py_type, Enum):
        return py_type.__name__

    origin = get_origin(py_type)
    args = get_args(py_type)

    if origin is Union:
        # Resolve all arguments of the Union to map properly to TS
        # types (including null)
        types = [get_ts_type(a) for a in args]
        unique_types = []
        for t in types:
            if t not in unique_types:
                unique_types.append(t)
        return " | ".join(unique_types)

    if origin is list or origin is list:
        item_type = get_ts_type(args[0]) if args else "any"
        return f"{item_type}[]"

    if origin is dict or origin is dict:
        key_type = get_ts_type(args[0]) if args else "string"
        val_type = get_ts_type(args[1]) if args else "any"
        return f"Record<{key_type}, {val_type}>"

    if isinstance(py_type, type) and issubclass(py_type, BaseModel):
        return py_type.__name__

    return "any"


def generate_enum(enum_cls: type[Enum], name: str) -> str:
    """Generate a TypeScript enum string from a Python Enum class.

    Parameters
    ----------
    enum_cls : `type[Enum]`
        The Python Enum class.
    name : `str`
        The name of the generated TypeScript enum.

    Returns
    -------
    enum_str : `str`
        The TypeScript enum block.
    """
    lines = []
    doc = enum_cls.__doc__ or ""
    if doc:
        lines.append("/**")
        for line in doc.strip().split("\n"):
            lines.append(f" * {line.strip()}")
        lines.append(" */")

    lines.append(f"export enum {name} {{")
    for member in enum_cls:
        if name == "FilterType":
            if member.name not in ("L", "R", "G", "B", "Ha", "OIII", "SII", "SPEC", "NONE"):
                continue
            val = member.name
            if member.name == "NONE":
                val = "None"
        else:
            val = member.value
        lines.append(f'  {member.name} = "{val}",')

    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")
    return "\n".join(lines)


def generate_interface(model: type[BaseModel], name: str) -> str:
    """Generate a TypeScript interface definition from a Pydantic model.

    Parameters
    ----------
    model : `type[BaseModel]`
        The Pydantic model to parse.
    name : `str`
        The name of the output interface.

    Returns
    -------
    interface_str : `str`
        The complete TypeScript interface definition.
    """
    lines = []
    doc = model.__doc__ or ""
    if doc:
        lines.append("/**")
        for line in doc.strip().split("\n"):
            lines.append(f" * {line.strip()}")
        lines.append(" */")

    lines.append(f"export interface {name} {{")

    hints = get_type_hints(model, include_extras=True)
    for field_name, field_info in model.model_fields.items():
        ts_name = field_info.alias or field_name
        optional = not field_info.is_required()
        ts_type = get_ts_type(hints[field_name])

        # Add JSDoc for field if description exists
        if field_info.description:
            lines.append(f"  /** {field_info.description} */")

        lines.append(f"  {ts_name}{'?' if optional else ''}: {ts_type};")

    # Computed fields are sent in every reply too. They are marked optional
    # so the app's own test data need not spell them out.
    for field_name, computed_info in model.model_computed_fields.items():
        ts_name = computed_info.alias or field_name
        if computed_info.description:
            lines.append(f"  /** {computed_info.description.strip().splitlines()[0]} */")
        lines.append(f"  {ts_name}?: {get_ts_type(computed_info.return_type)};")

    # Add flexible index for known models
    if name in ("TelescopeStatus", "TargetObject", "Spectrum"):
        lines.append("  /** Flexible index to accommodate additional data from the backend. */")
        lines.append("  [key: string]: any;")

    lines.append("}")
    return "\n".join(lines)


def generate_public_interface() -> str:
    """Generate the TypeScript list of the backend's methods and routes.

    Returns
    -------
    text : `str`
        ``RPC_METHODS`` and its ``RpcMethod`` type, then
        ``BACKEND_ROUTES``, one path per route name.
    """
    lines = [
        "/**",
        " * Every RPC method the backend serves, from backend/public_interface.py.",
        " */",
        "export const RPC_METHODS = [",
    ]
    lines.extend(f'  "{method}",' for method in RPC_METHODS)
    lines.extend([
        "] as const;",
        "",
        "/** The name of one RPC method the backend serves. */",
        "export type RpcMethod = (typeof RPC_METHODS)[number];",
        "",
        "/**",
        " * The path of every other route the backend serves, by name, from",
        " * backend/public_interface.py.",
        " */",
        "export const BACKEND_ROUTES = {",
    ])
    for route in ROUTES:
        lines.append(f"  /** {route.purpose} */")
        lines.append(f'  {route.name}: "{route.path}",')
    lines.append("} as const;")
    return "\n".join(lines)


def render_types() -> str:
    """Render the TypeScript text for every backend model the UI uses.

    Returns
    -------
    content : `str`
        The full text of ``ui/common/types/backendTypes.ts``.
    """
    header = """/**
 * @fileoverview Auto-generated TypeScript interfaces from Pydantic models,
 * and the backend's public interface (its RPC methods and routes).
 */
"""

    interfaces = [
        generate_enum(FilterType, "FilterType"),
        generate_enum(ImageType, "ImageType"),
        generate_enum(TargetObjectType, "TargetObjectType"),
        generate_interface(ErrorInfo, "ErrorInfo"),
        generate_interface(FrameMeasurements, "FrameMeasurements"),
        generate_interface(FrameRecord, "FrameRecord"),
        generate_interface(TelescopeStatus, "TelescopeStatus"),
        generate_interface(StackConfigurationResult, "StackConfigurationResult"),
        generate_interface(TargetStackingResult, "TargetStackingResult"),
        generate_interface(AsteroidDetectionResult, "AsteroidDetectionResult"),
        generate_interface(TargetQualitySummaries, "TargetQualitySummaries"),
        generate_interface(Target, "TargetObject"),
        generate_interface(FileItem, "FileItem"),
        generate_interface(TargetFilesResponse, "TargetFilesResponse"),
        generate_interface(GroupedFrameStat, "GroupedFrameStat"),
        generate_interface(PlotData, "PlotData"),
        generate_interface(StellarObject, "Spectrum"),
        generate_interface(StellarSessionMatch, "StellarSessionMatch"),
        generate_interface(CatalogMatchQuality, "CatalogMatchQuality"),
        generate_interface(OverlayStar, "OverlayStar"),
        generate_interface(TargetStarCount, "TargetStarCount"),
        generate_interface(SpectroscopyResult, "SpectroscopyResult"),
        generate_interface(SpectralExtractionDiagnostics, "SpectralExtractionDiagnostics"),
        generate_interface(ExtinctionCorrectionRecord, "ExtinctionCorrectionRecord"),
        generate_interface(CatalogComparison, "CatalogComparison"),
        generate_interface(InputQualityAssessment, "InputQualityAssessment"),
        generate_interface(OutputQualityAssessment, "OutputQualityAssessment"),
        generate_interface(StageQualityMetric, "StageQualityMetric"),
        generate_interface(StageQualityCheckpoint, "StageQualityCheckpoint"),
        generate_interface(StageQualityRollup, "StageQualityRollup"),
        generate_interface(PeriodogramResult, "PeriodogramResult"),
        generate_interface(TransitCandidate, "TransitCandidate"),
        generate_interface(SessionPhotometrySummary, "SessionPhotometrySummary"),
        generate_interface(PhotometryResult, "PhotometryResult"),
        generate_interface(TelescopePulse, "TelescopePulse"),
        generate_interface(ProcessingJobPulse, "ProcessingJobPulse"),
        generate_interface(SystemPulse, "SystemPulse"),
        generate_interface(GuidingSample, "GuidingSample"),
        generate_interface(AlignmentAttempt, "AlignmentAttempt"),
        generate_interface(PolarAlignmentStatus, "PolarAlignmentStatus"),
        generate_interface(AlignmentSessionSummary, "AlignmentSessionSummary"),
        generate_interface(AlignmentTrackPoint, "AlignmentTrackPoint"),
        generate_interface(AlignmentTargetSession, "AlignmentTargetSession"),
        generate_interface(MountPointingModel, "MountPointingModel"),
        generate_enum(ThresholdTier, "ThresholdTier"),
        generate_enum(ThresholdStatus, "ThresholdStatus"),
        generate_interface(PerformanceThreshold, "PerformanceThreshold"),
        generate_interface(TrackingRiskMap, "TrackingRiskMap"),
        generate_interface(PerformanceEnvelope, "PerformanceEnvelope"),
        generate_interface(GuidingSpectrumPeak, "GuidingSpectrumPeak"),
        generate_interface(GuidingSpectrumAnalysis, "GuidingSpectrumAnalysis"),
        generate_interface(ProcessStatus, "ProcessStatus"),
        generate_interface(ProcessingJob, "ProcessingJob"),
        generate_interface(AnalysisResult, "AnalysisResult"),
        generate_interface(VariableCandidate, "VariableCandidate"),
        generate_interface(FitsHeaderEntry, "FitsHeaderEntry"),
        generate_interface(IndiStatus, "IndiStatus"),
        generate_interface(SystemHealth, "SystemHealth"),
        generate_interface(IntrospectionMethod, "IntrospectionMethod"),
        generate_interface(IntrospectionEndpoint, "IntrospectionEndpoint"),
        generate_interface(GuidingStats, "GuidingStats"),
        generate_interface(LiveGuidingStatus, "LiveGuidingStatus"),
        generate_interface(StretchParameters, "StretchParameters"),
        generate_interface(RenderedImage, "RenderedImage"),
        generate_interface(SequenceItem, "SequenceItem"),
        generate_interface(SequencePlan, "SequencePlan"),
        generate_interface(CalibrationEntry, "CalibrationEntry"),
        generate_interface(CalibrationStats, "CalibrationStats"),
        generate_interface(MosaicPanel, "MosaicPanel"),
        generate_interface(TimeSpan, "TimeSpan"),
        generate_interface(MeridianStatus, "MeridianStatus"),
        generate_interface(MeridianCrossing, "MeridianCrossing"),
        generate_interface(SeparationRange, "SeparationRange"),
        generate_interface(VisibilitySample, "VisibilitySample"),
        generate_interface(VisibilitySpan, "VisibilitySpan"),
        generate_interface(ObjectVisibility, "ObjectVisibility"),
        generate_interface(SkySource, "SkySource"),
        generate_enum(GateStatus, "GateStatus"),
        generate_interface(GateResult, "GateResult"),
        generate_interface(ExcludedFrame, "ExcludedFrame"),
        generate_interface(TargetSessionContribution, "TargetSessionContribution"),
        generate_interface(ExposureGroupSummary, "ExposureGroupSummary"),
        generate_interface(AppliedCameraProfile, "AppliedCameraProfile"),
        generate_interface(StackingInputQuality, "StackingInputQuality"),
        generate_interface(StackingOutputQuality, "StackingOutputQuality"),
        generate_interface(StackingPipelineQualityMetrics, "StackingPipelineQualityMetrics"),
        generate_interface(StackQualitySummary, "StackQualitySummary"),
        generate_interface(AstrometryPipelineQualityMetrics, "AstrometryPipelineQualityMetrics"),
        generate_interface(AstrometryQualitySummary, "AstrometryQualitySummary"),
        generate_interface(FrameEnsembleComposition, "FrameEnsembleComposition"),
        generate_interface(NoiseModelPoint, "NoiseModelPoint"),
        generate_interface(PhotometryPipelineQualityMetrics, "PhotometryPipelineQualityMetrics"),
        generate_interface(PhotometryQualitySummary, "PhotometryQualitySummary"),
        generate_interface(SpectralClassificationConcern, "SpectralClassificationConcern"),
        generate_interface(SpectroscopyPipelineQualityMetrics, "SpectroscopyPipelineQualityMetrics"),
        generate_interface(SpectroscopyQualitySummary, "SpectroscopyQualitySummary"),
        generate_interface(FrameDetection, "FrameDetection"),
        generate_interface(MovingObjectTrack, "MovingObjectTrack"),
        generate_enum(CascadeStage, "CascadeStage"),
        generate_interface(EphemerisMatch, "EphemerisMatch"),
        generate_interface(AsteroidDetectionCandidate, "AsteroidDetectionCandidate"),
        generate_interface(
            AsteroidDetectionPipelineQualityMetrics, "AsteroidDetectionPipelineQualityMetrics"
        ),
        generate_interface(AsteroidDetectionQualitySummary, "AsteroidDetectionQualitySummary"),
        generate_interface(WeatherSample, "WeatherSample"),
        generate_public_interface(),
    ]

    content = header + "\n" + "\n\n".join(interfaces) + "\n"

    # Strip trailing whitespace from every line. Blank JSDoc continuation lines
    # are emitted as " * ", which the trailing-whitespace pre-commit hook then
    # strips -- and this generator would re-add on the next run, so the two
    # hooks never converge and `pre-commit run` fails forever. Emitting the
    # already-stripped form makes the generated file a fixed point of both.
    return "\n".join(line.rstrip() for line in content.split("\n"))


def main() -> None:
    """Run the entry point for generating backend TypeScript interfaces."""
    with open("ui/common/types/backendTypes.ts", "w") as f:
        f.write(render_types())
    print("Successfully generated ui/common/types/backendTypes.ts")


if __name__ == "__main__":
    main()
