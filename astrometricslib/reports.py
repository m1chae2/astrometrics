"""Purpose: The records that quality checks and stacks return.

Description: The quality reports, the quality row shapes, `CaptureSetting` and
`observing_night_id`, the join key between a frame and a night. A script
imports these names from here. Each is imported from the module where its code
lives today.
"""

from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.excluded_frames import (
    QuarantinePreview,
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
from astrometricslib.utilities.observing_night import observing_night_id

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
    "QuarantinePreview",
    "RawFrameCheckReport",
    "SetAsideFrame",
    "SetAsideFrameRecord",
    "SpectralFrameCheckReport",
    "StackQualityReport",
    "StackSummary",
    "StarResultReference",
    "SubjectLevel",
    "observing_night_id",
]
