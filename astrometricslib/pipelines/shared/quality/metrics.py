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
FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX = "frame.star_width_gaussian_brightest_px"
FRAME_DETECTED_REGION_COUNT = "frame.detected_region_count"
FRAME_SENSOR_TEMPERATURE = "frame.sensor_temperature_degc"
FRAME_FOCUSER_TEMPERATURE = "frame.focuser_temperature_degc"

_FRAME_ONLY = (SubjectLevel.FRAME,)

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
        # `raw_frame_check` measures its own plain-median sky level
        # (`sky_median_adu`). That is a different estimator and must never
        # be emitted under this name.
        estimator=(
            "Median of the pixels left after astropy's `sigma_clipped_stats` (3 sigma, "
            "the default maximum of 5 clipping passes), on the frame with its color planes "
            "collapsed to one."
        ),
        sample_definition="Every pixel of the frame.",
        band=(
            "The frame's own filter. A color image is first collapsed to the mean of its color planes; "
            "an undebayered one-shot-color mosaic is measured as it is, with its color pixels mixed."
        ),
        computed_by="pipelines.shared.quality.background_measurement.measure_frame_background_level",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "The stacking checks compare each frame's sky level with its session to find cloud and gradients."
        ),
        register_entry="STK-04",
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
        sample_definition="Every pixel of the frame, after a color image is collapsed to one plane.",
        band=(
            "The frame's own filter. A color image is first collapsed to the mean of its color planes; "
            "an undebayered one-shot-color mosaic is measured as it is, with its color pixels mixed."
        ),
        computed_by="pipelines.shared.quality.background_measurement.measure_frame_saturated_pixel_fraction",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame with many saturated pixels has lost the brightness of those pixels, "
            "and the checks warn about it. No register entry yet backs the limit it is compared with."
        ),
    ),
    MetricSpec(
        name=FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
        definition_version=1,
        unit="px",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.LOWER,
        meaning=(
            "The width (FWHM) of the brightest stars in one frame, from 2-D Gaussian fits. "
            "On a trailed frame the narrow-axis width is used, so the value can read sharp."
        ),
        decisions=("stack sharpness",),
        estimator=(
            "Median of the fitted star widths from `measure_image_fwhm`. Each star gets a tilted 2-D "
            "Gaussian fit that must reach R squared 0.8 and land within 2.5 px of the detected center. "
            "The width is 2.355 times the geometric mean of the two fitted sigmas; when the long axis "
            "is more than 1.5 times the short axis, it is 2.355 times the short-axis sigma instead, "
            "and that star stays in the median. A star whose box reaches 0.95 of the frame's "
            "plateau level (only when 3 or more pixels sit at the frame maximum) is skipped."
        ),
        sample_definition=(
            "Up to 400 detected sources with flux at least 5% of the brightest are tried, brightest "
            "first, until 15 fits succeed. The frame is collapsed to one plane first. The optional "
            "saturated-core mask is not passed by the callers."
        ),
        band=(
            "The frame's own filter. A color image is first collapsed to the mean of its color planes; "
            "an undebayered one-shot-color mosaic is measured as it is."
        ),
        computed_by="pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=("The stack sharpness check compares star width between frames and stacks."),
        register_entry="AST-07",
    ),
    MetricSpec(
        name=FRAME_DETECTED_REGION_COUNT,
        definition_version=1,
        unit="count",
        checkpoint=Checkpoint.FRAME,
        levels=_FRAME_ONLY,
        better=Direction.NONE,
        meaning=(
            "The number of connected bright regions in one raw frame. Hot pixels, nebulosity and "
            "pieces of a trail count too, and merged stars count once, so it is not a star count."
        ),
        decisions=("frame quarantine", "raw frame check"),
        estimator=(
            "`ndimage.label` count of regions above 40 noise units (`DETECTION_SIGMA`) after "
            "background removal and a 1.5 px Gaussian smooth, from `_detect_stars`."
        ),
        sample_definition="Every connected bright region in the frame.",
        band="The frame's own filter; a color image is first collapsed to one plane.",
        computed_by="pipelines.shared.quality.raw_frame_check.measure_raw_frame",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame with far fewer regions than its batch usually has cloud or fog, and "
            "the quarantine and the raw frame check flag it."
        ),
        register_entry="STK-01",
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
