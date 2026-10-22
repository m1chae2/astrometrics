"""The quality metrics that the stacking pipeline declares for the registry.

Each `MetricSpec` describes one measurement that existing code already makes.
The `computed_by` field points at the function that makes it.
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
)
