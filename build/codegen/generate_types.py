"""Auto-generate TypeScript interfaces and enums from backend Pydantic models.

Enforces synchronization between backend data models and frontend
TypeScript contracts.
"""

import os
import sys
from datetime import datetime
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel

# Ensure we can import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from astrometricslib import FilterType
from astrometricslib.models.moving_object import (
    AsteroidRecoveryCandidate,
    CascadeStage,
    EphemerisMatch,
    FrameDetection,
    MovingObjectTrack,
)
from astrometricslib.models.quality_summary import (
    AsteroidRecoveryPipelineQualityMetrics,
    AsteroidRecoveryQualitySummary,
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    ExcludedFrame,
    FrameEnsembleComposition,
    PhotometryPipelineQualityMetrics,
    PhotometryQualitySummary,
    SpectroscopyPipelineQualityMetrics,
    SpectroscopyQualitySummary,
    StackingPipelineQualityMetrics,
    StackQualitySummary,
    TargetSessionContribution,
)
from astrometricslib.models.stellar_source import (
    AnalysisResult,
    ExoplanetTransitCandidate,
    FileItem,
    GroupedFrameStat,
    LightCurve,
    PeriodogramResult,
    PlotData,
    SpectralObservation,
    StellarObject,
    StellarSessionMatch,
    TargetFilesResponse,
    VariableCandidate,
)
from astrometricslib.models.target import (
    FitsHeaderEntry,
    FrameRecord,
    ImageType,
    MosaicInfo,
    RenderedImage,
    StackConfigurationResult,
    Target,
)
from astrometricslib.utilities.pipeline_models import ProcessingJob, ProcessStatus
from backend.services.infrastructure.system_status_service import (
    IntrospectionEndpoint,
    IntrospectionMethod,
    ProcessingJobPulse,
    SystemHealth,
    SystemPulse,
    TelescopePulse,
)
from wayfindinglib.drivers.indi_interface import TelescopeStatus
from wayfindinglib.models.session.observation_session import WeatherSample
from wayfindinglib.models.session.telemetry import (
    AlignmentAttempt,
    GuidingSample,
    GuidingStats,
    GuidingStatus,
    IndiStatus,
)
from wayfindinglib.observation import (
    CalibrationEntry,
    CalibrationStats,
    MosaicPanel,
    SequenceItem,
    SequencePlan,
)
from wayfindinglib.observationlib.observation_session import ObservationSession


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

    # Add flexible index for known models
    if name in ("TelescopeStatus", "TargetObject", "Spectrum"):
        lines.append("  /** Flexible index to accommodate additional data from the backend. */")
        lines.append("  [key: string]: any;")

    lines.append("}")
    return "\n".join(lines)


def main() -> None:
    """Run the entry point for generating backend TypeScript interfaces."""
    header = """/**
 * @fileoverview Auto-generated TypeScript interfaces from Pydantic models.
 */
"""

    interfaces = [
        generate_enum(FilterType, "FilterType"),
        generate_enum(ImageType, "ImageType"),
        generate_interface(FrameRecord, "FrameRecord"),
        generate_interface(MosaicInfo, "MosaicInfo"),
        generate_interface(TelescopeStatus, "TelescopeStatus"),
        generate_interface(StackConfigurationResult, "StackConfigurationResult"),
        generate_interface(Target, "TargetObject"),
        generate_interface(FileItem, "FileItem"),
        generate_interface(TargetFilesResponse, "TargetFilesResponse"),
        generate_interface(GroupedFrameStat, "GroupedFrameStat"),
        generate_interface(PlotData, "PlotData"),
        generate_interface(StellarObject, "Spectrum"),
        generate_interface(StellarSessionMatch, "StellarSessionMatch"),
        generate_interface(SpectralObservation, "SpectralObservation"),
        generate_interface(PeriodogramResult, "PeriodogramResult"),
        generate_interface(ExoplanetTransitCandidate, "ExoplanetTransitCandidate"),
        generate_interface(LightCurve, "LightCurve"),
        generate_interface(TelescopePulse, "TelescopePulse"),
        generate_interface(ProcessingJobPulse, "ProcessingJobPulse"),
        generate_interface(SystemPulse, "SystemPulse"),
        generate_interface(GuidingSample, "GuidingSample"),
        generate_interface(AlignmentAttempt, "AlignmentAttempt"),
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
        generate_interface(GuidingStatus, "GuidingStatus"),
        generate_interface(RenderedImage, "RenderedImage"),
        generate_interface(SequenceItem, "SequenceItem"),
        generate_interface(SequencePlan, "SequencePlan"),
        generate_interface(CalibrationEntry, "CalibrationEntry"),
        generate_interface(CalibrationStats, "CalibrationStats"),
        generate_interface(MosaicPanel, "MosaicPanel"),
        generate_interface(ExcludedFrame, "ExcludedFrame"),
        generate_interface(TargetSessionContribution, "TargetSessionContribution"),
        generate_interface(StackingPipelineQualityMetrics, "StackingPipelineQualityMetrics"),
        generate_interface(StackQualitySummary, "StackQualitySummary"),
        generate_interface(AstrometryPipelineQualityMetrics, "AstrometryPipelineQualityMetrics"),
        generate_interface(AstrometryQualitySummary, "AstrometryQualitySummary"),
        generate_interface(FrameEnsembleComposition, "FrameEnsembleComposition"),
        generate_interface(PhotometryPipelineQualityMetrics, "PhotometryPipelineQualityMetrics"),
        generate_interface(PhotometryQualitySummary, "PhotometryQualitySummary"),
        generate_interface(SpectroscopyPipelineQualityMetrics, "SpectroscopyPipelineQualityMetrics"),
        generate_interface(SpectroscopyQualitySummary, "SpectroscopyQualitySummary"),
        generate_interface(FrameDetection, "FrameDetection"),
        generate_interface(MovingObjectTrack, "MovingObjectTrack"),
        generate_enum(CascadeStage, "CascadeStage"),
        generate_interface(EphemerisMatch, "EphemerisMatch"),
        generate_interface(AsteroidRecoveryCandidate, "AsteroidRecoveryCandidate"),
        generate_interface(AsteroidRecoveryPipelineQualityMetrics, "AsteroidRecoveryPipelineQualityMetrics"),
        generate_interface(AsteroidRecoveryQualitySummary, "AsteroidRecoveryQualitySummary"),
        generate_interface(WeatherSample, "WeatherSample"),
        generate_interface(ObservationSession, "ObservationSession"),
    ]

    content = header + "\n" + "\n\n".join(interfaces) + "\n"

    # Strip trailing whitespace from every line. Blank JSDoc continuation lines
    # are emitted as " * ", which the trailing-whitespace pre-commit hook then
    # strips -- and this generator would re-add on the next run, so the two
    # hooks never converge and `pre-commit run` fails forever. Emitting the
    # already-stripped form makes the generated file a fixed point of both.
    content = "\n".join(line.rstrip() for line in content.split("\n"))

    with open("ui/common/types/backendTypes.ts", "w") as f:
        f.write(content)
    print("Successfully generated ui/common/types/backendTypes.ts")


if __name__ == "__main__":
    main()
