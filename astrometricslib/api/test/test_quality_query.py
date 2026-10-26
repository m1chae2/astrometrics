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
from astrometricslib.drivers.quality_store import QualityStore, quality_database_path
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.calibration_identities import CalibrationIdentities
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


def test_capture_settings_read_by_id(quality: QualityRecords, tmp_path: Path) -> None:
    """The capture settings come back by id; asking first creates no file."""
    assert quality.capture_settings() == {}
    assert list(tmp_path.iterdir()) == []

    store = QualityStore(str(tmp_path / "astrometrics.db"))
    first = store.get_or_create_capture_setting(CaptureSetting(camera="ASI533MM", binning=1))
    second = store.get_or_create_capture_setting(CaptureSetting(camera="D5300"))

    assert set(quality.capture_settings()) == {first, second}
    only_first = quality.capture_settings([first, 9999])
    assert list(only_first) == [first]
    assert only_first[first].camera == "ASI533MM"


def test_calibration_identities_read_by_run_and_target(quality: QualityRecords, tmp_path: Path) -> None:
    """The reader returns records by run id and target; no file is created."""
    assert quality.calibration_identities() == []
    assert list(tmp_path.iterdir()) == []

    store = QualityStore(str(tmp_path / "astrometrics.db"))
    for run_id, target_id in (("run-1", "M31"), ("run-2", "M13")):
        store.write_calibration_identities(
            CalibrationIdentities(run_id=run_id, subject_id=f"{target_id}/stack.fits", target_id=target_id)
        )

    assert [record.run_id for record in quality.calibration_identities(run_ids=["run-2"])] == ["run-2"]
    assert [record.target_id for record in quality.calibration_identities(target_id="M31")] == ["M31"]


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


def test_variant_and_library_only_filters(quality: QualityRecords) -> None:
    """``variant`` picks one subset; ``library_only`` leaves subsets out."""
    quality.query()  # The store file may not exist yet; asking must be harmless.
    QualityStore(quality_database_path(quality._config)).write_measurements([
        _measurement("run-1", BASE_TIME),
        _measurement(
            "run-1",
            BASE_TIME,
            subject=QualitySubject(
                level=SubjectLevel.FRAME,
                subject_id="M31/_subsets/half/a.fits",
                target_id="M31",
                variant="half",
            ),
        ),
    ])

    assert [m.subject.variant for m in quality.query(variant="half")] == ["half"]
    assert [m.subject.variant for m in quality.query(library_only=True)] == [None]


def test_variant_with_library_only_is_refused(quality: QualityRecords) -> None:
    """Asking for one subset and for none at once raises an error."""
    with pytest.raises(InvalidArgumentError):
        quality.query(variant="half", library_only=True)


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


def test_query_fills_each_row_with_its_metric_unit(quality: QualityRecords, tmp_path: Path) -> None:
    """A row read back carries the unit of its metric."""
    QualityStore(quality_database_path(quality._config)).write_measurements([_measurement("run", BASE_TIME)])

    [row] = quality.query()

    assert row.unit == "arcsec"


def test_resolve_rows_gives_frame_and_stack_paths_and_reasons(tmp_path: Path) -> None:
    """Each found row gets a path or a reason; missing ids are listed apart."""
    frames_folder = tmp_path / "lights" / "M31" / "scope" / "cam"
    frames_folder.mkdir(parents=True)
    present = frames_folder / "a.fits"
    present.write_text("x")
    stack_folder = tmp_path / "lights" / "M31"
    (stack_folder / "M31_stack.fits").write_text("x")
    target = SimpleNamespace(
        id="M31",
        frames=[
            SimpleNamespace(path=str(present)),
            SimpleNamespace(path=str(frames_folder / "gone.fits")),
        ],
    )
    configuration = SimpleNamespace(get_library_path=lambda: tmp_path, get_stacks_path=lambda: tmp_path)
    records = QualityRecords(configuration, None, targets=SimpleNamespace(get=lambda target_id: target))
    subjects = {
        "frame": QualitySubject(level=SubjectLevel.FRAME, subject_id="M31/scope/cam/a.fits", target_id="M31"),
        "gone": QualitySubject(
            level=SubjectLevel.FRAME, subject_id="M31/scope/cam/gone.fits", target_id="M31"
        ),
        "unlisted": QualitySubject(
            level=SubjectLevel.FRAME, subject_id="M31/scope/cam/z.fits", target_id="M31"
        ),
        "stack": QualitySubject(level=SubjectLevel.STACK, subject_id="M31/M31_stack.fits", target_id="M31"),
        "run": QualitySubject(level=SubjectLevel.RUN, subject_id="run-1", target_id="M31"),
    }
    store = QualityStore(quality_database_path(configuration))
    store.write_measurements([
        _measurement(f"run-{name}", BASE_TIME, subject=subject) for name, subject in subjects.items()
    ])
    ids = {row.subject.subject_id: row.row_id for row in records.query(latest_only=False)}

    result = records.resolve_rows([
        ids["run-1"],
        9999,
        ids["M31/scope/cam/a.fits"],
        ids["M31/scope/cam/gone.fits"],
        ids["M31/scope/cam/z.fits"],
        ids["M31/M31_stack.fits"],
    ])

    assert result.not_found_row_ids == [9999]
    by_subject = {row.subject_id: row for row in result.rows}
    assert by_subject["M31/scope/cam/a.fits"].path == str(present)
    assert by_subject["M31/M31_stack.fits"].path == str(stack_folder / "M31_stack.fits")
    assert "no longer on disk" in by_subject["M31/scope/cam/gone.fits"].reason
    assert "not in the target's frame list" in by_subject["M31/scope/cam/z.fits"].reason
    assert by_subject["run-1"].path is None
    assert next(row.row_id for row in result.rows) == ids["run-1"]


def test_the_aperture_set_identity_row_is_read_back_through_the_public_query(
    quality: QualityRecords, tmp_path: Path
) -> None:
    """A stored identity row is read back and turned into its text."""
    from astrometricslib import aperture_identity_from_number, aperture_identity_to_number

    store = QualityStore(str(tmp_path / "astrometrics.db"))
    subject = QualitySubject(level=SubjectLevel.STACK, subject_id="M13/M13_Stacked.fits", target_id="M13")
    store.write_measurements([
        _measurement(
            "run-1",
            BASE_TIME,
            metric="stacking.aperture_set_number",
            subject=subject,
            value=aperture_identity_to_number("v3-156104df543d"),
        )
    ])
    rows = quality.query(metric="stacking.aperture_set_number", target_id="M13", library_only=True)
    assert [aperture_identity_from_number(row.value) for row in rows] == ["v3-156104df543d"]
