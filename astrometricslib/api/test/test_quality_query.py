"""Tests for the stored-measurement reads, ``astrometrics.quality.records``.

`QualityMeasurements.query` and `get` read what earlier runs stored,
`get_paths` finds the picture behind a row, and `get_metrics` and
`get_metric` read the declared metrics. The tests use a temporary library
folder, so no real database is touched.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
from astrometricslib._internal.testing import astrometrics_with_drivers
from astrometricslib.api.quality import QualityMeasurements, QualityRecords
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.quality.calibration_identities import CalibrationIdentities
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.models.target import Target

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
def quality(tmp_path: Path) -> QualityMeasurements:
    """Make the stored-measurement reads on a temporary library folder.

    Returns
    -------
    quality : `QualityMeasurements`
        An instance whose library path is empty.
    """
    configuration = SimpleNamespace(get_library_path=lambda: tmp_path)
    return QualityMeasurements(configuration)


def test_a_library_with_no_database_gives_an_empty_list_and_creates_nothing(
    quality: QualityMeasurements, tmp_path: Path
) -> None:
    """Asking before anything is stored creates no file."""
    assert list(quality.query()) == []
    assert list(tmp_path.iterdir()) == []


def test_a_database_without_the_quality_table_is_left_alone(
    quality: QualityMeasurements, tmp_path: Path
) -> None:
    """A database that lacks the quality table gets none from a read."""
    database = tmp_path / "astrometrics.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE other (x INTEGER)")
    connection.close()

    assert list(quality.query()) == []

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    connection.close()
    assert tables == {"other"}


def test_capture_settings_read_by_id(quality: QualityMeasurements, tmp_path: Path) -> None:
    """The capture settings come back by id; asking first creates no file."""
    assert quality.get_capture_settings() == []
    assert list(tmp_path.iterdir()) == []

    store = QualityStore(str(tmp_path / "astrometrics.db"))
    first = store.get_or_create_capture_setting(CaptureSetting(camera="ASI533MM", binning=1))
    second = store.get_or_create_capture_setting(CaptureSetting(camera="D5300"))

    assert [stored.id for stored in quality.get_capture_settings()] == sorted([first, second])
    only_first = quality.get_capture_settings([first, 9999])
    assert [stored.id for stored in only_first] == [first]
    assert only_first[0].setting.camera == "ASI533MM"


def test_calibration_identities_read_by_run_and_target(quality: QualityMeasurements, tmp_path: Path) -> None:
    """The reader returns records by run id and target; no file is created."""
    assert quality.get_calibration_identities() == []
    assert list(tmp_path.iterdir()) == []

    store = QualityStore(str(tmp_path / "astrometrics.db"))
    for run_id, target_id in (("run-1", "M31"), ("run-2", "M13")):
        store.write_calibration_identities(
            CalibrationIdentities(run_id=run_id, subject_id=f"{target_id}/stack.fits", target_id=target_id)
        )

    assert [record.run_id for record in quality.get_calibration_identities(run_ids=["run-2"])] == ["run-2"]
    assert [record.target_id for record in quality.get_calibration_identities(Target(id="M31"))] == ["M31"]


def test_stored_measurements_come_back_filtered(quality: QualityMeasurements, tmp_path: Path) -> None:
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
    assert list(quality.query(target=Target(id="nobody"))) == []
    assert len(quality.query(since="2026-10-01T03:30:00")) == 1
    assert len(quality.query(since=BASE_TIME + timedelta(hours=2))) == 0
    assert len(quality.query(level="frame", latest_only=False, until=BASE_TIME + timedelta(minutes=30))) == 1
    cited = quality.get(1)
    assert cited is not None and cited.row_id == 1
    assert quality.get(9999) is None
    with pytest.raises(NotFoundError):
        quality.get(9999, required=True)


def test_a_limit_and_offset_page_the_answer_and_say_whether_more_remain(
    quality: QualityMeasurements, tmp_path: Path
) -> None:
    """With no limit every match comes back; a limit pages the answer."""
    QualityStore(str(tmp_path / "astrometrics.db")).write_measurements([
        _measurement("run-1", BASE_TIME),
        _measurement("run-2", BASE_TIME + timedelta(hours=1), value=3.0),
        _measurement("run-3", BASE_TIME + timedelta(hours=2), value=4.0),
    ])

    everything = quality.query(latest_only=False)
    assert (len(everything), everything.total, everything.next_offset) == (3, 3, None)
    first_page = quality.query(latest_only=False, limit=2)
    assert [row.value for row in first_page] == [2.5, 3.0]
    assert (first_page.total, first_page.next_offset) == (3, 2)
    last_page = quality.query(latest_only=False, limit=2, offset=2)
    assert [row.value for row in last_page] == [4.0]
    assert last_page.next_offset is None
    with pytest.raises(InvalidArgumentError):
        quality.query(limit=0)
    with pytest.raises(InvalidArgumentError):
        quality.query(offset=-1)


def test_variant_and_library_only_filters(quality: QualityMeasurements) -> None:
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


def test_variant_with_library_only_is_refused(quality: QualityMeasurements) -> None:
    """Asking for one subset and for none at once raises an error."""
    with pytest.raises(InvalidArgumentError):
        quality.query(variant="half", library_only=True)


def test_bad_arguments_are_refused(quality: QualityMeasurements) -> None:
    """An unknown level or an unreadable time raises `InvalidArgumentError`."""
    with pytest.raises(InvalidArgumentError):
        quality.query(level="galaxy")
    with pytest.raises(InvalidArgumentError):
        quality.query(since="last night")


def test_an_unregistered_metric_name_is_refused(quality: QualityMeasurements) -> None:
    """`query(metric=...)` lists the registered names when it is wrong."""
    with pytest.raises(InvalidArgumentError, match=r"frame\.background_level_adu"):
        quality.query(metric="frame.no_such_metric")
    assert list(quality.query(metric="frame.background_level_adu")) == []


def test_get_metric_gives_that_one_spec(quality: QualityMeasurements) -> None:
    """`get_metric(name)` gives one declaration or refuses an unknown name."""
    assert quality.get_metric("frame.background_level_adu").name == "frame.background_level_adu"
    with pytest.raises(NotFoundError):
        quality.get_metric("frame.no_such_metric")


def test_get_metrics_lists_the_declarations(quality: QualityMeasurements) -> None:
    """`get_metrics` gives every full declaration, each once."""
    specs = quality.get_metrics()
    assert specs
    assert all(isinstance(spec, MetricSpec) for spec in specs)
    assert len({spec.name for spec in specs}) == len(specs)


def test_get_metrics_filters_by_checkpoint(quality: QualityMeasurements) -> None:
    """A checkpoint keeps only its metrics; an unknown one raises."""
    frame_metrics = quality.get_metrics(checkpoint="frame")
    assert frame_metrics
    assert all(spec.checkpoint.value == "frame" for spec in frame_metrics)
    assert len(frame_metrics) < len(quality.get_metrics())
    with pytest.raises(InvalidArgumentError):
        quality.get_metrics(checkpoint="nowhere")


def test_the_root_object_holds_the_quality_child() -> None:
    """`Astrometrics.quality` shares the target catalog; no old alias."""
    from unittest.mock import MagicMock

    from astrometricslib import AppConfiguration
    from astrometricslib.exceptions import MovedError

    astrometrics = astrometrics_with_drivers(AppConfiguration(), MagicMock())
    assert isinstance(astrometrics.quality, QualityRecords)
    assert astrometrics.quality.records._targets is astrometrics.targets
    for old in ("query", "measure_frames", "metrics", "resolve_rows", "measure_tuned_parameters"):
        with pytest.raises(MovedError, match="moved to astrometrics"):
            getattr(astrometrics.quality, old)
    assert not hasattr(astrometrics.processing, "diagnostics")


def test_query_fills_each_row_with_its_metric_unit(quality: QualityMeasurements, tmp_path: Path) -> None:
    """A row read back carries the unit of its metric."""
    QualityStore(quality_database_path(quality._config)).write_measurements([_measurement("run", BASE_TIME)])

    [row] = quality.query()

    assert row.unit == "arcsec"


def test_get_paths_gives_frame_and_stack_paths_and_reasons(tmp_path: Path) -> None:
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
    records = QualityMeasurements(configuration, targets=SimpleNamespace(get=lambda target_id: target))
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

    result = records.get_paths([
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
    quality: QualityMeasurements, tmp_path: Path
) -> None:
    """A stored identity row is read back and turned into its text."""
    from astrometricslib._internal.stacking import aperture_identity_from_number, aperture_identity_to_number

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
    rows = quality.query(metric="stacking.aperture_set_number", target=Target(id="M13"), library_only=True)
    assert [aperture_identity_from_number(row.value) for row in rows] == ["v3-156104df543d"]


def _frame_measurement(
    subject_id: str, run_id: str = "run-1", metric: str = "frame.star_width_moffat_arcsec"
) -> QualityMeasurement:
    """Build a measured measurement about one frame.

    Returns
    -------
    measurement : `QualityMeasurement`
        A measurement whose subject is the given frame.
    """
    return _measurement(
        run_id,
        BASE_TIME,
        metric=metric,
        subject=QualitySubject(level=SubjectLevel.FRAME, subject_id=subject_id, target_id="M31"),
    )


def test_deleting_frame_measurements_removes_only_the_given_frames(
    quality: QualityMeasurements, tmp_path: Path
) -> None:
    """A deleted frame loses its rows and subset weights; the rest stay."""
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    store.write_measurements([
        _frame_measurement("M31/a.fits"),
        _frame_measurement("M31/a.fits", metric="frame.elongation"),
        _frame_measurement("M31/a.fits#odd", run_id="run-2"),
        _frame_measurement("M31/ab.fits"),
        _frame_measurement("M31/b.fits"),
        _measurement(
            "run-1",
            BASE_TIME,
            subject=QualitySubject(level=SubjectLevel.STACK, subject_id="M31/a.fits", target_id="M31"),
        ),
    ])

    assert store.delete_frame_measurements(["M31/a.fits", "M31/missing.fits", ""]) == 3

    remaining = {
        (row.subject.level.value, row.subject.subject_id) for row in quality.query(latest_only=False)
    }
    assert remaining == {("frame", "M31/ab.fits"), ("frame", "M31/b.fits"), ("stack", "M31/a.fits")}


def test_deleting_measurements_with_no_database_creates_nothing(tmp_path: Path) -> None:
    """Deleting where nothing is stored is a no-op that creates no file."""
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    assert store.delete_frame_measurements(["M31/a.fits"]) == 0
    assert store.delete_frame_measurements([]) == 0
    assert list(tmp_path.iterdir()) == []


def test_deleting_a_targets_frames_forgets_their_measurements(tmp_path: Path) -> None:
    """Deleting frames of a target drops the deleted frames' rows only."""
    from unittest.mock import MagicMock

    from astrometricslib.api.targets import TargetCatalog
    from astrometricslib.models.target import FrameRecord, Target

    frames = tmp_path / "frames" / "lights" / "M31" / "scope" / "cam"
    frames.mkdir(parents=True)
    gone, kept = frames / "gone.fits", frames / "kept.fits"
    gone.write_bytes(b"x")
    kept.write_bytes(b"x")
    target = Target(id="M31", frames=[FrameRecord(path=str(gone)), FrameRecord(path=str(kept))])
    storage = MagicMock()
    storage.get.return_value = [target]
    del storage.merge_and_record
    configuration = SimpleNamespace(get_library_path=lambda: tmp_path)
    catalog = TargetCatalog(configuration, storage)
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    store.write_measurements([
        _frame_measurement(target.frame_subject_id(str(gone))),
        _frame_measurement(target.frame_subject_id(str(kept))),
    ])

    result = catalog.frames.delete(target, [str(gone)])

    assert result.deleted == [str(gone)]
    assert not gone.exists()
    rows = QualityMeasurements(configuration).query(latest_only=False)
    assert [row.subject.subject_id for row in rows] == ["M31/scope/cam/kept.fits"]


def test_the_data_version_changes_only_with_the_matching_rows(
    quality: QualityMeasurements, tmp_path: Path
) -> None:
    """The version follows the rows of its metric, level and target only.

    Asking before anything is stored creates no file. An added row of the
    metric changes the version, at any target for a library-wide version;
    a row of another metric, a stack row and a row of another target leave
    a narrower version as it is; a deleted frame changes it back.
    """
    width = "frame.star_width_moffat_arcsec"
    empty = quality.get_data_version(metric=width, level="frame")
    assert isinstance(empty, str)
    assert list(tmp_path.iterdir()) == []

    store = QualityStore(str(tmp_path / "astrometrics.db"))
    store.write_measurements([_measurement("run-1", BASE_TIME)])
    library = quality.get_data_version(metric=width, level="frame")
    target = quality.get_data_version(target=Target(id="M31"))
    assert library != empty
    assert quality.get_data_version(metric=width, level="frame") == library

    other_target = _measurement(
        "run-2",
        BASE_TIME,
        subject=QualitySubject(level=SubjectLevel.FRAME, subject_id="M13/a.fits", target_id="M13"),
    )
    store.write_measurements([other_target])
    assert quality.get_data_version(target=Target(id="M31")) == target
    after_other_target = quality.get_data_version(metric=width, level="frame")
    assert after_other_target != library

    other_metric = _measurement("run-3", BASE_TIME, metric="frame.star_elongation", value=1.1)
    stack = _measurement(
        "run-4",
        BASE_TIME,
        subject=QualitySubject(level=SubjectLevel.STACK, subject_id="M13/stack.fits", target_id="M13"),
    )
    store.write_measurements([other_metric, stack])
    assert quality.get_data_version(metric=width, level="frame") == after_other_target

    # The deleted frame leaves the rows as they were after the first write,
    # so the version is that one again.
    store.delete_frame_measurements(["M13/a.fits"])
    assert quality.get_data_version(metric=width, level="frame") == library


def test_the_data_version_refuses_unknown_names(quality: QualityMeasurements) -> None:
    """An unregistered metric, an unknown level and a bare id are refused."""
    with pytest.raises(InvalidArgumentError):
        quality.get_data_version(metric="frame.no_such_metric")
    with pytest.raises(InvalidArgumentError):
        quality.get_data_version(level="galaxy")
    with pytest.raises(InvalidArgumentError):
        quality.get_data_version(target="M31")  # type: ignore[arg-type]
