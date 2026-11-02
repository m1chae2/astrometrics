"""Purpose: Unit tests for the group and calibration fields of a summary.

Description: A stack's quality summary now carries one entry per exposure
length, the exposure that would keep the brightest star below the ceiling, and
signs that calibration left the stack blank (mostly exact zeros, or Siril's
"many negative pixels" warning). These tests check the fields are filled from
a run's diagnostics, that a blank imaging stack is flagged while a spectral
stack with a legitimately dark sky is not, and that a group left out of the
combined image is named in the flags.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.quality_summary import (
    StackingPipelineQualityMetrics,
    StackQualitySummary,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.calibration_health import measure_calibration_health
from astrometricslib.pipelines.stacking.post_processing.record_quality import stacking_notice_measurements
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import calibration_gates
from astrometricslib.pipelines.stacking.stage import (
    _base_stack_quality_summary,
    _record_exposure_groups,
    _record_output_gates,
)


def failed_reasons(summary: StackQualitySummary) -> list[str]:
    """List the sentences of the summary's failed gates.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The summary.

    Returns
    -------
    reasons : `list` [`str`]
        The detail of each failed gate.
    """
    return [gate.detail for gate in summary.gates if gate.status is GateStatus.FAILED]


def make_summary(is_spectral: bool = False) -> StackQualitySummary:
    """Build an empty stack summary.

    Returns
    -------
    summary : `StackQualitySummary`
        A summary with default metrics and no gate yet.
    """
    return StackQualitySummary(
        target_id="NGC 7023",
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


def test_group_entries_come_from_the_diagnostics() -> None:
    """Each entry becomes a group summary."""
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
    }

    _record_exposure_groups(summary, diagnostics)

    groups = summary.stacking_metrics.exposure_groups
    assert [group.exposure_seconds for group in groups] == [60.0, 300.0]
    assert [group.dark_applied for group in groups] == [False, True]
    assert [group.saturated for group in groups] == [False, True]


def test_a_stored_row_with_the_removed_exposure_recommendation_still_loads() -> None:
    """An old row holding `recommendedExposureSeconds` loads and drops it."""
    metrics = StackingPipelineQualityMetrics.model_validate({
        "isSpectral": False,
        "framesSubmitted": 10,
        "framesStacked": 10,
        "recommendedExposureSeconds": 42.5,
    })

    assert not hasattr(metrics, "recommended_exposure_seconds")
    assert "recommendedExposureSeconds" not in metrics.model_dump(by_alias=True)


def test_a_stack_without_group_entries_has_none() -> None:
    """A run that reported no groups has an empty list."""
    summary = make_summary()

    _record_exposure_groups(summary, {})

    assert summary.stacking_metrics.exposure_groups == []


def test_a_mostly_zero_imaging_stack_is_flagged_as_blank(tmp_path: Path) -> None:
    """A stack that is 99% zero pixels gets the blank-stack flag."""
    summary = make_summary()

    measure_calibration_health(summary, write_stack(tmp_path / "blank.fits", 0.99), {})
    _record_output_gates(summary)

    assert summary.stacking_metrics.zero_pixel_fraction > 0.95
    assert any("exactly zero" in reason for reason in failed_reasons(summary))


def test_a_normal_imaging_stack_is_not_flagged(tmp_path: Path) -> None:
    """A stack with almost no zero pixels is left alone."""
    summary = make_summary()

    measure_calibration_health(summary, write_stack(tmp_path / "fine.fits", 0.01), {})
    _record_output_gates(summary)

    assert not any("exactly zero" in reason for reason in failed_reasons(summary))


def test_a_mostly_zero_spectral_stack_is_flagged_but_a_nearly_clean_one_is_not(tmp_path: Path) -> None:
    """A spectral stack is flagged from 5% zero pixels.

    Library data: clean spectra read at most 0.4%, the bad ones 32% or more.
    """
    flagged = make_summary(is_spectral=True)
    measure_calibration_health(flagged, write_stack(tmp_path / "spec_bad.fits", 0.35), {})
    _record_output_gates(flagged)
    assert any("exactly zero" in reason for reason in failed_reasons(flagged))

    clean = make_summary(is_spectral=True)
    measure_calibration_health(clean, write_stack(tmp_path / "spec_ok.fits", 0.004), {})
    _record_output_gates(clean)
    assert not any("exactly zero" in reason for reason in failed_reasons(clean))


def test_the_negative_pixel_warning_from_siril_is_recorded_and_flagged(tmp_path: Path) -> None:
    """Siril's 99% warning surfaces in the summary and its flags."""
    summary = make_summary()
    diagnostics = {"negative_pixel_max_percent": 99, "negative_pixel_frames": 40}

    measure_calibration_health(summary, write_stack(tmp_path / "s.fits", 0.0), diagnostics)
    _record_output_gates(summary)

    assert summary.stacking_metrics.negative_pixel_max_percent == 99
    assert any("negative pixels" in reason for reason in failed_reasons(summary))


def test_a_small_negative_share_is_recorded_but_not_flagged(tmp_path: Path) -> None:
    """Below the limit the percentage is kept without raising a flag."""
    summary = make_summary()

    measure_calibration_health(
        summary, write_stack(tmp_path / "s.fits", 0.0), {"negative_pixel_max_percent": 10}
    )
    _record_output_gates(summary)

    assert summary.stacking_metrics.negative_pixel_max_percent == 10
    assert not any("negative pixels" in reason for reason in failed_reasons(summary))


def test_a_group_left_out_of_the_combined_image_is_named_in_a_notice_row() -> None:
    """Why a group is missing from the combined stack is a warning row."""
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
    context = PipelineRunContext(
        "NGC 7023", "stacking", "1.3.0", "job-1", "job-1", datetime(2026, 11, 1, tzinfo=UTC)
    )

    rows = stacking_notice_measurements(summary, context, "NGC7023_Stacked.fits")

    assert [row.reason for row in rows] == ["the 0.5 s exposure group could not be stacked"]
    assert rows[0].check_outcome is GateStatus.FAILED


def test_a_noisy_master_flat_fails_the_flat_noise_gate() -> None:
    """A noisy master flat fails the flat-noise gate with its sentence."""
    summary = make_summary()
    diagnostics = {
        "flat_calibration": {
            "frame_count": 1,
            "noise_fraction": 0.045,
            "smoothing_sigma_pixels": 2.5,
            "issues": ["master flat noise is 4.49% from 1 frame(s)"],
        }
    }
    for gate in calibration_gates(diagnostics):
        summary.record_gate(gate)
    assert "flat calibration: master flat noise is 4.49% from 1 frame(s)" in failed_reasons(summary)


def test_a_stack_with_good_flats_has_no_flat_warning() -> None:
    """No flat issues give no failed flat gate."""
    summary = make_summary()
    for gate in calibration_gates({"flat_calibration": {"frame_count": 40, "noise_fraction": 0.001}}):
        summary.record_gate(gate)
    assert not any(reason.startswith("flat calibration") for reason in failed_reasons(summary))


def test_a_colour_stack_with_one_dead_channel_is_flagged(tmp_path: Path) -> None:
    """A red channel that is 90% zero flags the stack (worst channel)."""
    generator = np.random.default_rng(1)
    data = generator.uniform(0.01, 0.5, (3, 100, 100)).astype(np.float32)
    data[0][generator.random((100, 100)) < 0.9] = 0.0
    path = tmp_path / "dead_red.fits"
    fits.writeto(path, data, overwrite=True)
    summary = make_summary()

    measure_calibration_health(summary, str(path), {})
    _record_output_gates(summary)

    assert summary.stacking_metrics.zero_pixel_fraction == pytest.approx(0.9, abs=0.03)
    assert any("exactly zero" in reason for reason in failed_reasons(summary))


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


def test_the_stack_summary_names_the_job_that_made_the_stack() -> None:
    """`summarize_stack` copies the job id, to list the job's warnings."""
    from astrometricslib.pipelines.stacking.post_processing.stack_quality_report import summarize_stack

    summary = make_summary()
    summary.provenance_activity_id = "job-stack-7"
    target = SimpleNamespace(
        id="M 81",
        stacking=SimpleNamespace(quality_summary=summary, stacked_image="/stacks/M81.fits"),
        spectral_stacking=None,
    )

    report = summarize_stack(target, "imaging")

    assert report.job_id == "job-stack-7"
    summary.provenance_activity_id = None
    assert summarize_stack(target, "imaging").job_id is None
