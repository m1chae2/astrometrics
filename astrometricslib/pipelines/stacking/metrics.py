"""The quality metrics that the stacking pipeline declares for the registry.

Each `MetricSpec` describes one measurement that existing code already makes.
The `computed_by` field points at the function that makes it.

Two of them are frame metrics (`frame.quarantine_roundness` and
`frame.quarantine_sky_median_adu`). The frame quarantine step measures them
for every light frame it checks, so the stacking stage records them for kept
frames as well as for the frames it moves aside.
"""

from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)

STACKING_REJECTED_PIXEL_FRACTION = "stacking.rejected_pixel_fraction"
FRAME_QUARANTINE_ROUNDNESS = "frame.quarantine_roundness"
FRAME_QUARANTINE_SKY_MEDIAN = "frame.quarantine_sky_median_adu"

STACKING_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name=STACKING_REJECTED_PIXEL_FRACTION,
        definition_version=1,
        unit="fraction",
        checkpoint=Checkpoint.STACKING,
        levels=(SubjectLevel.STACK,),
        better=Direction.LOWER,
        meaning="The share of pixel values thrown out while combining the frames.",
        decisions=("rejected pixel fraction gate",),
        estimator=(
            "The mean over all pixels of Siril's rejection map (`_RejMap.fits`), where each pixel "
            "holds the fraction of the frames that were rejected at that position."
        ),
        sample_definition=(
            "Every pixel position of the stack; each holds the share of frames rejected there. "
            "The register entry STK-24 is Partly measured: the normal range is measured, "
            "the detection side is not."
        ),
        band="stack filter or channel",
        computed_by="drivers.siril_output_parsing.measure_rejected_fraction",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        register_entry="STK-24",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A high share means the frames disagree, often from poor alignment or cloud, "
            "and the stack gate flags it."
        ),
    ),
    MetricSpec(
        name=FRAME_QUARANTINE_ROUNDNESS,
        definition_version=1,
        unit="",
        checkpoint=Checkpoint.FRAME,
        levels=(SubjectLevel.FRAME,),
        better=Direction.HIGHER,
        meaning=(
            "How round the brightest stars of one raw frame are, as the frame quarantine step "
            "measures it (1 is round, smaller is more stretched). It is the step's own number, "
            "kept so a frame that was kept can be compared with the frames that were moved."
        ),
        decisions=("frame quarantine",),
        # `frame.star_elongation` is a different estimator (Moffat fits and
        # second moments of up to 196 stars). The two must never share a name.
        estimator=(
            "Median over the brightest detected stars (at most `MAXIMUM_STARS_MEASURED`) of the "
            "smaller flux-weighted second-moment width divided by the larger one, measured in a "
            "small box on the background-subtracted frame by `detect_stars`. The widths are taken "
            "along the image x and y axes, not along the star's own axes, so a trail at 45 degrees "
            "to the axes reads round (a roundness near 1); the metric catches trails along the axes "
            "and is blind to diagonal ones. `frame.star_elongation` has no such blind spot."
        ),
        sample_definition=(
            "The brightest stars with a measurable shape, away from the frame edge. A frame with "
            "no such star gets no value."
        ),
        band="The frame's own filter; a color image is read as stored, with its color pixels mixed.",
        computed_by="pipelines.shared.quality.raw_frame_check.measure_raw_frame",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        register_entry="STK-01",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame whose stars are much less round than the rest of its session has trailed, and "
            "the quarantine step moves it out of the stack."
        ),
    ),
    MetricSpec(
        name=FRAME_QUARANTINE_SKY_MEDIAN,
        definition_version=1,
        unit="adu",
        checkpoint=Checkpoint.FRAME,
        levels=(SubjectLevel.FRAME,),
        better=Direction.NONE,
        meaning=(
            "The median pixel value of one raw frame, as the frame quarantine step measures it. "
            "It includes the bias level and every star, so it is not the sky brightness."
        ),
        decisions=("frame quarantine",),
        # `frame.background_level_adu` is the sigma-clipped median, a different
        # estimator. The two must never share a name.
        estimator="The plain median of every pixel of the frame, `numpy.median`, with no clipping.",
        sample_definition="Every pixel of the frame as stored.",
        band="The frame's own filter; a color image is read as stored, with its color pixels mixed.",
        computed_by="pipelines.shared.quality.raw_frame_check.measure_raw_frame",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        register_entry="STK-01",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame much brighter than its session, together with few stars, is clouded, and the "
            "quarantine step moves it out of the stack."
        ),
    ),
)
