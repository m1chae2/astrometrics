"""Purpose: Unit tests for calibration inventory domain models.

Description: Verifies CalibrationStats groups entries by frame type list
and CalibrationAdvisory constructs with a zero default existing_count.
"""

from wayfindinglib.models.equipment_and_site.calibration import (
    CalibrationAdvisory,
    CalibrationEntry,
    CalibrationStats,
)
from wayfindinglib.models.planning.observation_package import FrameType


def test_calibration_stats_groups_by_frame_type_list():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify darks/biases/flats are stored as separate lists."""
    stats = CalibrationStats(
        camera_id="c1",
        darks=[CalibrationEntry(camera_id="c1", frame_type=FrameType.DARK, exposure_sec=300.0, count=40)],
        biases=[CalibrationEntry(camera_id="c1", frame_type=FrameType.BIAS, count=100)],
    )
    assert len(stats.darks) == 1
    assert len(stats.biases) == 1
    assert stats.flats == []


def test_calibration_advisory_defaults_to_zero_count():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify CalibrationAdvisory defaults existing_count to zero."""
    advisory = CalibrationAdvisory(camera_id="c1", frame_type=FrameType.DARK, exposure_sec=300.0)
    assert advisory.existing_count == 0
    assert advisory.generated_at is not None
