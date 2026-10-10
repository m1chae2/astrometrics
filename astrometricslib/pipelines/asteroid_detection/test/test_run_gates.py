"""Red-team tests for the asteroid-detection gates.

A search that found nothing says little unless it could have found something.
These tests make each gate fail on purpose, and check that a failed
known-asteroid query is told apart from an empty field. The last tests run the
real pipeline and the real `validate_output`.
"""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from astrometricslib.models.gate_result import GateStatus
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.pipelines.asteroid_detection import run_gates as rg
from astrometricslib.pipelines.asteroid_detection.pipeline import AsteroidDetectionPipeline
from astrometricslib.pipelines.asteroid_detection.runner import AsteroidDetectionPipelineAdapter
from astrometricslib.pipelines.asteroid_detection.test.test_moving_object_pipeline import (
    _build_moving_target,
    _light_frames,
)
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result

HEALTHY_METRICS = {
    "frames_with_wcs_estimate": 8,
    "frames_excluded_missing_pointing_metadata": 0,
    "candidates_rate_linearity_confirmed": 2,
    "candidates_ephemeris_matched": 2,
    "ephemeris_queries_attempted": 1,
    "ephemeris_queries_failed": 0,
    "residual_chains_tested": 3,
    "residual_chains_rejected": 1,
    "residual_chains_non_monotonic": 2,
    "residual_assumed_error_detections": 0,
    "residual_worst_accepted_ratio": 1.4,
}


def gates_for(awaiting: int = 0, minimum_frames: int = 3, **changes: int) -> dict:
    """Build the gates for a run with some counts changed.

    Returns
    -------
    gates : `dict` [`str`, `GateResult`]
        The gates keyed by name.
    """
    metrics = {**HEALTHY_METRICS, **changes}
    return {gate.name: gate for gate in rg.asteroid_run_gates(metrics, minimum_frames, awaiting)}


def test_a_healthy_run_passes_every_gate() -> None:
    """Enough frames, movers matched, residuals measured: five passes."""
    gates = gates_for()

    assert len(gates) == 5
    assert {gate.status for gate in gates.values()} == {GateStatus.PASSED}


def test_search_gate_says_not_checked_when_too_few_frames_were_searched() -> None:
    """Two frames cannot make a three-frame track; none found means little."""
    gate = gates_for(frames_with_wcs_estimate=2)[rg.SEARCH_FRAMES_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED
    assert "no mover could have been found" in gate.detail


def test_pointing_gate_goes_red_when_frames_lack_pointing_metadata() -> None:
    """Excluded frames fail the gate with the original sentence."""
    gate = gates_for(frames_excluded_missing_pointing_metadata=3)[rg.POINTING_METADATA_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.detail == "3 frame(s) excluded for missing RA/DEC/NAXIS pointing metadata"


def test_pointing_gate_is_not_checked_when_no_frame_was_considered() -> None:
    """No frames at all is not a pass."""
    gate = gates_for(frames_with_wcs_estimate=0)[rg.POINTING_METADATA_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED


def test_ephemeris_gate_goes_red_when_the_database_could_not_be_reached() -> None:
    """A failed query fails the gate; it must not look like an empty field."""
    gate = gates_for(ephemeris_queries_failed=1, candidates_ephemeris_matched=0)[rg.EPHEMERIS_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert "could not be reached" in gate.detail


def test_ephemeris_gate_is_not_checked_without_movers_or_without_a_query() -> None:
    """No confirmed mover, or no query made, is not a pass."""
    no_movers = gates_for(candidates_rate_linearity_confirmed=0)[rg.EPHEMERIS_GATE_NAME]
    assert no_movers.status is GateStatus.NOT_CHECKED

    never_asked = gates_for(ephemeris_queries_attempted=0)[rg.EPHEMERIS_GATE_NAME]
    assert never_asked.status is GateStatus.NOT_CHECKED


def test_unmatched_movers_gate_goes_red_with_the_manual_look_sentence() -> None:
    """A confirmed mover that matches nothing known fails the gate."""
    gate = gates_for(awaiting=1, candidates_ephemeris_matched=1)[rg.UNMATCHED_MOVERS_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.detail.endswith("worth a manual look")

    no_movers = gates_for(candidates_rate_linearity_confirmed=0)[rg.UNMATCHED_MOVERS_GATE_NAME]
    assert no_movers.status is GateStatus.NOT_CHECKED


def run_pipeline(tmp_path: Path, mocker: MockerFixture, **cone_search: object) -> tuple:
    """Run the real pipeline on the synthetic moving target.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Where the synthetic frames are written.
    mocker : `MockerFixture`
        Stubs the known-asteroid database.
    **cone_search
        Arguments for the stubbed `Skybot.cone_search`.

    Returns
    -------
    target : `Target`
        The synthetic target.
    pipeline : `AsteroidDetectionPipeline`
        The pipeline after its run.
    candidates : `list`
        What the run returned.
    """
    mocker.patch("astroquery.imcce.Skybot.cone_search", **cone_search)
    target = _build_moving_target(tmp_path)
    pipeline = AsteroidDetectionPipeline(MovingObjectConfig())
    candidates = pipeline.process(target.id, target.stacking.stacked_image, _light_frames(target))
    return target, pipeline, candidates


def test_the_pipeline_counts_a_failed_query_and_an_empty_field_differently(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """An unreachable database is a failure; an empty field is not."""
    (tmp_path / "empty").mkdir()
    (tmp_path / "down").mkdir()
    _target, empty_field, _ = run_pipeline(tmp_path / "empty", mocker, return_value=None)
    # One question at the first detection and one at the last.
    assert empty_field.last_run_metrics["ephemeris_queries_attempted"] == 2
    assert empty_field.last_run_metrics["ephemeris_queries_failed"] == 0

    _target, unreachable, _ = run_pipeline(
        tmp_path / "down", mocker, side_effect=RuntimeError("service error")
    )
    # The first question failed, so the second was never sent.
    assert unreachable.last_run_metrics["ephemeris_queries_attempted"] == 1
    assert unreachable.last_run_metrics["ephemeris_queries_failed"] == 1


@pytest.mark.parametrize(
    ("cone_search", "expected_ephemeris", "expected_flagged_text"),
    [
        ({"return_value": None}, GateStatus.PASSED, "worth a manual look"),
        ({"side_effect": RuntimeError("service error")}, GateStatus.FAILED, "could not be reached"),
    ],
)
def test_the_real_validate_output_tells_an_empty_field_from_an_offline_database(
    tmp_path: Path,
    mocker: MockerFixture,
    cone_search: dict,
    expected_ephemeris: GateStatus,
    expected_flagged_text: str,
) -> None:
    """The saved summary records which of the two happened."""
    target, pipeline, candidates = run_pipeline(tmp_path, mocker, **cone_search)
    result = Result(candidates=candidates, payload={"metrics": pipeline.last_run_metrics})
    request = PipelineRequest(target=target, catalog_access=None)

    summary = AsteroidDetectionPipelineAdapter().validate_output(request, result)

    assert summary.gate(rg.EPHEMERIS_GATE_NAME).status is expected_ephemeris
    assert any(expected_flagged_text in reason for reason in summary.flag_reasons)
    assert summary.flagged is True


def test_residual_gate_is_not_checked_when_no_chain_reached_the_straight_line_test() -> None:
    """With nothing judged, the gate says so instead of passing."""
    gate = gates_for(residual_chains_tested=0)[rg.RESIDUAL_CRITERION_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED
    assert "no residual was judged" in gate.detail


def test_residual_gate_goes_red_when_the_position_error_was_only_assumed() -> None:
    """A verdict that rests on an unmeasured error fails the gate."""
    gate = gates_for(residual_assumed_error_detections=4)[rg.RESIDUAL_CRITERION_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(4.0)
    assert "assumed error" in gate.detail


def test_residual_gate_passes_with_the_worst_accepted_ratio_and_the_limit() -> None:
    """A measured error passes, and the gate quotes the ratio and the limit."""
    gates = {
        gate.name: gate
        for gate in rg.asteroid_run_gates(HEALTHY_METRICS, 3, 0, residual_rms_max_multiple=2.5)
    }
    gate = gates[rg.RESIDUAL_CRITERION_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(1.4)
    assert gate.limit == pytest.approx(2.5)
    assert "1 rejected" in gate.detail
    assert "2 rejected for moving back" in gate.detail
