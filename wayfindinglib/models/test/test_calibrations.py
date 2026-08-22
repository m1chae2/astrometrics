"""Purpose: Unit tests for guider calibration and focus model domain models.

Description: Verifies GuiderCalibration round-trips, FocusModel's
duplicate-filter-offset rejection, and offset_for_filter's fallback to
zero for an unconfigured filter.
"""

import pytest
from pydantic import ValidationError

from wayfindinglib.models.equipment_and_site.focus_model import (
    ApproachDirection,
    FilterFocusOffset,
    FocusModel,
)
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration


def test_guider_calibration_round_trips():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a GuiderCalibration constructs and reports its fields back."""
    calibration = GuiderCalibration(
        id="gc1",
        camera_id="c1",
        telescope_id="t1",
        arcsec_per_pixel=1.2,
        camera_angle_deg=15.0,
        ra_rate_arcsec_per_sec=10.0,
        dec_rate_arcsec_per_sec=10.0,
    )
    assert calibration.arcsec_per_pixel == pytest.approx(1.2)
    assert calibration.calibrated_at is not None


def test_focus_model_rejects_duplicate_filter_offsets():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify two offsets for the same filter are rejected."""
    with pytest.raises(ValidationError):
        FocusModel(
            id="fm1",
            camera_id="c1",
            telescope_id="t1",
            backlash_steps=50,
            approach_direction=ApproachDirection.INWARD,
            filter_offsets=[
                FilterFocusOffset(filter="Ha", offset_steps=100),
                FilterFocusOffset(filter="Ha", offset_steps=200),
            ],
        )


def test_focus_model_offset_for_filter_returns_zero_when_unconfigured():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify offset_for_filter() falls back to zero for an unlisted filter."""
    model = FocusModel(
        id="fm1",
        camera_id="c1",
        telescope_id="t1",
        backlash_steps=50,
        approach_direction=ApproachDirection.OUTWARD,
        filter_offsets=[FilterFocusOffset(filter="Ha", offset_steps=120)],
    )
    assert model.offset_for_filter("Ha") == 120
    assert model.offset_for_filter("OIII") == 0
