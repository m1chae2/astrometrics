"""Tests for the helpers that let a pipeline emit quality measurements."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import StorageError
from astrometricslib.models.quality.gate_result import GateStatus, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import MeasurementStatus, SubjectLevel
from astrometricslib.pipelines.shared.quality.metrics import FRAME_DETECTED_REGION_COUNT, FRAME_METRICS
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
    gate_measurement,
    make_run_id,
    record_run_measurements,
)

MEASURED_AT = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)
# A count metric that allows the frame level; the helpers do not care which
# metric they build, only that it is a declared spec.
SPEC = next(spec for spec in FRAME_METRICS if spec.name == FRAME_DETECTED_REGION_COUNT)


def _context() -> PipelineRunContext:
    """Build a context for a made-up pipeline.

    Returns
    -------
    context : `PipelineRunContext`
        The context.
    """
    return PipelineRunContext("T 1", "demo", "1.0.0", "job-1", "job-1", MEASURED_AT)


def test_make_run_id_uses_the_job_id_or_makes_a_sortable_unique_one() -> None:
    """A job id is kept; with none, a unique generated id is made."""
    assert make_run_id("job-9") == "job-9"
    first = make_run_id(None)
    second = make_run_id(None)
    assert first.startswith("run-")
    assert first != second
    assert make_run_id("").startswith("run-")


def test_subjects_have_the_documented_ids() -> None:
    """Run and stack subjects join their parts with slashes."""
    context = _context()
    run = context.run_subject()
    assert (run.level, run.subject_id, run.target_id) == (SubjectLevel.RUN, "T 1/demo/job-1", "T 1")
    stack = context.stack_subject("T 1/stack_L.fits")
    assert (stack.level, stack.subject_id) == (SubjectLevel.STACK, "T 1/T 1/stack_L.fits")


def test_from_options_needs_a_time_and_reads_the_job_id() -> None:
    """Without measured_at there is no context; the job id is the run id."""
    assert PipelineRunContext.from_options("T 1", "demo", "1.0.0", {"job_id": "j"}) is None
    context = PipelineRunContext.from_options(
        "T 1", "demo", "1.0.0", {"job_id": "j", "measured_at": MEASURED_AT}
    )
    assert context is not None
    assert (context.run_id, context.job_id) == ("j", "j")


def test_build_measurement_carries_the_spec_and_context_and_never_defaults() -> None:
    """Spec and context fill the fields; None becomes not_measured."""
    context = _context()
    measured = build_measurement(SPEC, 3.0, context, context.run_subject())
    assert measured.metric == SPEC.name
    assert measured.definition_version == SPEC.definition_version
    assert (measured.pipeline_version, measured.run_id, measured.job_id) == ("1.0.0", "job-1", "job-1")
    assert measured.measured_at == MEASURED_AT
    missing = build_measurement(SPEC, None, context, context.run_subject(), reason="no data")
    assert missing.status is MeasurementStatus.NOT_MEASURED
    assert missing.value is None
    with pytest.raises(ValueError, match="reason"):
        build_measurement(SPEC, None, context, context.run_subject())


def test_gate_measurement_keeps_value_outcome_limit_and_reason() -> None:
    """A passed gate gives value, outcome, limit in the unit and detail."""
    context = _context()
    gate = passed_gate("g", 2.0, 5.0, "a setting", "fine")
    measurement = gate_measurement(
        gate, SPEC, context, context.run_subject(), limit_tier=LimitTier.PHYSICAL_BUDGET
    )
    assert measurement.value == pytest.approx(2.0)
    assert measurement.check_outcome is GateStatus.PASSED
    assert measurement.check_limit is not None
    assert (measurement.check_limit.value, measurement.check_limit.unit) == (5.0, SPEC.unit)
    assert measurement.check_limit.derivation == "a setting"
    assert measurement.reason == "fine"


def test_gate_measurement_without_a_value_or_limit_still_carries_the_outcome() -> None:
    """Unchecked has no value; a limitless failure has an empty limit."""
    context = _context()
    unchecked = gate_measurement(
        unchecked_gate("g", "could not look"),
        SPEC,
        context,
        context.run_subject(),
        limit_tier=LimitTier.GEOMETRY,
    )
    assert unchecked.status is MeasurementStatus.NOT_MEASURED
    assert unchecked.check_outcome is GateStatus.NOT_CHECKED
    assert unchecked.reason == "could not look"
    assert unchecked.check_limit is None
    no_limit = gate_measurement(
        failed_gate("g", "bad", 1.0), SPEC, context, context.run_subject(), limit_tier=LimitTier.GEOMETRY
    )
    assert no_limit.check_outcome is GateStatus.FAILED
    assert no_limit.check_limit is not None
    assert no_limit.check_limit.value is None


def test_record_run_measurements_writes_once_and_swallows_storage_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """New rows are counted, a repeat adds none, a StorageError is kept in."""
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    context = _context()
    subject = context.run_subject()
    # A frame-level metric on a run subject is refused; use the frame subject.
    frame_subject = subject.model_copy(update={"level": SubjectLevel.FRAME})
    measurements = [build_measurement(SPEC, 3.0, context, frame_subject)]

    assert record_run_measurements(measurements) == 1
    assert record_run_measurements(measurements) == 0
    assert len(QualityStore(str(tmp_path / "astrometrics.db")).query(latest_only=False)) == 1
    assert record_run_measurements([]) == 0

    mocker.patch(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.record_measurements",
        side_effect=StorageError("disk full"),
    )
    assert record_run_measurements(measurements) == 0


def test_a_refused_measurement_is_logged_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registry violation stores nothing and does not stop the run."""
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    context = _context()
    wrong_level = [build_measurement(SPEC, 3.0, context, context.run_subject())]
    assert record_run_measurements(wrong_level) == 0
