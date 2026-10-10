"""Tests for the Target and Frame information structures.

These tests make sure that when we save and load target information
(like from a file), all the details about its images are kept
correctly, and that a target/frame stored under the old, flat field
layout (before the tiered reorganization) still loads correctly.
"""

import pytest

from astrometricslib.models.target import FrameRecord, Target


def test_frame_record_measurements_default_to_none_and_round_trip() -> None:
    """Check that image quality details start empty and can round-trip."""
    frame = FrameRecord(path="frame.fits")
    assert frame.measurements.registration_fwhm_x_px is None
    assert frame.measurements.registration_dx_px is None
    assert frame.measurements.background_level is None
    assert frame.measurements.saturated_pixel_fraction is None

    frame.measurements.registration_fwhm_x_px = 2.6
    frame.measurements.registration_fwhm_y_px = 2.7
    frame.measurements.registration_roundness = 0.9
    frame.measurements.registration_rmse = 0.15
    frame.measurements.registration_star_count = 42
    frame.measurements.registration_dx_px = 1.2
    frame.measurements.registration_dy_px = -0.8
    frame.measurements.background_level = 480.0
    frame.measurements.saturated_pixel_fraction = 0.0

    target = Target(id="TestTarget", frames=[frame])
    reloaded = Target.model_validate(target.serialize())

    reloaded_frame = reloaded.frames[0]
    # Check that the loaded values exactly match what we put in
    # before saving.
    assert reloaded_frame.measurements.registration_fwhm_x_px == pytest.approx(2.6)
    assert reloaded_frame.measurements.registration_dx_px == pytest.approx(1.2)
    assert reloaded_frame.measurements.registration_dy_px == pytest.approx(-0.8)
    assert reloaded_frame.measurements.registration_star_count == 42
    assert reloaded_frame.measurements.background_level == pytest.approx(480.0)
    assert reloaded_frame.measurements.saturated_pixel_fraction == pytest.approx(0.0)


def test_a_frame_stored_with_old_flat_measurement_fields_still_loads() -> None:
    """Check the pre-`FrameMeasurements` flat shape migrates on load."""
    old_shaped = {
        "path": "frame.fits",
        "backgroundLevel": 480.0,
        "registrationFwhmXPx": 2.6,
        "registrationDxPx": 1.2,
    }
    frame = FrameRecord.model_validate(old_shaped)
    assert frame.measurements.background_level == pytest.approx(480.0)
    assert frame.measurements.registration_fwhm_x_px == pytest.approx(2.6)
    assert frame.measurements.registration_dx_px == pytest.approx(1.2)


def test_a_frame_with_no_old_measurement_fields_gets_an_empty_measurements() -> None:
    """Check a plain frame with no flat or nested measurements still works."""
    frame = FrameRecord.model_validate({"path": "frame.fits"})
    assert frame.measurements.background_level is None


def test_target_stacking_and_quality_default_to_empty_not_none() -> None:
    """Check every nested result group is always present, never `None`.

    Matches `StellarObject`'s own pattern -- `target.stacking.stacked_image`
    always works with no `None`-check, even for a brand-new target.
    """
    target = Target(id="Fresh")
    assert target.stacking.stacked_image == ""
    assert target.spectral_stacking.stacked_image == ""
    assert target.asteroid_detection.candidates == []
    assert target.quality.astrometry is None
    assert target.quality.photometry is None
    assert target.quality.spectroscopy is None


def test_target_nested_fields_round_trip_through_save_and_load() -> None:
    """Check the new tiered shape saves and reloads correctly."""
    target = Target(id="M13")
    target.stacking.stacked_image = "/library/M13/stack.fits"
    target.stacking.processed_image = "/library/M13/proc.png"
    target.spectral_stacking.stacked_image = "/library/M13/spec.fits"

    reloaded = Target.model_validate(target.serialize())
    assert reloaded.stacking.stacked_image == "/library/M13/stack.fits"
    assert reloaded.stacking.processed_image == "/library/M13/proc.png"
    assert reloaded.spectral_stacking.stacked_image == "/library/M13/spec.fits"


def test_a_target_stored_with_old_flat_stacking_fields_still_loads() -> None:
    """Check a target from before the tiered reorganization migrates.

    Old rows have `stackedImage`/`processedImage`/`stackedSpectralTarget`
    as top-level siblings of `id`, not nested under `stacking`. This is
    what makes the reorganization a non-breaking change for stored data:
    no database rewrite is required.
    """
    old_shaped = {
        "id": "M13",
        "stackedImage": "/library/M13/stack.fits",
        "processedImage": "/library/M13/proc.png",
        "stackedSpectralTarget": "/library/M13/spec.fits",
    }
    target = Target.model_validate(old_shaped)
    assert target.stacking.stacked_image == "/library/M13/stack.fits"
    assert target.stacking.processed_image == "/library/M13/proc.png"
    assert target.spectral_stacking.stacked_image == "/library/M13/spec.fits"


def test_a_target_stored_with_old_flat_quality_summary_fields_still_loads() -> None:
    """Check old `*QualitySummary` siblings migrate into `quality`."""
    old_shaped = {
        "id": "M13",
        "astrometryQualitySummary": {
            "pipelineName": "astrometry",
            "pipelineVersion": "1.0",
            "targetId": "M13",
            "astrometryMetrics": {
                "sourcesDetected": 1,
                "solveAttempted": True,
                "plateSolveSucceeded": True,
                "simbadMatchedCount": 0,
            },
        },
    }
    target = Target.model_validate(old_shaped)
    assert target.quality.astrometry is not None
    assert target.quality.astrometry.pipeline_name == "astrometry"


def test_a_target_stored_with_old_flat_asteroid_fields_still_loads() -> None:
    """Check old `asteroidCandidates`/`asteroidDetectionQualitySummary`."""
    old_shaped = {"id": "M13", "asteroidCandidates": []}
    target = Target.model_validate(old_shaped)
    assert target.asteroid_detection.candidates == []


def test_a_target_already_in_the_new_shape_is_left_alone() -> None:
    """Check a target that already has a `stacking` key is not re-migrated."""
    new_shaped = {"id": "M13", "stacking": {"stackedImage": "/library/M13/stack.fits"}}
    target = Target.model_validate(new_shaped)
    assert target.stacking.stacked_image == "/library/M13/stack.fits"
