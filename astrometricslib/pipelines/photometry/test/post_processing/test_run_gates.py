"""Red-team tests for the photometry run gates.

Each gate has to fail on a run that is truly bad, pass on a good one, and
read "not checked" when it could not look. The last test runs the real
`validate_output`, which no earlier test covered.
"""

from typing import Any

import pytest

from astrometricslib.models.gate_result import GateResult, GateStatus
from astrometricslib.models.quality_summary import ExcludedFrame
from astrometricslib.models.target import Target
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.post_processing import run_gates as rg
from astrometricslib.pipelines.pipeline_base import PipelineRequest


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
        "ensemble_sizes": [60, 58, 61, 60],
        "registration_drifts_px": [3.0, 5.0, None],
        "stars_with_scatter": 80,
        # Catalogued variables clearly scatter more than the others here.
        "known_variable_cvs": [0.3 + 0.01 * index for index in range(20)],
        "unlisted_cvs": [0.05 + 0.001 * index for index in range(60)],
        "cutoff_cv": 0.04,
    }
    inputs.update(changes)
    return {gate.name: gate for gate in rg.photometry_run_gates(**inputs)}


def test_a_healthy_run_passes_every_gate() -> None:
    """Nothing wrong gives ten passes and no failure."""
    gates = good_run()

    assert len(gates) == 10
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


def test_ensemble_gate_goes_red_when_a_frame_had_too_few_comparison_stars() -> None:
    """A frame with four comparison stars fails; none recorded is unchecked."""
    failed = good_run(ensemble_sizes=[60, 4, 58])[rg.COMPARISON_ENSEMBLE_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert failed.measured_value == pytest.approx(4.0)

    unchecked = good_run(ensemble_sizes=[])[rg.COMPARISON_ENSEMBLE_GATE_NAME]
    assert unchecked.status is GateStatus.NOT_CHECKED


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
