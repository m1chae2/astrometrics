"""Tests for the stacking record step: what is stored for one stack.

They check the shape of the stored rows (subjects, gates, the missing-value
rules), that the registry accepts them, that a second record of the same run
adds nothing, that a missing coordinate solution gives not-measured noise, and
that the stage stores nothing when the caller gave no time.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualityMeasurement,
    SubjectLevel,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.metrics import (
    GATE_METRIC_BY_NAME,
    STACKING_EFFECTIVE_FRAME_COUNT,
    STACKING_EFFECTIVE_TIME_S,
    STACKING_EMPTY_APERTURE_NOISE_NORMALIZED,
    STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_FRAME_WEIGHT,
    STACKING_GATE_NEGATIVE_PIXELS,
    STACKING_GROUP_ALIGNMENT_SHIFT_PX,
    STACKING_NEGATIVE_PIXEL_MAX_FRACTION,
    STACKING_NOISE_REFERENCE_FLUX_STACK_UNIT,
)
from astrometricslib.pipelines.stacking.post_processing.record_quality import (
    measure_noise_safely,
    stacking_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    StackMember,
    StackMembership,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    NO_WCS_REASON,
    StackNoiseOutcome,
)
from astrometricslib.pipelines.stacking.test.conformance_fixture import (
    FIXTURE_TIME,
    fixture_membership,
    fixture_noise_outcome,
    fixture_summary,
)

STACK_NAME = "M99_Stacked.fits"


def _context() -> PipelineRunContext:
    """Build the run context of the tests.

    Returns
    -------
    context : `PipelineRunContext`
        A context for target M 99 with run id ``job-1``.
    """
    return PipelineRunContext("M 99", "stacking", "1.3.0", "job-1", "job-1", FIXTURE_TIME)


def _measurements(**overrides: object) -> list[QualityMeasurement]:
    """Build the fixture stack's measurements, with parts replaced.

    Parameters
    ----------
    **overrides
        ``noise`` or ``membership`` to replace the fixture's.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The record step's output.
    """
    return stacking_measurements(
        fixture_summary(),
        _context(),
        STACK_NAME,
        overrides.get("noise", fixture_noise_outcome()),
        overrides.get("membership", fixture_membership()),
    )


def _by_metric(measurements: list[QualityMeasurement], metric: str) -> list[QualityMeasurement]:
    """Pick the measurements of one metric.

    Parameters
    ----------
    measurements : `list` of `QualityMeasurement`
        Measurements.
    metric : `str`
        A metric name.

    Returns
    -------
    picked : `list` of `QualityMeasurement`
        The ones of that metric.
    """
    return [measurement for measurement in measurements if measurement.metric == metric]


def test_every_row_is_accepted_by_the_registry_and_a_second_record_adds_none(tmp_path: Path) -> None:
    """The store takes each row once; a repeat adds none."""
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    measurements = _measurements()
    first = record_measurements(store, measurements)
    assert first == len(measurements)
    assert record_measurements(store, _measurements()) == 0


def test_summary_numbers_and_the_stack_subject() -> None:
    """The stack's numbers sit on the stack subject."""
    rows = _by_metric(_measurements(), STACKING_NEGATIVE_PIXEL_MAX_FRACTION)
    assert len(rows) == 1
    assert rows[0].value == pytest.approx(0.12)
    assert rows[0].subject.level is SubjectLevel.STACK
    assert rows[0].subject.subject_id == f"M 99/{STACK_NAME}"
    assert rows[0].run_id == "job-1"
    assert rows[0].pipeline_version == "1.3.0"


def test_a_missing_number_is_not_measured_with_a_reason() -> None:
    """A summary field that is `None` is never stored as a default."""
    summary = fixture_summary()
    summary.stacking_metrics.flat_frame_count = None
    measurements = stacking_measurements(
        summary, _context(), STACK_NAME, fixture_noise_outcome(), fixture_membership()
    )
    row = _by_metric(measurements, "stacking.flat_frame_count")[0]
    assert row.status is MeasurementStatus.NOT_MEASURED
    assert row.value is None
    assert row.reason


def test_every_gate_is_stored_with_its_outcome_and_a_negative_pixel_limit_in_fractions() -> None:
    """Each of the 16 gates is stored; percent becomes a fraction."""
    measurements = _measurements()
    for metric in GATE_METRIC_BY_NAME.values():
        rows = _by_metric(measurements, metric)
        assert len(rows) == 1
        assert rows[0].check_outcome is GateStatus.PASSED
        assert rows[0].check_limit is not None
    negative = _by_metric(measurements, STACKING_GATE_NEGATIVE_PIXELS)[0]
    assert negative.value == pytest.approx(0.0001)
    assert negative.check_limit.value == pytest.approx(0.001)


def test_exposure_group_values_sit_on_each_groups_own_stack() -> None:
    """A group is named after its own stack file."""
    rows = _by_metric(_measurements(), STACKING_GROUP_ALIGNMENT_SHIFT_PX)
    by_subject = {row.subject.subject_id: row for row in rows}
    assert by_subject["M 99/M99_Stacked.fits"].status is MeasurementStatus.NOT_MEASURED
    assert by_subject["M 99/groups/M99_Stacked_exp120s.fits"].value == pytest.approx(5.0)


def test_noise_rows_carry_their_uncertainty_and_sample_count() -> None:
    """Noise, reference flux and ratio carry their errors."""
    measurements = _measurements()
    noise = _by_metric(measurements, STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT)[0]
    reference = _by_metric(measurements, STACKING_NOISE_REFERENCE_FLUX_STACK_UNIT)[0]
    ratio = _by_metric(measurements, STACKING_EMPTY_APERTURE_NOISE_NORMALIZED)[0]
    assert (noise.sample_count, reference.sample_count, ratio.sample_count) == (100, 10, 100)
    assert noise.uncertainty == pytest.approx(0.00014)
    assert ratio.value == pytest.approx(noise.value / reference.value)


def test_no_solution_gives_three_not_measured_noise_rows_with_the_reason() -> None:
    """Noise rows say why they are missing; the rest is stored."""
    outcome = StackNoiseOutcome(None, NO_WCS_REASON)
    measurements = _measurements(noise=outcome)
    for metric in (
        STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
        STACKING_NOISE_REFERENCE_FLUX_STACK_UNIT,
        STACKING_EMPTY_APERTURE_NOISE_NORMALIZED,
    ):
        row = _by_metric(measurements, metric)[0]
        assert row.status is MeasurementStatus.NOT_MEASURED
        assert row.reason == NO_WCS_REASON
    assert _by_metric(measurements, STACKING_NEGATIVE_PIXEL_MAX_FRACTION)[0].value is not None


def test_a_spectral_stack_gets_no_noise(tmp_path: Path) -> None:
    """A spectral stack gets no noise and nothing is read."""
    outcome = measure_noise_safely(str(tmp_path / "missing.fits"), None, True)
    assert outcome.result is None
    assert "spectral" in outcome.reason


def test_a_failure_while_measuring_noise_is_recorded_as_a_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unreadable stack file never raises out of the noise step."""
    with caplog.at_level(logging.ERROR):
        outcome = measure_noise_safely(str(tmp_path / "missing.fits"), None, False)
    assert outcome.result is None
    assert "failed" in outcome.reason


def test_one_weight_row_per_member_frame_and_the_weights_have_mean_one() -> None:
    """Membership rows match the member frames; the weights average to 1."""
    membership = fixture_membership()
    rows = _by_metric(_measurements(), STACKING_FRAME_WEIGHT)
    assert len(rows) == len(membership.members) == 2
    assert {row.subject.subject_id for row in rows} == {"M 99/Scope/Cam/a.fits", "M 99/Scope/Cam/b.fits"}
    assert all(row.subject.level is SubjectLevel.FRAME for row in rows)
    assert sum(row.value for row in rows) / len(rows) == pytest.approx(1.0)
    assert {row.subject.observing_night_id is not None for row in rows} == {True}
    count = _by_metric(_measurements(), STACKING_EFFECTIVE_FRAME_COUNT)[0]
    assert count.value == pytest.approx(9.0 / 5.0)
    assert count.sample_count == 2
    seconds = _by_metric(_measurements(), STACKING_EFFECTIVE_TIME_S)[0]
    assert seconds.value == pytest.approx(9.0 / (1 / 60 + 4 / 120))


def test_unknown_weights_still_record_membership() -> None:
    """Unknown weights still record each frame as a member."""
    frames = [member.frame for member in fixture_membership().members]
    membership = StackMembership(
        tuple(StackMember(frame, None) for frame in frames),
        weights_reason="Siril weighted the frames itself (-weight=noise) and does not report the weights",
    )
    measurements = _measurements(membership=membership)
    rows = _by_metric(measurements, STACKING_FRAME_WEIGHT)
    assert len(rows) == 2
    assert all(row.status is MeasurementStatus.NOT_MEASURED and "-weight=noise" in row.reason for row in rows)
    assert (
        _by_metric(measurements, STACKING_EFFECTIVE_FRAME_COUNT)[0].status is MeasurementStatus.NOT_MEASURED
    )


def test_an_uncertain_member_list_marks_the_rows_assumed() -> None:
    """An uncertain member list marks the rows assumed."""
    base = fixture_membership()
    membership = StackMembership(base.members, membership_reason="Siril stacked 1 of 2 candidate frames")
    rows = _by_metric(_measurements(membership=membership), STACKING_FRAME_WEIGHT)
    assert {row.status for row in rows} == {MeasurementStatus.ASSUMED}
    assert all("1 of 2" in row.reason for row in rows)


def test_no_membership_gives_not_measured_effective_numbers() -> None:
    """No membership gives no weight rows and a reason."""
    measurements = stacking_measurements(
        fixture_summary(),
        _context(),
        STACK_NAME,
        fixture_noise_outcome(),
        None,
        "the member frames could not be worked out",
    )
    assert _by_metric(measurements, STACKING_FRAME_WEIGHT) == []
    assert _by_metric(measurements, STACKING_EFFECTIVE_TIME_S)[0].status is MeasurementStatus.NOT_MEASURED


def test_the_stage_stores_nothing_without_a_measurement_time(caplog: pytest.LogCaptureFixture) -> None:
    """No measurement time means no rows and a warning."""
    with caplog.at_level(logging.WARNING):
        stage._record_stack_quality(
            fixture_summary(), "M 99", "/stacks/lights/M 99/a.fits", [], {}, None, (0, 0), None, None, None
        )
    assert "not stored" in caplog.text


def test_the_stage_stores_the_rows_of_a_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a time, the stage hook records the rows."""
    stored: list[QualityMeasurement] = []
    monkeypatch.setattr(
        "astrometricslib.pipelines.stacking.post_processing.record_quality.record_run_measurements",
        lambda measurements, config=None: stored.extend(measurements) or len(measurements),
    )
    stage._record_stack_quality(
        fixture_summary(),
        "M 99",
        str(tmp_path / STACK_NAME),
        [member.frame for member in fixture_membership().members],
        {},
        None,
        (0, 0),
        "job-9",
        datetime(2026, 10, 22, tzinfo=UTC),
        None,
    )
    assert stored
    assert {row.run_id for row in stored} == {"job-9"}
    # The stack file is missing, so the noise is recorded as not measured.
    assert (
        _by_metric(stored, STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT)[0].status
        is MeasurementStatus.NOT_MEASURED
    )
