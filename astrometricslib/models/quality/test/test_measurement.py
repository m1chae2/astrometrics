"""Tests for the measurement, limit and metric-spec models.

They check each validator and that every model survives a JSON round trip.
"""

import datetime

import pytest
from pydantic import ValidationError

from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementReference,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    StarResultReference,
    SubjectLevel,
)
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)


def _subject() -> QualitySubject:
    """Build a frame subject.

    Returns
    -------
    subject : `QualitySubject`
        A subject with every field filled.
    """
    return QualitySubject(
        level=SubjectLevel.FRAME,
        subject_id="M 57/light_001.fits",
        target_id="M 57",
        observing_night_id="2026-10-02",
        capture_setting_id=3,
        channel=None,
    )


def _measurement(**overrides: object) -> QualityMeasurement:
    """Build a valid measurement, with optional field overrides.

    Parameters
    ----------
    **overrides
        Fields to replace.

    Returns
    -------
    measurement : `QualityMeasurement`
        The measurement.
    """
    fields = {
        "metric": "frame.star_width_moffat_arcsec",
        "definition_version": 1,
        "subject": _subject(),
        "status": MeasurementStatus.MEASURED,
        "value": 2.5,
        "uncertainty": 0.1,
        "sample_count": 120,
        "origin": MeasurementOrigin.MEASURED,
        "run_id": "run-1",
        "measured_at": datetime.datetime(2026, 10, 2, 3, 0, 0),
    }
    fields.update(overrides)
    return QualityMeasurement(**fields)


def _limit(**overrides: object) -> QualityLimit:
    """Build a valid limit, with optional field overrides.

    Parameters
    ----------
    **overrides
        Fields to replace.

    Returns
    -------
    limit : `QualityLimit`
        The limit.
    """
    fields = {
        "value": 3.0,
        "unit": "arcsec",
        "tier": LimitTier.GEOMETRY,
        "derivation": "Twice the pixel scale.",
        "sample_count": None,
    }
    fields.update(overrides)
    return QualityLimit(**fields)


def _spec(**overrides: object) -> MetricSpec:
    """Build a valid decision metric spec, with optional field overrides.

    Parameters
    ----------
    **overrides
        Fields to replace.

    Returns
    -------
    spec : `MetricSpec`
        The spec.
    """
    fields = {
        "name": "frame.star_width_moffat_arcsec",
        "definition_version": 1,
        "unit": "arcsec",
        "checkpoint": Checkpoint.FRAME,
        "levels": frozenset({SubjectLevel.FRAME}),
        "better": Direction.LOWER,
        "meaning": "Median star width over the frame.",
        "decisions": ("focus",),
        "estimator": "Moffat fit",
        "sample_definition": "Up to 200 stars",
        "band": "Frame filter",
        "computed_by": "pipelines.shared.quality.frame_measurement.measure_star_width",
        "uncertainty_kind": UncertaintyKind.STATISTICAL_1_SIGMA,
        "uncertainty_method": "Bootstrap of the median",
        "register_entry": None,
        "purpose": MetricPurpose.DECISION,
        "purpose_reason": "Feeds the focus warning.",
    }
    fields.update(overrides)
    return MetricSpec(**fields)


def test_measured_measurement_round_trips_through_json() -> None:
    """A full measurement survives a JSON round trip unchanged."""
    measurement = _measurement(
        check_outcome=GateStatus.PASSED,
        check_limit=_limit(),
        action_taken="none",
        row_id=7,
    )
    assert QualityMeasurement.model_validate_json(measurement.model_dump_json()) == measurement


def test_row_id_defaults_to_none() -> None:
    """A freshly built measurement has no row id."""
    assert _measurement().row_id is None


def test_not_measured_requires_reason_and_no_value() -> None:
    """A not_measured record needs a reason and must not carry a value."""
    ok = _measurement(status=MeasurementStatus.NOT_MEASURED, value=None, reason="Too few stars.")
    assert ok.value is None
    with pytest.raises(ValidationError):
        _measurement(status=MeasurementStatus.NOT_MEASURED, value=None, reason="")
    with pytest.raises(ValidationError):
        _measurement(status=MeasurementStatus.NOT_MEASURED, value=None, reason="   ")
    with pytest.raises(ValidationError):
        _measurement(status=MeasurementStatus.NOT_MEASURED, value=1.0, reason="Too few stars.")


@pytest.mark.parametrize("status", [MeasurementStatus.MEASURED, MeasurementStatus.ASSUMED])
def test_measured_and_assumed_require_a_value(status: MeasurementStatus) -> None:
    """Measured and assumed records must carry a value."""
    with pytest.raises(ValidationError):
        _measurement(status=status, value=None)


def test_subject_round_trips_and_has_no_night_or_target_level() -> None:
    """The subject round-trips and offers only frame, stack and run levels."""
    subject = _subject()
    assert QualitySubject.model_validate_json(subject.model_dump_json()) == subject
    assert {level.value for level in SubjectLevel} == {"frame", "stack", "run"}


def test_measurement_reference_requires_exactly_one_target() -> None:
    """A reference names a row id or a star result, never both or neither."""
    star = StarResultReference(star_id="S1", run_id="run-1", metric="photometry.flux_error")
    assert MeasurementReference(row_id=4).row_id == 4
    assert MeasurementReference(star_result=star).star_result == star
    with pytest.raises(ValidationError):
        MeasurementReference()
    with pytest.raises(ValidationError):
        MeasurementReference(row_id=4, star_result=star)


def test_measurement_references_round_trip() -> None:
    """Both reference kinds survive a JSON round trip."""
    star = StarResultReference(star_id="S1", run_id="run-1", metric="photometry.flux_error")
    for reference in (MeasurementReference(row_id=4), MeasurementReference(star_result=star)):
        assert MeasurementReference.model_validate_json(reference.model_dump_json()) == reference


def test_validated_dataset_limit_requires_register_entry() -> None:
    """Only a validated_dataset limit must name its register entry."""
    with pytest.raises(ValidationError):
        _limit(tier=LimitTier.VALIDATED_DATASET)
    assert _limit(tier=LimitTier.VALIDATED_DATASET, register_entry="STK-07").register_entry == "STK-07"
    assert _limit(tier=LimitTier.OWN_BASELINE).register_entry is None


def test_limit_round_trips_through_json() -> None:
    """A limit survives a JSON round trip unchanged."""
    limit = _limit(tier=LimitTier.VALIDATED_DATASET, register_entry="STK-07", sample_count=12)
    assert QualityLimit.model_validate_json(limit.model_dump_json()) == limit


def test_metric_spec_is_frozen() -> None:
    """A registered metric cannot be changed after it is built."""
    spec = _spec()
    with pytest.raises(ValidationError):
        spec.unit = "pixel"


def test_decision_and_context_metrics_require_a_reason() -> None:
    """Decision and context metrics must state why they exist."""
    for purpose in (MetricPurpose.DECISION, MetricPurpose.CONTEXT):
        assert _spec(purpose=purpose, purpose_reason="Shown as evidence.").purpose == purpose
        with pytest.raises(ValidationError):
            _spec(purpose=purpose, purpose_reason="")


def test_exploratory_metric_requires_question_and_review_date() -> None:
    """An exploratory metric needs a question and a review date."""
    ok = _spec(
        purpose=MetricPurpose.EXPLORATORY,
        purpose_reason="",
        exploratory_question="Does width track temperature?",
        review_date=datetime.date(2027, 1, 1),
    )
    assert ok.review_date == datetime.date(2027, 1, 1)
    with pytest.raises(ValidationError):
        _spec(purpose=MetricPurpose.EXPLORATORY, review_date=datetime.date(2027, 1, 1))
    with pytest.raises(ValidationError):
        _spec(purpose=MetricPurpose.EXPLORATORY, exploratory_question="Why?")


def test_metric_spec_round_trips_through_json() -> None:
    """A spec, with its frozenset of levels, survives a JSON round trip."""
    spec = _spec(register_entry="FRM-01")
    assert MetricSpec.model_validate_json(spec.model_dump_json()) == spec


def test_measurement_dumps_camel_case_and_round_trips_by_alias() -> None:
    """A measurement dumps camelCase keys and validates from them again."""
    measurement = _measurement(check_outcome=GateStatus.FAILED, check_limit=_limit())
    dumped = measurement.model_dump(by_alias=True, mode="json")
    assert {
        "definitionVersion",
        "sampleCount",
        "checkOutcome",
        "checkLimit",
        "actionTaken",
        "runId",
        "jobId",
    } <= set(dumped)
    assert {"subjectId", "targetId", "observingNightId", "captureSettingId"} <= set(dumped["subject"])
    assert {"sampleCount"} <= set(dumped["checkLimit"])
    assert QualityMeasurement.model_validate(dumped) == measurement


def test_references_dump_camel_case_and_round_trip_by_alias() -> None:
    """References dump camelCase keys, and both name styles construct them."""
    star = StarResultReference(starId="S1", runId="run-1", metric="m")
    assert star == StarResultReference(star_id="S1", run_id="run-1", metric="m")
    reference = MeasurementReference(starResult=star)
    dumped = reference.model_dump(by_alias=True, mode="json")
    assert set(dumped) == {"rowId", "starResult"}
    assert set(dumped["starResult"]) == {"starId", "runId", "metric"}
    assert MeasurementReference.model_validate(dumped) == reference
    assert MeasurementReference(rowId=3).row_id == 3


def test_limit_dumps_camel_case_and_round_trips_by_alias() -> None:
    """A limit dumps camelCase keys and validates from them again."""
    limit = _limit(tier=LimitTier.VALIDATED_DATASET, register_entry="STK-07", sample_count=5)
    dumped = limit.model_dump(by_alias=True, mode="json")
    assert {"sampleCount", "registerEntry"} <= set(dumped)
    assert QualityLimit.model_validate(dumped) == limit
    assert QualityLimit(value=1.0, unit="u", tier="geometry", derivation="d", sampleCount=2).sample_count == 2


def test_metric_spec_dumps_camel_case_and_round_trips_by_alias() -> None:
    """A spec dumps camelCase keys, validates from them, and stays frozen."""
    spec = _spec(register_entry="FRM-01")
    dumped = spec.model_dump(by_alias=True, mode="json")
    expected = {
        "definitionVersion",
        "sampleDefinition",
        "computedBy",
        "uncertaintyKind",
        "uncertaintyMethod",
        "registerEntry",
        "purposeReason",
        "exploratoryQuestion",
        "reviewDate",
    }
    assert expected <= set(dumped)
    assert MetricSpec.model_validate(dumped) == spec
    with pytest.raises(ValidationError):
        spec.unit = "pixel"
