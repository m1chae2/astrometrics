"""The frame metrics the library already measures, for the registry.

Each `MetricSpec` here describes one per-frame measurement that existing code
computes today. The name says which estimator it is, so a number from another
estimator never shares a name with it. Nothing here measures anything: the
`computed_by` field points at the function that does.

The registry (`metric_registry.py`) collects these with the metrics that each
pipeline declares in its own ``metrics.py``.
"""

from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)

FRAME_BACKGROUND_LEVEL = "frame.background_level_adu"
FRAME_SATURATED_PIXEL_FRACTION = "frame.saturated_pixel_fraction"
FRAME_STAR_WIDTH_BRIGHTEST_PX = "frame.star_width_brightest_px"
FRAME_STAR_COUNT = "frame.star_count"
FRAME_SENSOR_TEMPERATURE = "frame.sensor_temperature_degc"
FRAME_FOCUSER_TEMPERATURE = "frame.focuser_temperature_degc"

_FRAME_ONLY = frozenset({SubjectLevel.FRAME})

FRAME_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name=FRAME_BACKGROUND_LEVEL,
        definition_version=1,
        unit="adu",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning="The brightness of the sky background in one frame.",
        decisions=("frame quarantine", "background homogeneity"),
        estimator="Sigma-clipped median of all pixels (3 sigma), after collapsing color planes to one.",
        sample_definition="Every pixel of the frame.",
        band="frame filter or channel",
        computed_by="pipelines.shared.quality.background_measurement.measure_frame_background_level",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "The stacking checks compare each frame's sky level with its session to find cloud and gradients."
        ),
    ),
    MetricSpec(
        name=FRAME_SATURATED_PIXEL_FRACTION,
        definition_version=1,
        unit="fraction",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning="The share of pixels at or above the camera's saturation level.",
        decisions=("raw frame clipping",),
        estimator=(
            "Count of pixels at or above the camera-profile saturation threshold, divided by the pixel count."
        ),
        sample_definition="Every pixel of the frame.",
        band="frame filter or channel",
        computed_by="pipelines.shared.quality.background_measurement.measure_frame_saturated_pixel_fraction",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame with many saturated pixels has lost the brightness of those pixels, "
            "and the checks warn about it."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_WIDTH_BRIGHTEST_PX,
        definition_version=1,
        unit="px",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning="The width (FWHM) of the brightest round stars in one frame.",
        decisions=("frame quarantine", "stack sharpness"),
        estimator="Median FWHM of fitted stars; a star is skipped if its elongation is above 1.5.",
        sample_definition="The 15 brightest stars that pass the elongation cut and the saturated-core mask.",
        band="frame filter or channel",
        computed_by="pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "The registration and stack sharpness checks compare star width between frames and stacks."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_COUNT,
        definition_version=1,
        unit="count",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.HIGHER,
        meaning="The number of bright connected regions (stars) found in one raw frame.",
        decisions=("raw frame check",),
        estimator="Count of connected bright regions found by the quick raw-frame star detector.",
        sample_definition="Every connected bright region in the frame.",
        band="frame filter or channel",
        computed_by="pipelines.shared.quality.raw_frame_check.measure_raw_frame",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame with far fewer stars than its batch usually has cloud or fog, and "
            "the raw frame check flags it."
        ),
    ),
    MetricSpec(
        name=FRAME_SENSOR_TEMPERATURE,
        definition_version=1,
        unit="degC",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning="The camera sensor temperature recorded when the frame was taken.",
        decisions=(),
        estimator="Read from the FITS header card CCD-TEMP; not computed from pixels.",
        sample_definition="One header value per frame.",
        band="not applicable",
        computed_by="pipelines.shared.frame_scanning.create_frame_record_from_fits",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="The camera's reading is taken as given.",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "Dark current depends on sensor temperature, so it helps explain noise "
            "differences between frames."
        ),
    ),
    MetricSpec(
        name=FRAME_FOCUSER_TEMPERATURE,
        definition_version=1,
        unit="degC",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning="The focuser temperature recorded when the frame was taken.",
        decisions=(),
        estimator="Read from the FITS header card FOCUSTEM; not computed from pixels.",
        sample_definition="One header value per frame.",
        band="not applicable",
        computed_by="pipelines.shared.frame_scanning.create_frame_record_from_fits",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="The focuser's reading is taken as given.",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "Focus drifts with temperature, so it helps explain a change in star width through a night."
        ),
    ),
)
