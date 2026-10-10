"""Red-team tests for the photometry run gates.

Each gate has to fail on a run that is truly bad, pass on a good one, and
read "not checked" when it could not look. The last test runs the real
`validate_output`, which no earlier test covered.
"""

from datetime import datetime
from typing import Any

import pytest

from astrometricslib.models.gate_result import GateResult, GateStatus
from astrometricslib.models.quality_summary import ExcludedFrame
from astrometricslib.models.stellar_source import PhotometryResult
from astrometricslib.models.target import Target
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.post_processing import run_gates as rg
from astrometricslib.pipelines.photometry.pre_processing.observation_times import (
    TIME_BASIS_BJD_TDB,
    TIME_BASIS_BJD_TDB_GEOCENTRIC,
    TIME_BASIS_UTC_START,
)
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import ComparisonSetResult
from astrometricslib.pipelines.pipeline_base import PipelineRequest


def comparison_set(size: int = 12, frames: int = 40, **changes: Any) -> ComparisonSetResult:
    """Build the comparison-set record of a healthy session.

    Parameters
    ----------
    size : `int`, optional
        The number of comparison stars in the set.
    frames : `int`, optional
        The number of frames of the session.
    **changes
        Fields of `ComparisonSetResult` to replace.

    Returns
    -------
    record : `ComparisonSetResult`
        A set with ``size`` stars used in every one of ``frames`` frames.
    """
    fields: dict[str, Any] = {
        "star_ids": tuple(f"Star_{index}" for index in range(size)),
        "rejected_ids": ("Star_90",),
        "scatter_mag": 0.0012,
        "expected_error_mag": 0.0011,
        "candidate_count": size + 1,
        "uses_errors": True,
        "frame_sizes": (size,) * frames,
    }
    fields.update(changes)
    return ComparisonSetResult(**fields)


def good_run(**changes: Any) -> dict[str, GateResult]:
    """Build the gates for a healthy run, with some inputs changed.

    Parameters
    ----------
    **changes
        Inputs of `photometry_run_gates` to replace.

    Returns
    -------
    gates : `dict` [`str`, `GateResult`]
        The gates keyed by name.
    """
    inputs: dict[str, Any] = {
        "frames_contributed": 40,
        "rejected_frame_count": 2,
        "frames_without_timestamp": 0,
        "session_count": 2,
        "session_empty_reasons": [],
        "sessions_missing_wcs": [],
        "no_work_reason": None,
        "comparison_sets": [comparison_set(12), comparison_set(15)],
        "registration_drifts_px": [3.0, 5.0, None],
        "stars_with_scatter": 80,
        # Catalogued variables clearly scatter more than the others here.
        "known_variable_cvs": [0.3 + 0.01 * index for index in range(20)],
        "unlisted_cvs": [0.05 + 0.001 * index for index in range(60)],
        "cutoff_cv": 0.04,
        "median_flux_error_mag": 0.02,
        "errors_assume_unit_gain": False,
    }
    inputs.update(changes)
    return {gate.name: gate for gate in rg.photometry_run_gates(**inputs)}


def test_a_healthy_run_passes_every_gate() -> None:
    """Nothing wrong gives eleven passes and no failure."""
    gates = good_run()

    assert len(gates) == 11
    assert {gate.status for gate in gates.values()} == {GateStatus.PASSED}


def test_outlier_rejection_gate_goes_red_on_a_cloudy_night() -> None:
    """Ten of 30 frames rejected (33%) fails; 2 of 40 passes."""
    failed = good_run(frames_contributed=30, rejected_frame_count=10)[rg.ENSEMBLE_REJECTION_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert "10 of 30" in failed.detail

    assert good_run()[rg.ENSEMBLE_REJECTION_GATE_NAME].status is GateStatus.PASSED


def test_outlier_rejection_gate_needs_enough_frames_to_judge() -> None:
    """Three frames are too few to call any an outlier; that is not a pass."""
    gate = good_run(frames_contributed=3, rejected_frame_count=1)[rg.ENSEMBLE_REJECTION_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED


def test_timestamp_gate_goes_red_when_frames_have_no_capture_time() -> None:
    """Frames without a timestamp fail the gate."""
    gate = good_run(frames_without_timestamp=3)[rg.CAPTURE_TIMESTAMP_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.detail == "3 frame(s) excluded for missing capture timestamp"


def test_timestamp_gate_detail_quotes_why_header_dates_were_unreadable() -> None:
    """Reasons for unreadable DATE-OBS headers reach the gate's detail.

    Four frames fail. The gate quotes the first three reasons and counts
    the rest, so a long list does not flood the summary.
    """
    reasons = [f"f{index}.fits: DATE-OBS is missing from the FITS header" for index in range(4)]

    gate = good_run(frames_without_timestamp=4, timestamp_exclusion_reasons=reasons)[
        rg.CAPTURE_TIMESTAMP_GATE_NAME
    ]

    assert gate.status is GateStatus.FAILED
    assert gate.detail.startswith("4 frame(s) excluded for missing capture timestamp: f0.fits")
    assert "f2.fits: DATE-OBS is missing" in gate.detail
    assert "f3.fits" not in gate.detail
    assert gate.detail.endswith("; and 1 more")


def test_validate_output_lists_frames_with_unreadable_date_obs() -> None:
    """`validate_output` fails the gate and keeps each frame's reason."""
    request = PipelineRequest(target=Target(id="Test Target"), catalog_access=None)
    result = runner._empty_photometry_result("placeholder")
    result.payload["unreadable_date_obs_frames"] = [
        ExcludedFrame(path="/data/night/a.fits", reason="DATE-OBS is missing from the FITS header"),
        ExcludedFrame(path="/data/night/b.fits", reason="DATE-OBS 'not a date' is not a valid FITS date"),
    ]

    summary = runner.PhotometryPipelineAdapter().validate_output(request, result)

    gate = summary.gate(rg.CAPTURE_TIMESTAMP_GATE_NAME)
    assert gate.status is GateStatus.FAILED
    assert "2 frame(s) excluded" in gate.detail
    assert "a.fits: DATE-OBS is missing" in gate.detail
    assert "b.fits: DATE-OBS 'not a date'" in gate.detail
    excluded = {frame.path: frame.reason for frame in summary.photometry_metrics.rejected_frames}
    assert "DATE-OBS is missing" in excluded["/data/night/a.fits"]
    assert "not a date" in excluded["/data/night/b.fits"]


def test_session_content_gate_goes_red_for_an_empty_session() -> None:
    """An empty session fails; no sessions at all is not checked."""
    failed = good_run(session_empty_reasons=["session A detected zero stars"])[rg.SESSION_CONTENT_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert failed.detail == "session A detected zero stars"

    unchecked = good_run(session_count=0)[rg.SESSION_CONTENT_GATE_NAME]
    assert unchecked.status is GateStatus.NOT_CHECKED


def test_plate_solve_gate_goes_red_and_skips_a_single_session() -> None:
    """An unsolved session fails; one session needs no matching."""
    failed = good_run(sessions_missing_wcs=["night-2"])[rg.SESSION_PLATE_SOLVE_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert "night-2" in failed.detail

    single = good_run(session_count=1)[rg.SESSION_PLATE_SOLVE_GATE_NAME]
    assert single.status is GateStatus.NOT_CHECKED


def test_work_gate_goes_red_when_the_run_found_nothing_to_do() -> None:
    """A run with a no-work reason fails and says why."""
    gate = good_run(no_work_reason="no frames for the requested filter")[rg.PHOTOMETRY_WORK_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.detail == "no frames for the requested filter"


def test_ensemble_gate_goes_red_when_a_session_had_too_few_comparison_stars() -> None:
    """A set of four stars fails; no session normalized is not checked."""
    failed = good_run(comparison_sets=[comparison_set(12), comparison_set(4)])[
        rg.COMPARISON_ENSEMBLE_GATE_NAME
    ]
    assert failed.status is GateStatus.FAILED
    assert failed.measured_value == pytest.approx(4.0)
    assert failed.limit == pytest.approx(5.0)
    assert "fewer than 5 comparison stars (the smallest had 4)" in failed.detail

    unchecked = good_run(comparison_sets=[])[rg.COMPARISON_ENSEMBLE_GATE_NAME]
    assert unchecked.status is GateStatus.NOT_CHECKED


def test_ensemble_gate_goes_red_when_the_set_changes_within_a_session() -> None:
    """A session that used different numbers of comparison stars fails."""
    moving = comparison_set(12, frame_sizes=(12,) * 20 + (11,) * 20)

    gate = good_run(comparison_sets=[comparison_set(12), moving])[rg.COMPARISON_ENSEMBLE_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert "different number of comparison stars in different frames" in gate.detail


def test_ensemble_gate_reports_the_set_size_that_it_is_fixed_and_the_scatter() -> None:
    """A passing gate gives set sizes, that they are fixed, and the scatter."""
    gate = good_run()[rg.COMPARISON_ENSEMBLE_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(12.0)
    assert "comparison set sizes 12, 15" in gate.detail
    assert "the same in every frame of its session" in gate.detail
    assert "ensemble scatter 0.0012 mag" in gate.detail
    assert "errors predict 0.0011 mag" in gate.detail
    assert "2 star(s) turned away" in gate.detail


def test_drift_gate_goes_red_on_lost_tracking() -> None:
    """A 60 px drift fails; no recorded drift is not a pass."""
    failed = good_run(registration_drifts_px=[2.0, 60.0])[rg.REGISTRATION_DRIFT_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert failed.measured_value == pytest.approx(60.0)

    unchecked = good_run(registration_drifts_px=[None, None])[rg.REGISTRATION_DRIFT_GATE_NAME]
    assert unchecked.status is GateStatus.NOT_CHECKED
    assert (
        good_run(registration_drifts_px=[])[rg.REGISTRATION_DRIFT_GATE_NAME].status is GateStatus.NOT_CHECKED
    )


def test_scatter_population_gate_is_not_checked_with_few_stars() -> None:
    """Four stars cannot say what normal scatter is."""
    gate = good_run(stars_with_scatter=4)[rg.SCATTER_POPULATION_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED
    assert "4 star(s)" in gate.detail


def test_the_real_validate_output_records_the_gates_for_a_run_with_no_work() -> None:
    """A run with nothing to do is flagged; the other gates are unchecked."""
    request = PipelineRequest(target=Target(id="Test Target"), catalog_access=None)
    result = runner._empty_photometry_result("no frames for the requested filter")

    summary = runner.PhotometryPipelineAdapter().validate_output(request, result)

    assert summary.flagged is True
    assert "no frames for the requested filter" in summary.flag_reasons
    assert summary.gate(rg.PHOTOMETRY_WORK_GATE_NAME).status is GateStatus.FAILED
    for name in (
        rg.COMPARISON_ENSEMBLE_GATE_NAME,
        rg.REGISTRATION_DRIFT_GATE_NAME,
        rg.SCATTER_POPULATION_GATE_NAME,
    ):
        assert summary.gate(name).status is GateStatus.NOT_CHECKED


def test_the_discrimination_gate_goes_red_when_scatter_cannot_see_known_variables() -> None:
    """Known variables no noisier than the rest give a failed gate."""
    same = [0.05 + 0.001 * index for index in range(60)]
    gate = good_run(known_variable_cvs=same[:20], unlisted_cvs=same[20:])[
        rg.VARIABILITY_DISCRIMINATION_GATE_NAME
    ]

    assert gate.status is GateStatus.FAILED
    assert "does not pick out the 20 stars the catalogs list as variable" in gate.detail


def test_the_discrimination_gate_passes_when_known_variables_stand_out() -> None:
    """Catalogued variables with clearly more scatter pass."""
    gate = good_run()[rg.VARIABILITY_DISCRIMINATION_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(1.0)


def test_the_discrimination_gate_is_not_checked_without_enough_catalogued_stars() -> None:
    """Too few known variables or unlisted stars cannot show skill."""
    few_known = good_run(known_variable_cvs=[0.4] * 5)[rg.VARIABILITY_DISCRIMINATION_GATE_NAME]
    few_unlisted = good_run(unlisted_cvs=[0.05] * 10)[rg.VARIABILITY_DISCRIMINATION_GATE_NAME]
    nothing = good_run(known_variable_cvs=[], unlisted_cvs=[])[rg.VARIABILITY_DISCRIMINATION_GATE_NAME]

    assert few_known.status is GateStatus.NOT_CHECKED
    assert few_unlisted.status is GateStatus.NOT_CHECKED
    assert nothing.status is GateStatus.NOT_CHECKED


def test_the_amplitude_gate_goes_red_when_the_cutoff_hides_small_variables() -> None:
    """A 30% scatter cutoff needs a variable of about 0.9 mag."""
    gate = good_run(cutoff_cv=0.3)[rg.DETECTABLE_AMPLITUDE_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(0.92, abs=0.01)
    assert "smaller variables cannot be flagged" in gate.detail


def test_the_amplitude_gate_passes_for_a_sensitive_run_and_skips_a_run_with_no_cutoff() -> None:
    """A 4% cutoff sees about 0.12 mag; no cutoff is not checked."""
    assert good_run()[rg.DETECTABLE_AMPLITUDE_GATE_NAME].status is GateStatus.PASSED
    assert good_run(cutoff_cv=None)[rg.DETECTABLE_AMPLITUDE_GATE_NAME].status is GateStatus.NOT_CHECKED


def test_the_timestamp_gate_names_the_time_scale_when_it_passes() -> None:
    """A passing gate says which time scale the light curves use."""
    gate = good_run(time_basis="BJD_TDB, mid-exposure")[rg.CAPTURE_TIMESTAMP_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.detail == "times: BJD_TDB, mid-exposure"


def test_the_timestamp_gate_keeps_its_detail_and_adds_the_time_scale_when_it_fails() -> None:
    """The failed detail keeps its first sentence; the time scale follows."""
    gate = good_run(frames_without_timestamp=3, time_basis=TIME_BASIS_UTC_START)[
        rg.CAPTURE_TIMESTAMP_GATE_NAME
    ]

    assert gate.status is GateStatus.FAILED
    assert gate.detail.startswith("3 frame(s) excluded for missing capture timestamp")
    assert gate.detail.endswith(f"Times of the other frames: {TIME_BASIS_UTC_START}")


def test_the_flux_uncertainty_gate_reports_the_median_error_when_the_gain_is_known() -> None:
    """With a known gain the gate passes and quotes the median error in mag."""
    gate = good_run(median_flux_error_mag=0.0123)[rg.FLUX_UNCERTAINTY_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(0.0123)
    assert "0.0123 mag" in gate.detail


def test_the_flux_uncertainty_gate_says_when_read_noise_was_assumed_zero() -> None:
    """A known gain with an unknown read noise still passes, with a caveat."""
    gate = good_run(errors_assume_zero_read_noise=True)[rg.FLUX_UNCERTAINTY_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert "read noise assumed to be zero" in gate.detail


def test_the_flux_uncertainty_gate_is_not_checked_when_unit_gain_was_assumed() -> None:
    """Errors that assume 1 e-/ADU are a guide, so the gate cannot pass them.

    The sentence still quotes the median error so a reader can see its size.
    """
    gate = good_run(errors_assume_unit_gain=True, median_flux_error_mag=0.0456)[rg.FLUX_UNCERTAINTY_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED
    assert "errors assume unit gain" in gate.detail
    assert "0.0456 mag" in gate.detail


def test_the_flux_uncertainty_gate_is_not_checked_without_any_errors() -> None:
    """A run whose light curves carry no errors has nothing to report."""
    gate = good_run(median_flux_error_mag=None)[rg.FLUX_UNCERTAINTY_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED
    assert "no light curve carries measurement errors" in gate.detail


def _light_curve(time_basis: str | None, has_bjd: bool) -> PhotometryResult:
    """Build a two-point light curve with or without BJD_TDB times.

    Returns
    -------
    light_curve : `PhotometryResult`
        A light curve whose time basis is `time_basis`.
    """
    stamps = [datetime(2026, 5, 24, 4, 0, 0), datetime(2026, 5, 24, 4, 1, 0)]
    return PhotometryResult(
        timestamps=stamps,
        time_bjd_tdb=[2461184.67, 2461184.68] if has_bjd else [],
        time_basis=time_basis,
    )


def test_the_run_time_basis_is_the_least_exact_one_any_light_curve_has() -> None:
    """One light curve without BJD_TDB times makes the run read UTC start."""
    full = _light_curve(TIME_BASIS_BJD_TDB, True)
    geocentric = _light_curve(TIME_BASIS_BJD_TDB_GEOCENTRIC, True)
    bare = _light_curve(None, False)

    assert runner._run_time_basis([full, full]) == TIME_BASIS_BJD_TDB
    assert runner._run_time_basis([full, geocentric]) == TIME_BASIS_BJD_TDB_GEOCENTRIC
    assert runner._run_time_basis([full, geocentric, bare]) == TIME_BASIS_UTC_START
    assert runner._run_time_basis([]) is None
