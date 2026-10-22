"""Tests for the write gate that checks measurements against the registry."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_BACKGROUND_LEVEL,
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
)
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements


def _measurement(**overrides: object) -> QualityMeasurement:
    """Build a valid background-level measurement, with fields replaced.

    Returns
    -------
    measurement : `QualityMeasurement`
        A frame-level measurement of the registered background metric.
    """
    fields: dict = {
        "metric": FRAME_BACKGROUND_LEVEL,
        "definition_version": 1,
        "subject": QualitySubject(level=SubjectLevel.FRAME, subject_id="M31/a.fits"),
        "status": MeasurementStatus.MEASURED,
        "value": 1200.0,
        "origin": MeasurementOrigin.MEASURED,
        "run_id": "run-1",
        "measured_at": datetime(2026, 10, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return QualityMeasurement(**fields)


@pytest.fixture
def store(tmp_path: Path) -> QualityStore:
    """Provide a store on a temporary database.

    Returns
    -------
    store : `QualityStore`
        A fresh store.
    """
    return QualityStore(str(tmp_path / "astrometrics.db"))


def test_a_valid_measurement_is_stored(store: QualityStore) -> None:
    """A measurement that follows its declaration reaches the store."""
    assert record_measurements(store, [_measurement()]) == 1
    assert len(store.query()) == 1


def test_an_unregistered_metric_is_refused(store: QualityStore) -> None:
    """An unknown metric name raises and stores nothing."""
    with pytest.raises(InvalidArgumentError, match=r"frame\.nope"):
        record_measurements(store, [_measurement(), _measurement(metric="frame.nope", run_id="run-2")])
    assert store.query() == []


def test_a_wrong_definition_version_is_refused(store: QualityStore) -> None:
    """A version other than the registered one raises, naming the metric."""
    with pytest.raises(InvalidArgumentError, match=FRAME_BACKGROUND_LEVEL):
        record_measurements(store, [_measurement(definition_version=2)])


def test_a_level_the_metric_does_not_allow_is_refused(store: QualityStore) -> None:
    """A stack-level measurement of a frame-only metric raises."""
    subject = QualitySubject(level=SubjectLevel.STACK, subject_id="stack-1")
    with pytest.raises(InvalidArgumentError, match="frame"):
        record_measurements(store, [_measurement(subject=subject)])


def test_a_check_limit_in_another_unit_is_refused(store: QualityStore) -> None:
    """A limit in arcsec on an adu metric raises."""
    limit = QualityLimit(value=3.0, unit="arcsec", tier=LimitTier.GEOMETRY, derivation="test")
    with pytest.raises(InvalidArgumentError, match="unit"):
        record_measurements(store, [_measurement(check_limit=limit)])


def test_an_uncertainty_on_a_metric_without_one_is_refused(store: QualityStore) -> None:
    """A metric that declares no uncertainty refuses one."""
    with pytest.raises(InvalidArgumentError, match="uncertainty"):
        record_measurements(
            store, [_measurement(metric=FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX, uncertainty=0.1)]
        )
