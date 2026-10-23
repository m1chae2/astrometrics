"""Tests for the measurements the photometry record step emits.

The gates and the summary stay as they are; the measurements are written next
to them. These tests check that every declared metric is emitted once, that a
gate keeps its outcome and limit, that a gate's number means one thing in
every outcome, and that the real `validate_output` stores a run once.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import StorageError
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.measurement import MeasurementOrigin, MeasurementStatus, SubjectLevel
from astrometricslib.models.target import Target
from astrometricslib.pipelines.photometry import runner
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_CENTROID_FALLBACK_FRACTION,
    PHOTOMETRY_COMPARISON_SCATTER_OVER_PREDICTED,
    PHOTOMETRY_CORNER_DISPLACEMENT_P95_PX,
    PHOTOMETRY_GATE_COMPARISON_ENSEMBLE,
    PHOTOMETRY_GATE_FLUX_UNCERTAINTY,
    PHOTOMETRY_GATE_PHOTOMETRY_WORK,
    PHOTOMETRY_GATE_REGISTRATION_DRIFT,
    PHOTOMETRY_LIGHT_CURVE_SCATTER_RMS_MEDIAN_MAG,
    PHOTOMETRY_METRICS,
    PHOTOMETRY_STARS_PROCESSED,
)
from astrometricslib.pipelines.photometry.post_processing import record_quality as rq
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import ComparisonSetResult
from astrometricslib.pipelines.photometry.test.conformance_fixture import (
    FIXTURE_CENTROID_SUMMARIES,
    FIXTURE_COMPARISON_SETS,
    photometry_fixture_gates,
    photometry_fixture_inputs,
    photometry_fixture_metrics,
)
from astrometricslib.pipelines.pipeline_base import PipelineRequest
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext

MEASURED_AT = datetime(2026, 10, 22, 22, 0, tzinfo=UTC)


def _declared_names() -> set[str]:
    """Give the name of every metric the photometry pipeline declares.

    Returns
    -------
    names : `set` of `str`
        The declared names.
    """
    return {spec.name for spec in PHOTOMETRY_METRICS}


def _context(run_id: str = "job-1") -> PipelineRunContext:
    """Build a run context with a fixed time.

    Parameters
    ----------
    run_id : `str`, optional
        The run and job id.

    Returns
    -------
    context : `PipelineRunContext`
        A context for a test target.
    """
    return PipelineRunContext("Test Target", "photometry", "1.3.0", run_id, run_id, MEASURED_AT)


def _healthy_by_name(**input_changes: object) -> dict:
    """Build the healthy run's measurements, keyed by metric name.

    Parameters
    ----------
    **input_changes
        Fields of `PhotometryRunInputs` to replace.

    Returns
    -------
    measurements : `dict` [`str`, `QualityMeasurement`]
        The run's measurements.
    """
    from dataclasses import replace

    inputs = replace(photometry_fixture_inputs(), **input_changes)
    return {
        measurement.metric: measurement
        for measurement in rq.photometry_run_measurements(
            photometry_fixture_metrics(), photometry_fixture_gates(), inputs, _context()
        )
    }


def test_a_healthy_run_emits_every_declared_metric_once() -> None:
    """One measurement per declared metric, all measured, about the run."""
    measurements = rq.photometry_run_measurements(
        photometry_fixture_metrics(), photometry_fixture_gates(), photometry_fixture_inputs(), _context()
    )
    names = [measurement.metric for measurement in measurements]

    assert set(names) == _declared_names()
    assert len(names) == len(set(names)) == 27
    assert {m.subject.level for m in measurements} == {SubjectLevel.RUN}
    assert {m.origin for m in measurements} == {MeasurementOrigin.MEASURED}
    assert {m.run_id for m in measurements} == {"job-1"}
    assert {m.pipeline_version for m in measurements} == {"1.3.0"}
    assert all(m.measured_at.tzinfo is not None for m in measurements)


def test_every_gate_keeps_its_outcome_next_to_its_number() -> None:
    """A gate that passed is stored as passed, with its limit."""
    by_name = _healthy_by_name()
    rejection = by_name["photometry.gate_ensemble_frame_rejection"]

    assert rejection.check_outcome is GateStatus.PASSED
    assert rejection.value == pytest.approx(2 / 80)
    assert rejection.check_limit is not None
    assert rejection.check_limit.value == pytest.approx(0.25)
    assert by_name[PHOTOMETRY_STARS_PROCESSED].value == pytest.approx(120.0)
    assert by_name[PHOTOMETRY_LIGHT_CURVE_SCATTER_RMS_MEDIAN_MAG].value == pytest.approx(0.012)


def test_gates_with_no_number_are_not_measured_but_keep_the_outcome() -> None:
    """The work and content gates carry an outcome and no value."""
    by_name = _healthy_by_name()
    work = by_name[PHOTOMETRY_GATE_PHOTOMETRY_WORK]

    assert work.status is MeasurementStatus.NOT_MEASURED
    assert work.value is None
    assert work.check_outcome is GateStatus.PASSED
    assert work.reason


def test_the_comparison_gate_stores_the_set_size_even_when_the_scatter_test_fails() -> None:
    """The ratio is its own metric, so the gate's number keeps one meaning."""
    noisy = ComparisonSetResult(
        star_ids=tuple(f"Star_{index}" for index in range(12)),
        scatter_mag=0.0655,
        expected_error_mag=0.0036,
        candidate_count=13,
        uses_errors=True,
        frame_sizes=(12,) * 40,
    )
    gates = rq.photometry_run_gates(
        **{**_gate_inputs(), "comparison_sets": [noisy]},
    )
    inputs = rq.PhotometryRunInputs(comparison_sets=[noisy])
    by_name = {
        m.metric: m
        for m in rq.photometry_run_measurements(photometry_fixture_metrics(), gates, inputs, _context())
    }
    gate = by_name[PHOTOMETRY_GATE_COMPARISON_ENSEMBLE]

    assert gate.check_outcome is GateStatus.FAILED
    assert gate.value == pytest.approx(12.0)
    assert gate.check_limit is not None
    assert gate.check_limit.value == pytest.approx(5.0)
    assert by_name[PHOTOMETRY_COMPARISON_SCATTER_OVER_PREDICTED].value == pytest.approx(0.0655 / 0.0036)


def _gate_inputs() -> dict:
    """Give the keyword inputs of `photometry_run_gates` for a healthy run.

    Returns
    -------
    inputs : `dict`
        Inputs that pass every gate.
    """
    return {
        "frames_contributed": 80,
        "rejected_frame_count": 2,
        "frames_without_timestamp": 0,
        "session_count": 2,
        "session_empty_reasons": [],
        "sessions_missing_wcs": [],
        "no_work_reason": None,
        "comparison_sets": FIXTURE_COMPARISON_SETS,
        "registration_drifts_px": [3.0, 5.0, None],
        "centroid_shift_summaries": FIXTURE_CENTROID_SUMMARIES,
        "stars_with_scatter": 80,
        "median_flux_error_mag": 0.02,
    }


def test_the_registration_gate_stores_the_global_drift_when_the_corner_test_fails() -> None:
    """A rotated field fails the gate; the corner value has its own metric."""
    from astrometricslib.pipelines.photometry.test.post_processing.test_record_quality import (
        centroid_summary,
    )

    rotated = centroid_summary(corner_displacement_p95_px=1.9, fallback_count=40)
    gates = rq.photometry_run_gates(**{**_gate_inputs(), "centroid_shift_summaries": [rotated]})
    inputs = rq.PhotometryRunInputs(
        registration_drifts_px=[3.0, 5.0, None], centroid_shift_summaries=[rotated]
    )
    by_name = {
        m.metric: m
        for m in rq.photometry_run_measurements(photometry_fixture_metrics(), gates, inputs, _context())
    }
    gate = by_name[PHOTOMETRY_GATE_REGISTRATION_DRIFT]

    assert gate.check_outcome is GateStatus.FAILED
    assert gate.value == pytest.approx(5.0)
    assert gate.check_limit is not None
    assert gate.check_limit.unit == "px"
    assert by_name[PHOTOMETRY_CORNER_DISPLACEMENT_P95_PX].value == pytest.approx(1.9)
    assert by_name[PHOTOMETRY_CENTROID_FALLBACK_FRACTION].value == pytest.approx(0.1)


def test_a_missing_input_gives_a_not_measured_record_with_a_reason() -> None:
    """No sessions summary or comparison set is never stored as a default."""
    by_name = _healthy_by_name(centroid_shift_summaries=(), comparison_sets=())

    for name in (
        PHOTOMETRY_CORNER_DISPLACEMENT_P95_PX,
        PHOTOMETRY_CENTROID_FALLBACK_FRACTION,
        PHOTOMETRY_COMPARISON_SCATTER_OVER_PREDICTED,
    ):
        assert by_name[name].status is MeasurementStatus.NOT_MEASURED
        assert by_name[name].value is None
        assert by_name[name].reason


def test_an_assumed_gain_stores_the_error_as_an_assumed_value() -> None:
    """With unit gain assumed, the value is only a guide."""
    gates = rq.photometry_run_gates(
        **{**_gate_inputs(), "errors_assume_unit_gain": True},
    )
    inputs = rq.PhotometryRunInputs(median_flux_error_mag=0.02, errors_assume_unit_gain=True)
    by_name = {
        m.metric: m
        for m in rq.photometry_run_measurements(photometry_fixture_metrics(), gates, inputs, _context())
    }
    flux = by_name[PHOTOMETRY_GATE_FLUX_UNCERTAINTY]

    assert flux.check_outcome is GateStatus.NOT_CHECKED
    assert flux.status is MeasurementStatus.ASSUMED
    assert flux.value == pytest.approx(0.02)
    assert "unit gain" in flux.reason


def _use_temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualityStore:
    """Point the quality store at a temporary library.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The temporary library folder.
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the library path for the test.

    Returns
    -------
    store : `QualityStore`
        The store on the temporary library's database.
    """
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    return QualityStore(str(tmp_path / "astrometrics.db"))


def _run_validate_output(options: dict) -> object:
    """Run the real `validate_output` on a run with no work to do.

    Parameters
    ----------
    options : `dict`
        The request options.

    Returns
    -------
    summary : `PhotometryQualitySummary`
        The summary the run built.
    """
    request = PipelineRequest(target=Target(id="Test Target"), catalog_access=None, options=options)
    result = runner._empty_photometry_result("no frames for the requested filter")
    return runner.PhotometryPipelineAdapter().validate_output(request, result)


def test_the_runner_writes_one_row_per_declared_metric_and_a_repeat_writes_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate_output stores the run once; a second store adds nothing."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    summary = _run_validate_output({"job_id": "job-7", "measured_at": MEASURED_AT})
    rows = [row for row in store.query(target_id="Test Target", latest_only=False) if row.run_id == "job-7"]

    assert {row.metric for row in rows} == _declared_names()
    assert len(rows) == len(_declared_names())
    work = next(row for row in rows if row.metric == PHOTOMETRY_GATE_PHOTOMETRY_WORK)
    assert work.check_outcome is GateStatus.FAILED
    again = rq.record_photometry_quality(
        summary.photometry_metrics, list(summary.gates), rq.PhotometryRunInputs(), _context("job-7")
    )
    assert again == 0


def test_a_run_without_a_job_gets_a_generated_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no job id the rows still share one generated id."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    _run_validate_output({"measured_at": MEASURED_AT})
    rows = store.query(target_id="Test Target", latest_only=False)

    assert len(rows) == len(_declared_names())
    assert len({row.run_id for row in rows}) == 1
    assert rows[0].run_id.startswith("run-")
    assert rows[0].job_id is None


def test_a_run_without_a_measurement_time_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No measured_at means no guessed time and no rows."""
    store = _use_temporary_store(tmp_path, monkeypatch)
    _run_validate_output({})

    assert store.query(target_id="Test Target", latest_only=False) == []


def test_a_storage_failure_does_not_fail_the_run(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken store is logged and the summary is still returned."""
    _use_temporary_store(tmp_path, monkeypatch)
    mocker.patch(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.record_measurements",
        side_effect=StorageError("disk full"),
    )

    summary = _run_validate_output({"measured_at": MEASURED_AT})

    assert summary.gates
