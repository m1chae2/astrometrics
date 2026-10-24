"""Red-team tests for the asteroid-detection gates and their measurements.

A search that found nothing says little unless it could have found something.
These tests make each gate fail on purpose, and check that a failed
known-asteroid query is told apart from an empty field. The last tests run the
real pipeline and the real `validate_output`.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import StorageError
from astrometricslib.models.moving_object_config import MovingObjectConfig
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import MeasurementOrigin, MeasurementStatus, SubjectLevel
from astrometricslib.models.quality.quality_summary import AsteroidDetectionPipelineQualityMetrics
from astrometricslib.pipelines.asteroid_detection import record_quality as rg
from astrometricslib.pipelines.asteroid_detection.metrics import (
    ASTEROID_DETECTION_METRICS,
    ASTEROID_GATE_EPHEMERIS_CROSS_MATCH,
    ASTEROID_GATE_SEARCH_FRAMES,
    ASTEROID_GATE_TRACK_RESIDUALS,
    ASTEROID_RESIDUAL_WORST_ACCEPTED_RATIO,
)
from astrometricslib.pipelines.asteroid_detection.pipeline import AsteroidDetectionPipeline
from astrometricslib.pipelines.asteroid_detection.runner import AsteroidDetectionPipelineAdapter
from astrometricslib.pipelines.asteroid_detection.test.test_moving_object_pipeline import (
    _build_moving_target,
    _light_frames,
)
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext, make_run_id

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


# -- Quality measurements next to the summary ---------------------------

MEASURED_AT = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)


def _context(job_id: str | None = "job-1") -> PipelineRunContext:
    """Build a run context for the asteroid pipeline.

    Returns
    -------
    context : `PipelineRunContext`
        A context for the target ``TestAsteroidTarget``.
    """
    return PipelineRunContext(
        target_id="TestAsteroidTarget",
        pipeline_name="asteroid_detection",
        pipeline_version="1.1.0",
        run_id=make_run_id(job_id),
        job_id=job_id,
        measured_at=MEASURED_AT,
    )


def _declared_names() -> set[str]:
    """Name every metric the asteroid pipeline declares.

    Returns
    -------
    names : `set` [`str`]
        The declared metric names.
    """
    return {spec.name for spec in ASTEROID_DETECTION_METRICS}


def test_every_numeric_summary_field_and_gate_is_declared() -> None:
    """The residual fields exist on the model and every field has a metric."""
    fields = set(AsteroidDetectionPipelineQualityMetrics.model_fields)
    assert {
        "residual_chains_tested",
        "residual_chains_rejected",
        "residual_chains_non_monotonic",
        "residual_assumed_error_detections",
        "residual_worst_accepted_ratio",
    } <= fields
    declared = _declared_names()
    # A count's registered name ends with its unit; the ratio has no unit.
    assert {
        f"asteroid_detection.{field}" if field.endswith("_ratio") else f"asteroid_detection.{field}_count"
        for field in fields
    } <= declared
    assert {
        f"asteroid_detection.gate_{name}"
        for name in (
            rg.SEARCH_FRAMES_GATE_NAME,
            rg.POINTING_METADATA_GATE_NAME,
            rg.EPHEMERIS_GATE_NAME,
            rg.UNMATCHED_MOVERS_GATE_NAME,
            rg.RESIDUAL_CRITERION_GATE_NAME,
        )
    } <= declared


def test_the_residual_fields_survive_the_summary_model() -> None:
    """The residual counts are kept instead of being dropped."""
    summary_metrics = AsteroidDetectionPipelineQualityMetrics(
        **HEALTHY_METRICS, candidates_detected=3, candidates_persistence_confirmed=3
    )
    assert summary_metrics.residual_chains_tested == 3
    assert summary_metrics.residual_chains_non_monotonic == 2
    assert summary_metrics.residual_worst_accepted_ratio == pytest.approx(1.4)


def test_a_healthy_run_emits_one_measurement_per_declared_metric() -> None:
    """Counts, the ratio and five gates make every declared metric once."""
    metrics = {**HEALTHY_METRICS, "candidates_detected": 3, "candidates_persistence_confirmed": 3}
    gates = rg.asteroid_run_gates(metrics, 3, 0, 2.0)
    measurements = rg.asteroid_run_measurements(metrics, gates, _context())

    assert [m.metric for m in measurements].count(measurements[0].metric) == 1
    assert {m.metric for m in measurements} == _declared_names()
    assert len({m.metric for m in measurements}) == len(measurements)
    by_metric = {m.metric: m for m in measurements}
    residual = by_metric[ASTEROID_GATE_TRACK_RESIDUALS]
    assert residual.check_outcome is GateStatus.PASSED
    assert residual.value == pytest.approx(1.4)
    assert residual.check_limit is not None
    assert residual.check_limit.value == pytest.approx(2.0)
    assert residual.check_limit.tier is not LimitTier.VALIDATED_DATASET
    assert by_metric[ASTEROID_RESIDUAL_WORST_ACCEPTED_RATIO].value == pytest.approx(1.4)
    assert all(m.run_id == "job-1" and m.origin is MeasurementOrigin.MEASURED for m in measurements)
    assert all(m.subject.level is SubjectLevel.RUN for m in measurements)


def test_unreliable_numbers_are_stored_as_not_measured() -> None:
    """A missing ratio, failed residual gate or unchecked gate has no value."""
    metrics = {
        **HEALTHY_METRICS,
        "candidates_detected": 0,
        "candidates_persistence_confirmed": 0,
        "residual_chains_tested": 0,
        "residual_worst_accepted_ratio": 0.0,
        "frames_with_wcs_estimate": 1,
    }
    gates = rg.asteroid_run_gates(metrics, 3, 0, 2.0)
    by_metric = {m.metric: m for m in rg.asteroid_run_measurements(metrics, gates, _context())}

    ratio = by_metric[ASTEROID_RESIDUAL_WORST_ACCEPTED_RATIO]
    assert ratio.status is MeasurementStatus.NOT_MEASURED
    assert ratio.value is None
    search = by_metric[ASTEROID_GATE_SEARCH_FRAMES]
    assert search.status is MeasurementStatus.NOT_MEASURED
    assert search.check_outcome is GateStatus.NOT_CHECKED
    assert "no mover could have been found" in search.reason

    failed_metrics = {**HEALTHY_METRICS, "residual_assumed_error_detections": 4}
    failed_gates = rg.asteroid_run_gates(failed_metrics, 3, 0, 2.0)
    failed = {m.metric: m for m in rg.asteroid_run_measurements(failed_metrics, failed_gates, _context())}
    residual = failed[ASTEROID_GATE_TRACK_RESIDUALS]
    assert residual.check_outcome is GateStatus.FAILED
    assert residual.value is None
    assert residual.check_limit is not None


def test_the_ephemeris_gate_always_stores_the_failed_query_count() -> None:
    """The known-asteroid gate has one meaning: failed searches, limit zero."""
    healthy = {
        m.metric: m
        for m in rg.asteroid_run_measurements(
            HEALTHY_METRICS, rg.asteroid_run_gates(HEALTHY_METRICS, 3, 0), _context()
        )
    }
    down_metrics = {**HEALTHY_METRICS, "ephemeris_queries_failed": 1, "candidates_ephemeris_matched": 0}
    down = {
        m.metric: m
        for m in rg.asteroid_run_measurements(
            down_metrics, rg.asteroid_run_gates(down_metrics, 3, 0), _context()
        )
    }

    assert healthy[ASTEROID_GATE_EPHEMERIS_CROSS_MATCH].value == pytest.approx(0.0)
    assert healthy[ASTEROID_GATE_EPHEMERIS_CROSS_MATCH].check_outcome is GateStatus.PASSED
    assert down[ASTEROID_GATE_EPHEMERIS_CROSS_MATCH].value == pytest.approx(1.0)
    assert down[ASTEROID_GATE_EPHEMERIS_CROSS_MATCH].check_outcome is GateStatus.FAILED


def _use_temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualityStore:
    """Point the quality store at a temporary library.

    Returns
    -------
    store : `QualityStore`
        The store on the temporary library's database.
    """
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    return QualityStore(str(tmp_path / "astrometrics.db"))


def test_the_runner_writes_one_row_per_declared_metric_and_a_repeat_writes_none(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate_output stores the run once; a second store adds nothing."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    target, pipeline, candidates = run_pipeline(tmp_path, mocker, return_value=None)
    result = Result(candidates=candidates, payload={"metrics": pipeline.last_run_metrics})
    request = PipelineRequest(
        target=target, catalog_access=None, options={"job_id": "job-7", "measured_at": MEASURED_AT}
    )

    summary = AsteroidDetectionPipelineAdapter().validate_output(request, result)
    rows = [row for row in store.query(target_id=target.id, latest_only=False) if row.run_id == "job-7"]

    assert {row.metric for row in rows} == _declared_names()
    assert len(rows) == len(_declared_names())
    assert summary.asteroid_detection_metrics.residual_chains_tested is not None
    again = rg.record_asteroid_quality(pipeline.last_run_metrics, list(summary.gates), _context("job-7"))
    assert again == 0


def test_a_run_without_a_job_gets_a_generated_run_id(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no job id the rows still share one generated id."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    target, pipeline, candidates = run_pipeline(tmp_path, mocker, return_value=None)
    result = Result(candidates=candidates, payload={"metrics": pipeline.last_run_metrics})
    request = PipelineRequest(target=target, catalog_access=None, options={"measured_at": MEASURED_AT})

    AsteroidDetectionPipelineAdapter().validate_output(request, result)
    rows = store.query(target_id=target.id, latest_only=False)

    assert len(rows) == len(_declared_names())
    assert len({row.run_id for row in rows}) == 1
    assert rows[0].run_id.startswith("run-")
    assert rows[0].job_id is None


def test_a_storage_failure_does_not_fail_the_run(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken store is logged and the summary is still returned."""
    _use_temporary_store(tmp_path, monkeypatch)
    mocker.patch(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.record_measurements",
        side_effect=StorageError("disk full"),
    )
    target, pipeline, candidates = run_pipeline(tmp_path, mocker, return_value=None)
    result = Result(candidates=candidates, payload={"metrics": pipeline.last_run_metrics})
    request = PipelineRequest(target=target, catalog_access=None, options={"measured_at": MEASURED_AT})

    summary = AsteroidDetectionPipelineAdapter().validate_output(request, result)

    assert summary.gates
