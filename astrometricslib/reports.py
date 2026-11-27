"""Purpose: The records that quality checks and stacks return.

Description: The quality reports and the quality row shapes. A script
imports these names from here; the capture setting, an input as well as a
result, is `astrometricslib.CaptureSetting`. The night rule,
`observing_night_id`, is in `astrometricslib.arguments`. Each name is imported
from the module where its code lives today.
"""

from astrometricslib.models.quality.excluded_frames import (
    SetAsideFrame,
    SetAsideFrameRecord,
)
from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import (
    MeasurementReference,
    MeasurementStatus,
    QualityMeasurement,
    StarResultReference,
    SubjectLevel,
)
from astrometricslib.models.quality.quality_reports import (
    InputQualityReport,
    RawFrameCheckReport,
    SpectralFrameCheckReport,
    StackQualityReport,
    StackSummary,
)
from astrometricslib.models.quality.quality_summary import (
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    ExposureGroupSummary,
)
from astrometricslib.models.quality.subset_stacks import EARLIER_NIGHTS_VARIANT

__all__ = [
    "EARLIER_NIGHTS_VARIANT",
    "AstrometryPipelineQualityMetrics",
    "AstrometryQualitySummary",
    "ExposureGroupSummary",
    "InputQualityReport",
    "LimitTier",
    "MeasurementReference",
    "MeasurementStatus",
    "QualityLimit",
    "QualityMeasurement",
    "RawFrameCheckReport",
    "SetAsideFrame",
    "SetAsideFrameRecord",
    "SpectralFrameCheckReport",
    "StackQualityReport",
    "StackSummary",
    "StarResultReference",
    "SubjectLevel",
]
