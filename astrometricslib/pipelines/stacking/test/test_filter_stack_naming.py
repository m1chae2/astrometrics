"""Purpose: Unit test for filter-aware stacking output naming.

Description: Verifies that stacking sub-filters produces distinct output
filenames like M_13_L_Stacked.fits and M_13_SPEC_Stacked.fits, populating
target.stacked_image for standard filters and target.stacked_spectral_target
for SPEC filters.
"""

from unittest.mock import MagicMock, patch

from astrometricslib.models.quality_summary import (
    StackingPipelineQualityMetrics,
    StackQualitySummary,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stage as stacking_tasks
from astrometricslib.utilities.enums import FilterType


def test_filter_stack_naming_and_target_properties():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify filter-aware output filenames and Target property updates.

    Confirms that:
    - Standard filters (L, NONE) generate output names like
      <Target>_<Filter>_Stacked.fits and update target.stacked_image.
    - Spectral filters (SPEC) generate <Target>_SPEC_Stacked.fits and
      update target.stacked_spectral_target.
    """
    target = Target(
        id="M 13",
        frames=[
            FrameRecord(path="/fake/l_1.fits", filter=FilterType.L, camera="ZWO ASI 533MM Pro"),
            FrameRecord(path="/fake/l_2.fits", filter=FilterType.L, camera="ZWO ASI 533MM Pro"),
            FrameRecord(path="/fake/spec_1.fits", filter=FilterType.SPEC, camera="ZWO ASI 533MM Pro"),
        ],
    )

    l_frames = [f for f in target.frames if f.filter == FilterType.L]
    spec_frames = [f for f in target.frames if f.filter == FilterType.SPEC]

    dummy_summary = StackQualitySummary(
        pipeline_name="stacking",
        pipeline_version="1.0.0",
        target_id="M 13",
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False,
            frames_submitted=2,
            frames_stacked=2,
        ),
    )

    siril_path = "astrometricslib.drivers.siril_interface.ImageProcessing"
    summary_path = "astrometricslib.pipelines.stacking.stage._build_stack_quality_summary"

    with (
        patch(siril_path) as mock_driver_cls,
        patch(summary_path) as mock_summary,
    ):
        mock_driver = MagicMock()
        mock_driver.last_run_diagnostics = {"corrupt_frames_skipped": []}
        mock_driver_cls.return_value = mock_driver
        mock_summary.return_value = dummy_summary

        # 1. Stack L frames
        mock_driver.process_target.return_value = "/library/lights/M_13/M_13_L_Stacked.fits"
        stacked_l = stacking_tasks.stack_frames(target, frames_to_stack=l_frames, filter_type=FilterType.L)

        assert stacked_l == "/library/lights/M_13/M_13_L_Stacked.fits"
        assert target.stacked_image == "/library/lights/M_13/M_13_L_Stacked.fits"
        assert mock_driver.process_target.call_args.kwargs["output_file"] == "M_13_L_Stacked.fits"

        # 2. Stack SPEC frames
        mock_driver.process_target.return_value = "/library/lights/M_13/M_13_SPEC_Stacked.fits"
        stacked_spec = stacking_tasks.stack_frames(
            target, frames_to_stack=spec_frames, filter_type=FilterType.SPEC
        )

        assert stacked_spec == "/library/lights/M_13/M_13_SPEC_Stacked.fits"
        assert target.stacked_spectral_target == "/library/lights/M_13/M_13_SPEC_Stacked.fits"
        assert mock_driver.process_target.call_args.kwargs["output_file"] == "M_13_SPEC_Stacked.fits"
