"""Tests for the Target and Frame information structures.

These tests make sure that when we save and load target information
(like from a file), all the details about its images are kept correctly.
"""

import pytest

from astrometricslib.models.target import FrameRecord, Target


def test_frame_record_per_frame_quality_facts_default_to_none_and_round_trip() -> None:
    """Check that image quality details start empty and can round-trip."""
    frame = FrameRecord(path="frame.fits")
    assert frame.registration_fwhm_x_px is None
    assert frame.registration_dx_px is None
    assert frame.background_level is None
    assert frame.saturated_pixel_fraction is None

    frame.registration_fwhm_x_px = 2.6
    frame.registration_fwhm_y_px = 2.7
    frame.registration_roundness = 0.9
    frame.registration_rmse = 0.15
    frame.registration_star_count = 42
    frame.registration_dx_px = 1.2
    frame.registration_dy_px = -0.8
    frame.background_level = 480.0
    frame.saturated_pixel_fraction = 0.0

    target = Target(id="TestTarget", frames=[frame])
    reloaded = Target()
    reloaded.deserialize(target.serialize())

    reloaded_frame = reloaded.frames[0]
    # Check that the loaded values exactly match what we put in
    # before saving.
    assert reloaded_frame.registration_fwhm_x_px == pytest.approx(2.6)
    assert reloaded_frame.registration_dx_px == pytest.approx(1.2)
    assert reloaded_frame.registration_dy_px == pytest.approx(-0.8)
    assert reloaded_frame.registration_star_count == 42
    assert reloaded_frame.background_level == pytest.approx(480.0)
    assert reloaded_frame.saturated_pixel_fraction == pytest.approx(0.0)
