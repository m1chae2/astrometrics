"""Purpose: Test that rows carry the science limits they were judged with.

Description: A caller that quotes a limit (a spectral match limit, the dark
temperature tolerance or a saturation limit) reads it from the row it holds,
not from a copy of the number. These tests check that each value equals the
library constant it replaces, and that the stored form of a spectroscopy
result does not change.
"""

from astrometricslib.models.stellar_source import AMBIGUOUS_RMS_GAP, NO_GOOD_MATCH_RMS, SpectroscopyResult
from astrometricslib.pipelines.shared.quality.metrics import FRAME_METRICS, FRAME_SATURATED_PIXEL_FRACTION
from astrometricslib.pipelines.stacking.post_processing.exposure_saturation import (
    SATURATED_BLOB_MINIMUM_PIXELS,
    SATURATED_FRAME_FRACTION,
)


def test_spectroscopy_result_carries_its_match_limits_without_saving_them() -> None:
    """The two limits equal the constants and are not in the saved form."""
    result = SpectroscopyResult()
    assert result.poor_match_rms_limit == NO_GOOD_MATCH_RMS
    assert result.ambiguous_rms_gap_limit == AMBIGUOUS_RMS_GAP
    saved = result.model_dump(by_alias=True)
    assert "poorMatchRmsLimit" not in saved
    assert "ambiguousRmsGapLimit" not in saved
    assert "poor_match_rms_limit" not in result.model_dump()


def test_the_saturated_pixel_metric_lists_the_saturation_limits() -> None:
    """The metric lists the blob size and clipped-frame share it uses."""
    spec = next(spec for spec in FRAME_METRICS if spec.name == FRAME_SATURATED_PIXEL_FRACTION)
    values = {limit.unit: limit.value for limit in spec.limits}
    assert values == {
        "pixels": float(SATURATED_BLOB_MINIMUM_PIXELS),
        "fraction": SATURATED_FRAME_FRACTION,
    }
    assert all(limit.derivation for limit in spec.limits)


def test_results_give_the_provenance_ids_their_summaries_carry() -> None:
    """A stack or analysis result reads its run ids from its summaries."""
    from astrometricslib.models.processing_results import ProcessTargetResult, StackResult
    from astrometricslib.models.quality.quality_summary import AstrometryQualitySummary, StackQualitySummary
    from astrometricslib.models.target import TargetQualitySummaries

    stack = StackResult(
        target_id="M13",
        kind="imaging",
        frames_selected=3,
        first_file="a",
        last_file="c",
        quality_summary=StackQualitySummary.model_construct(provenance_activity_id="job-1"),
    )
    assert stack.provenance_activity_id == "job-1"
    assert (
        StackResult(
            target_id="M13", kind="imaging", frames_selected=3, first_file="a", last_file="c"
        ).provenance_activity_id
        is None
    )
    analysis = ProcessTargetResult(
        target_id="M13",
        quality=TargetQualitySummaries(
            astrometry=AstrometryQualitySummary.model_construct(provenance_activity_id="job-2")
        ),
    )
    assert analysis.provenance_activity_ids == {"astrometry": "job-2"}
    assert ProcessTargetResult(target_id="M13").provenance_activity_ids == {}
