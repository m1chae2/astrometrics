"""Purpose: Unit tests for the group and calibration fields of a summary.

Description: A stack's quality summary now carries one entry per exposure
length, the exposure that would keep the brightest star below the ceiling, and
signs that calibration left the stack blank (mostly exact zeros, or Siril's
"many negative pixels" warning). These tests check the fields are filled from
a run's diagnostics, that a blank imaging stack is flagged while a spectral
stack with a legitimately dark sky is not, and that a group left out of the
combined image is named in the flags.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.quality_summary import (
    StackingPipelineQualityMetrics,
    StackQualitySummary,
)
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import assess_input_quality
from astrometricslib.pipelines.stacking.stage import (
    _base_stack_quality_summary,
    _finalize_stack_quality_flags,
    _measure_calibration_health,
    _record_exposure_groups,
)


def make_summary(is_spectral: bool = False) -> StackQualitySummary:
    """Build an empty stack summary.

    Returns
    -------
    summary : `StackQualitySummary`
        A summary with default metrics and a clean input judgement.
    """
    return StackQualitySummary(
        target_id="NGC 7023",
        input_quality=assess_input_quality(10, 10, [], None, {}),
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=is_spectral, frames_submitted=10, frames_stacked=10
        ),
    )


def write_stack(path: Path, zero_fraction: float) -> str:
    """Write a stack whose pixels are the given fraction zero.

    Returns
    -------
    path : `str`
        The file written.
    """
    generator = np.random.default_rng(0)
    data = generator.uniform(0.01, 0.5, (100, 100)).astype(np.float32)
    data[generator.random((100, 100)) < zero_fraction] = 0.0
    fits.writeto(path, data, overwrite=True)
    return str(path)


def test_group_entries_and_the_recommendation_come_from_the_diagnostics() -> None:
    """Each entry becomes a group summary; the recommendation is copied."""
    summary = make_summary()
    diagnostics = {
        "exposure_group_summaries": [
            {
                "exposure_seconds": 60.0,
                "frames_submitted": 14,
                "frames_stacked": 14,
                "dark_applied": False,
                "saturated": False,
            },
            {
                "exposure_seconds": 300.0,
                "frames_submitted": 20,
                "frames_stacked": 20,
                "dark_applied": True,
                "saturated": True,
                "stack_path": "/library/lights/NGC/groups/x_exp300s.fits",
            },
        ],
        "recommended_exposure_seconds": 42.5,
    }

    _record_exposure_groups(summary, diagnostics)

    groups = summary.stacking_metrics.exposure_groups
    assert [group.exposure_seconds for group in groups] == [60.0, 300.0]
    assert [group.dark_applied for group in groups] == [False, True]
    assert [group.saturated for group in groups] == [False, True]
    assert summary.stacking_metrics.recommended_exposure_seconds == pytest.approx(42.5)


def test_a_stack_without_group_entries_has_none() -> None:
    """A run that reported no groups has an empty list and no advice."""
    summary = make_summary()

    _record_exposure_groups(summary, {})

    assert summary.stacking_metrics.exposure_groups == []
    assert summary.stacking_metrics.recommended_exposure_seconds is None


def test_a_mostly_zero_imaging_stack_is_flagged_as_blank(tmp_path: Path) -> None:
    """A stack that is 99% zero pixels gets the blank-stack flag."""
    summary = make_summary()

    _measure_calibration_health(summary, write_stack(tmp_path / "blank.fits", 0.99), False, {})
    _finalize_stack_quality_flags(summary)

    assert summary.stacking_metrics.zero_pixel_fraction > 0.95
    assert summary.stacking_metrics.zero_fraction_flagged
    assert summary.flagged
    assert any("exactly zero" in reason for reason in summary.flag_reasons)


def test_a_normal_imaging_stack_is_not_flagged(tmp_path: Path) -> None:
    """A stack with almost no zero pixels is left alone."""
    summary = make_summary()

    _measure_calibration_health(summary, write_stack(tmp_path / "fine.fits", 0.01), False, {})

    assert not summary.stacking_metrics.zero_fraction_flagged


def test_a_spectral_stack_with_a_dark_sky_is_not_flagged_for_zeros(tmp_path: Path) -> None:
    """A spectral stack's sky is legitimately zero; only imaging is flagged."""
    summary = make_summary(is_spectral=True)

    _measure_calibration_health(summary, write_stack(tmp_path / "spec.fits", 0.93), True, {})

    assert summary.stacking_metrics.zero_pixel_fraction > 0.9
    assert not summary.stacking_metrics.zero_fraction_flagged


def test_the_negative_pixel_warning_from_siril_is_recorded_and_flagged(tmp_path: Path) -> None:
    """Siril's 99% warning surfaces in the summary and its flags."""
    summary = make_summary()
    diagnostics = {"negative_pixel_max_percent": 99, "negative_pixel_frames": 40}

    _measure_calibration_health(summary, write_stack(tmp_path / "s.fits", 0.0), False, diagnostics)
    _finalize_stack_quality_flags(summary)

    assert summary.stacking_metrics.negative_pixel_max_percent == 99
    assert summary.stacking_metrics.negative_pixels_flagged
    assert any("negative pixels" in reason for reason in summary.flag_reasons)


def test_a_small_negative_share_is_recorded_but_not_flagged(tmp_path: Path) -> None:
    """Below the limit the percentage is kept without raising a flag."""
    summary = make_summary()

    _measure_calibration_health(
        summary, write_stack(tmp_path / "s.fits", 0.0), False, {"negative_pixel_max_percent": 10}
    )

    assert summary.stacking_metrics.negative_pixel_max_percent == 10
    assert not summary.stacking_metrics.negative_pixels_flagged


def test_a_group_left_out_of_the_combined_image_is_named_in_the_flags() -> None:
    """Why a group is missing from the combined stack appears as a flag."""
    summary = make_summary()
    _record_exposure_groups(
        summary,
        {
            "exposure_group_summaries": [
                {
                    "exposure_seconds": 0.5,
                    "frames_submitted": 60,
                    "frames_stacked": 0,
                    "dark_applied": False,
                    "left_out_reason": "could not be stacked",
                }
            ]
        },
    )

    _finalize_stack_quality_flags(summary)

    assert summary.flagged
    assert "the 0.5 s exposure group could not be stacked" in summary.flag_reasons


def test_the_flat_measurements_come_from_the_diagnostics_and_are_flagged() -> None:
    """Flat issues become flag reasons; the measurements are copied."""
    summary = make_summary()
    summary.input_quality = assess_input_quality(
        10,
        10,
        [],
        None,
        {
            "flat_calibration": {
                "frame_count": 1,
                "noise_fraction": 0.045,
                "smoothing_sigma_pixels": 2.5,
                "issues": ["master flat noise is 4.49% from 1 frame(s)"],
            }
        },
    )
    _finalize_stack_quality_flags(summary)
    assert summary.input_quality.flat_noise_fraction == pytest.approx(0.045)
    assert summary.flagged
    assert "flat calibration: master flat noise is 4.49% from 1 frame(s)" in summary.flag_reasons


def test_a_stack_with_good_flats_has_no_flat_flag() -> None:
    """No flat issues add no flag reason."""
    summary = make_summary()
    summary.input_quality = assess_input_quality(
        10, 10, [], None, {"flat_calibration": {"frame_count": 40, "noise_fraction": 0.001}}
    )
    _finalize_stack_quality_flags(summary)
    assert not any(reason.startswith("flat calibration") for reason in summary.flag_reasons)


def test_a_colour_stack_with_one_dead_channel_is_flagged(tmp_path: Path) -> None:
    """A red channel that is 90% zero flags the stack (worst channel)."""
    generator = np.random.default_rng(1)
    data = generator.uniform(0.01, 0.5, (3, 100, 100)).astype(np.float32)
    data[0][generator.random((100, 100)) < 0.9] = 0.0
    path = tmp_path / "dead_red.fits"
    fits.writeto(path, data, overwrite=True)
    summary = make_summary()

    _measure_calibration_health(summary, str(path), False, {})
    _finalize_stack_quality_flags(summary)

    assert summary.stacking_metrics.zero_pixel_fraction == pytest.approx(0.9, abs=0.03)
    assert summary.stacking_metrics.zero_fraction_flagged
    assert any("exactly zero" in reason for reason in summary.flag_reasons)


def test_the_summary_records_the_rejection_limits_and_whether_the_floor_applied() -> None:
    """The resolved parameters carry the low and high limits and the floor."""
    diagnostics = {
        "rejection_sigma_low": 3.0,
        "rejection_sigma_high": 2.5,
        "rejection_sigma_mode": "adaptive",
        "rejection_sigma_floor": 2.5,
        "rejection_sigma_low_extra": 0.5,
        "rejection_sigma_floor_applied": True,
    }

    summary = _base_stack_quality_summary(
        SimpleNamespace(id="M 13"),
        False,
        5,
        [],
        [],
        diagnostics,  # type: ignore[arg-type]
    )

    parameters = summary.resolved_parameters
    assert parameters["rejection_sigma_low"] == pytest.approx(3.0)
    assert parameters["rejection_sigma_high"] == pytest.approx(2.5)
    assert parameters["rejection_sigma_mode"] == "adaptive"
    assert parameters["rejection_sigma_floor"] == pytest.approx(2.5)
    assert parameters["rejection_sigma_low_extra"] == pytest.approx(0.5)
    assert parameters["rejection_sigma_floor_applied"] is True
