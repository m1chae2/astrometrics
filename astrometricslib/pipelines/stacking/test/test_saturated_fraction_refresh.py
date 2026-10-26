"""Tests that the stacking stage re-measures a stale saturated fraction.

A frame's saturated pixel fraction is stored with the level it was counted
at. These tests check that the stage keeps a fraction whose level matches
the camera profile and measures again when the level differs or is missing.
"""

from unittest.mock import patch

import pytest

from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.target import FrameMeasurements, FrameRecord
from astrometricslib.pipelines.stacking.stage import refresh_saturated_pixel_fraction

MEASURE = (
    "astrometricslib.pipelines.shared.quality.background_measurement.measure_frame_saturated_pixel_fraction"
)
RESOLVE = "astrometricslib.pipelines.stacking.stage.resolve_camera_profile"
PROVENANCE = {"kind": "assumed", "source": "a test"}


def _nikon_like_profile() -> CameraProfile:
    """Build a profile whose threshold is above its clip ceiling.

    Returns
    -------
    profile : `CameraProfile`
        A profile with a pixel-saturation level of 16,383 ADU.
    """
    return CameraProfile.model_validate({
        "camera_name": "Test Nikon",
        "clip_ceiling_adu": {"value": 16383.0, "provenance": {"kind": "measured", "source": "a test"}},
        "saturation_threshold_adu": {"value": 65000.0, "provenance": PROVENANCE},
    })


def _frame(fraction: float | None, limit: float | None) -> FrameRecord:
    """Build a frame record holding a stored fraction and its level.

    Parameters
    ----------
    fraction : `float` or `None`
        The stored saturated pixel fraction.
    limit : `float` or `None`
        The level it was counted at, in ADU.

    Returns
    -------
    frame : `FrameRecord`
        The frame.
    """
    return FrameRecord(
        path="/fake/frame.fits",
        camera="Test Nikon",
        measurements=FrameMeasurements(
            saturated_pixel_fraction=fraction, saturated_pixel_fraction_limit_adu=limit
        ),
    )


@pytest.mark.parametrize(("stored_limit"), [65000.0, None])
def test_a_fraction_counted_at_another_level_or_none_is_measured_again(stored_limit: float | None) -> None:
    """A different or missing stored level triggers a new measurement."""
    frame = _frame(0.0, stored_limit)
    with patch(RESOLVE, return_value=_nikon_like_profile()), patch(MEASURE, return_value=0.25) as measure:
        assert refresh_saturated_pixel_fraction(frame) is True
    measure.assert_called_once_with("/fake/frame.fits", 16383.0)
    assert frame.measurements.saturated_pixel_fraction == pytest.approx(0.25)
    assert frame.measurements.saturated_pixel_fraction_limit_adu == pytest.approx(16383.0)


def test_a_fraction_counted_at_the_current_level_is_kept() -> None:
    """A stored fraction whose level matches the profile is kept."""
    frame = _frame(0.1, 16383.0)
    with patch(RESOLVE, return_value=_nikon_like_profile()), patch(MEASURE) as measure:
        assert refresh_saturated_pixel_fraction(frame) is False
    measure.assert_not_called()
    assert frame.measurements.saturated_pixel_fraction == pytest.approx(0.1)


def test_a_missing_fraction_is_measured() -> None:
    """A frame with no fraction is measured and its level recorded."""
    frame = _frame(None, None)
    with patch(RESOLVE, return_value=_nikon_like_profile()), patch(MEASURE, return_value=0.0):
        assert refresh_saturated_pixel_fraction(frame) is True
    assert frame.measurements.saturated_pixel_fraction_limit_adu == pytest.approx(16383.0)


def test_an_old_record_without_the_level_field_still_loads() -> None:
    """A record from before the level was kept loads with it empty."""
    measurements = FrameMeasurements.model_validate({"saturatedPixelFraction": 0.05})
    assert measurements.saturated_pixel_fraction == pytest.approx(0.05)
    assert measurements.saturated_pixel_fraction_limit_adu is None
    stored = FrameMeasurements(saturated_pixel_fraction_limit_adu=16383.0).model_dump(by_alias=True)
    assert stored["saturatedPixelFractionLimitAdu"] == pytest.approx(16383.0)
