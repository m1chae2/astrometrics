"""Tests for the stored-measurement reads on the quality root.

`QualityRecords.query` reads what earlier runs stored, and `metrics`
lists the declared metrics. The tests use a temporary library folder, so no
real database is touched.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib import Astrometrics
from astrometricslib.api.quality import QualityRecords
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.metric_spec import MetricSpec, MetricSummary

BASE_TIME = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)


def _measurement(run_id: str, measured_at: datetime, **overrides: object) -> QualityMeasurement:
    """Build a measured frame measurement.

    Returns
    -------
    measurement : `QualityMeasurement`
        A valid measurement, with any field replaced.
    """
    fields: dict = {
        "metric": "frame.star_width_moffat_arcsec",
        "definition_version": 1,
        "subject": QualitySubject(level=SubjectLevel.FRAME, subject_id="M31/a.fits", target_id="M31"),
        "status": MeasurementStatus.MEASURED,
        "value": 2.5,
        "origin": MeasurementOrigin.MEASURED,
        "run_id": run_id,
        "measured_at": measured_at,
    }
    fields.update(overrides)
    return QualityMeasurement(**fields)


@pytest.fixture
def quality(tmp_path: Path) -> QualityRecords:
    """Make the quality API on a temporary library folder.

    Returns
    -------
    quality : `QualityRecords`
        An instance whose library path is empty.
    """
    configuration = SimpleNamespace(get_library_path=lambda: tmp_path)
    return QualityRecords(configuration, None)


def test_a_library_with_no_database_gives_an_empty_list_and_creates_nothing(
    quality: QualityRecords, tmp_path: Path
) -> None:
    """Asking before anything is stored creates no file."""
    assert quality.query() == []
    assert list(tmp_path.iterdir()) == []


def test_a_database_without_the_quality_table_is_left_alone(quality: QualityRecords, tmp_path: Path) -> None:
    """A database that lacks the quality table gets none from a read."""
    database = tmp_path / "astrometrics.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE other (x INTEGER)")
    connection.close()

    assert quality.query() == []

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    connection.close()
    assert tables == {"other"}


def test_stored_measurements_come_back_filtered(quality: QualityRecords, tmp_path: Path) -> None:
    """The filters narrow the rows, and the newest row wins by default."""
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    setting_id = store.get_or_create_capture_setting(CaptureSetting(camera="ASI533MM"))
    store.write_measurements([
        _measurement("run-1", BASE_TIME),
        _measurement("run-2", BASE_TIME + timedelta(hours=1), value=3.0),
        _measurement(
            "run-1",
            BASE_TIME,
            subject=QualitySubject(
                level=SubjectLevel.STACK, subject_id="stack-1", target_id="M13", capture_setting_id=setting_id
            ),
        ),
    ])

    latest = quality.query(level="frame")
    assert [measurement.value for measurement in latest] == [3.0]
    assert len(quality.query(level="frame", latest_only=False)) == 2
    assert [m.subject.target_id for m in quality.query(camera="ASI533MM")] == ["M13"]
    assert quality.query(target_id="nobody") == []
    assert len(quality.query(since="2026-10-01T03:30:00")) == 1
    assert len(quality.query(since=BASE_TIME + timedelta(hours=2))) == 0
    cited = quality.query(row_ids=[1])
    assert [measurement.row_id for measurement in cited] == [1]


def test_bad_arguments_are_refused(quality: QualityRecords) -> None:
    """An unknown level or an unreadable time raises `InvalidArgumentError`."""
    with pytest.raises(InvalidArgumentError):
        quality.query(level="galaxy")
    with pytest.raises(InvalidArgumentError):
        quality.query(since="last night")


def test_an_unregistered_metric_name_is_refused(quality: QualityRecords) -> None:
    """`query(metric=...)` lists the registered names when it is wrong."""
    with pytest.raises(InvalidArgumentError, match=r"frame\.background_level_adu"):
        quality.query(metric="frame.no_such_metric")
    assert quality.query(metric="frame.background_level_adu") == []


def test_metrics_with_a_name_gives_that_one_spec(quality: QualityRecords) -> None:
    """`metrics(name=...)` gives one declaration or refuses an unknown name."""
    one = quality.metrics(name="frame.background_level_adu")
    assert [spec.name for spec in one] == ["frame.background_level_adu"]
    with pytest.raises(InvalidArgumentError):
        quality.metrics(name="frame.no_such_metric")


def test_metrics_lists_the_declarations(quality: QualityRecords) -> None:
    """`metrics` gives summaries by default and full specs on request."""
    summaries = quality.metrics()
    assert summaries
    assert all(isinstance(summary, MetricSummary) for summary in summaries)
    assert len({summary.name for summary in summaries}) == len(summaries)
    specs = quality.metrics(detail="full")
    assert all(isinstance(spec, MetricSpec) for spec in specs)
    assert [spec.name for spec in specs] == [summary.name for summary in summaries]


def test_metrics_filters_by_checkpoint_and_name(quality: QualityRecords) -> None:
    """A checkpoint keeps only its metrics; bad choices raise."""
    frame_metrics = quality.metrics(checkpoint="frame", detail="full")
    assert frame_metrics
    assert all(spec.checkpoint.value == "frame" for spec in frame_metrics)
    assert len(frame_metrics) < len(quality.metrics())
    one = quality.metrics(name=frame_metrics[0].name)
    assert [summary.name for summary in one] == [frame_metrics[0].name]
    for bad in ({"checkpoint": "nowhere"}, {"detail": "everything"}, {"name": "not.a.metric"}):
        with pytest.raises(InvalidArgumentError):
            quality.metrics(**bad)


def test_the_root_object_holds_the_quality_child() -> None:
    """`Astrometrics.quality` shares the target catalog; no old alias."""
    from unittest.mock import MagicMock

    from astrometricslib import AppConfiguration

    astrometrics = Astrometrics(AppConfiguration(), MagicMock())
    assert isinstance(astrometrics.quality, QualityRecords)
    assert astrometrics.quality._targets is astrometrics.targets
    assert not hasattr(astrometrics.processing, "diagnostics")
