"""Purpose: The records that quality checks and stacks return.

Description: The quality reports, the quality row shapes and
`CaptureSetting`. A script imports these names from here. The night rule,
`observing_night_id`, is in `astrometricslib.arguments`. Each name is imported
from the module where its code lives today.
"""

from astrometricslib.models.quality.capture_setting import CaptureSetting
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
    "CaptureSetting",
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
