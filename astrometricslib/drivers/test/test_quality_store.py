"""Tests for the append-only quality measurement store.

Covers a full round trip of every field, re-run safety (including a missing
channel), the latest view, every query filter, capture-setting get-or-create,
`StorageError` on a failing write, and an idempotent migration that leaves
other tables alone.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from astrometricslib.drivers.quality_store import SCHEMA_VERSION, QualityStore, quality_tables_exist
from astrometricslib.foundation.errors import StorageError
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)

BASE_TIME = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)


def make_measurement(**overrides: object) -> QualityMeasurement:
    """Build a valid measurement, with any field replaced.

    Returns
    -------
    measurement : `QualityMeasurement`
        A measured frame-level measurement.
    """
    subject = overrides.pop("subject", None) or QualitySubject(
        level=SubjectLevel.FRAME, subject_id="M31/frame_001.fits", target_id="M31"
    )
    fields: dict = {
        "metric": "frame.star_width_moffat_arcsec",
        "definition_version": 1,
        "subject": subject,
        "status": MeasurementStatus.MEASURED,
        "value": 2.5,
        "origin": MeasurementOrigin.MEASURED,
        "run_id": "run-1",
        "measured_at": BASE_TIME,
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


def test_round_trip_keeps_every_field(store: QualityStore) -> None:
    """A full measurement and a not-measured one read back equal."""
    setting_id = store.get_or_create_capture_setting(CaptureSetting(camera="ASI533MM"))
    full = make_measurement(
        subject=QualitySubject(
            level=SubjectLevel.STACK,
            subject_id="stack-7",
            target_id="M31",
            observing_night_id="2026-10-01",
            capture_setting_id=setting_id,
            channel="G",
        ),
        uncertainty=0.1,
        sample_count=42,
        check_outcome=GateStatus.FAILED,
        check_limit=QualityLimit(
            value=3.0,
            unit="arcsec",
            tier=LimitTier.VALIDATED_DATASET,
            derivation="From the M 81 run.",
            sample_count=12,
            register_entry="STK-07",
        ),
        action_taken="frame moved to _excluded/",
        origin=MeasurementOrigin.BACKFILLED,
        pipeline_version=None,
        job_id="job-9",
    )
    missing = make_measurement(
        metric="frame.sky_level",
        status=MeasurementStatus.NOT_MEASURED,
        value=None,
        reason="No stars found.",
    )
    assert store.write_measurements([full, missing]) == 2
    read = {m.metric: m for m in store.query()}
    assert read[full.metric].model_dump(exclude={"row_id"}) == full.model_dump(exclude={"row_id"})
    assert read[missing.metric].model_dump(exclude={"row_id"}) == missing.model_dump(exclude={"row_id"})
    assert read[full.metric].row_id is not None
    assert read[missing.metric].value is None
    assert read[missing.metric].reason == "No stars found."


def test_rerun_writes_no_duplicates_even_without_channel(store: QualityStore) -> None:
    """Writing the same run twice adds nothing, with channel None."""
    measurement = make_measurement()
    assert measurement.subject.channel is None
    assert store.write_measurements([measurement]) == 1
    changed = make_measurement(value=9.9)
    assert store.write_measurements([measurement, changed]) == 0
    rows = store.query(latest_only=False)
    assert len(rows) == 1
    assert rows[0].value == pytest.approx(2.5)


def test_write_empty_list_returns_zero(store: QualityStore) -> None:
    """Writing nothing stores nothing."""
    assert store.write_measurements([]) == 0


def test_latest_view_picks_newest_by_time_then_row(store: QualityStore) -> None:
    """The newest `measured_at` wins; ties fall back to the later row."""
    older = make_measurement(run_id="a", value=1.0, measured_at=BASE_TIME)
    newer = make_measurement(run_id="b", value=2.0, measured_at=BASE_TIME + timedelta(hours=1))
    store.write_measurements([newer])
    store.write_measurements([older])
    assert [m.value for m in store.query()] == [2.0]
    assert len(store.query(latest_only=False)) == 2
    tie_first = make_measurement(run_id="c", value=3.0, measured_at=BASE_TIME + timedelta(hours=1))
    store.write_measurements([tie_first])
    assert [m.value for m in store.query()] == [3.0]


def test_latest_view_ignores_pipeline_version(store: QualityStore) -> None:
    """A newer time with an older pipeline version is still the latest."""
    store.write_measurements([make_measurement(run_id="a", value=1.0, pipeline_version="9.0")])
    later = BASE_TIME + timedelta(days=1)
    store.write_measurements([
        make_measurement(run_id="b", value=2.0, pipeline_version="1.0", measured_at=later)
    ])
    assert [m.value for m in store.query()] == [2.0]


def test_query_filters(store: QualityStore) -> None:
    """Every filter narrows the result as described."""
    nikon = store.get_or_create_capture_setting(CaptureSetting(camera="Nikon", telescope="Lens"))
    asi = store.get_or_create_capture_setting(CaptureSetting(camera="ASI533MM", telescope="RC"))

    def subject(
        identifier: str, level: SubjectLevel, target: str, night: str, setting: int
    ) -> QualitySubject:
        """Build a subject for the filter test.

        Returns
        -------
        subject : `QualitySubject`
            The subject.
        """
        return QualitySubject(
            level=level,
            subject_id=identifier,
            target_id=target,
            observing_night_id=night,
            capture_setting_id=setting,
        )

    store.write_measurements([
        make_measurement(subject=subject("f1", SubjectLevel.FRAME, "M31", "n1", nikon)),
        make_measurement(
            metric="frame.sky_level",
            subject=subject("f2", SubjectLevel.FRAME, "M13", "n2", asi),
            measured_at=BASE_TIME + timedelta(days=2),
        ),
        make_measurement(
            metric="stack.noise",
            subject=subject("s1", SubjectLevel.STACK, "M31", "n2", asi),
            measured_at=BASE_TIME + timedelta(days=1),
        ),
    ])
    assert len(store.query()) == 3
    assert [m.subject.subject_id for m in store.query(metric="frame.sky_level")] == ["f2"]
    assert {m.subject.subject_id for m in store.query(level=SubjectLevel.STACK)} == {"s1"}
    assert {m.subject.subject_id for m in store.query(target_id="M31")} == {"f1", "s1"}
    assert {m.subject.subject_id for m in store.query(observing_night_id="n2")} == {"f2", "s1"}
    assert {m.subject.subject_id for m in store.query(camera="Nikon")} == {"f1"}
    assert {m.subject.subject_id for m in store.query(telescope="RC")} == {"f2", "s1"}
    assert {m.subject.subject_id for m in store.query(since=BASE_TIME + timedelta(days=1))} == {"f2", "s1"}
    ids = [m.row_id for m in store.query(target_id="M31")]
    assert {m.row_id for m in store.query(row_ids=ids)} == set(ids)
    assert store.query(row_ids=[]) == []


def test_row_ids_return_exact_rows_even_when_superseded(store: QualityStore) -> None:
    """A cited row is found by id after a newer row replaced it."""
    store.write_measurements([make_measurement(run_id="a", value=1.0)])
    first_id = store.query()[0].row_id
    store.write_measurements([
        make_measurement(run_id="b", value=2.0, measured_at=BASE_TIME + timedelta(hours=1))
    ])
    cited = store.query(row_ids=[first_id])
    assert [m.value for m in cited] == [1.0]


def test_capture_setting_get_or_create_is_stable(store: QualityStore) -> None:
    """Equal settings, including ones with missing fields, share one id."""
    partial = CaptureSetting(camera="ASI533MM")
    full = CaptureSetting(
        camera="ASI533MM",
        telescope="RC",
        focal_length_mm=1000.0,
        pixel_scale_arcsec=0.8,
        filter_name="L",
        exposure_seconds=30.0,
        gain_setting=100,
        offset=10,
        binning=1,
    )
    first = store.get_or_create_capture_setting(partial)
    assert store.get_or_create_capture_setting(partial) == first
    second = store.get_or_create_capture_setting(full)
    assert second != first
    assert store.get_or_create_capture_setting(full) == second
    assert store.get_capture_setting(second) == full
    assert store.get_capture_setting(first) == partial
    assert store.get_capture_setting(9999) is None


def test_failing_write_raises_storage_error(store: QualityStore) -> None:
    """A damaged database raises `StorageError` and stores nothing."""
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("DROP VIEW quality_measurements_latest")
        connection.execute("DROP TABLE quality_measurements")
    with pytest.raises(StorageError):
        store.write_measurements([make_measurement()])


def test_unopenable_database_raises_storage_error(tmp_path: Path) -> None:
    """A path that is a directory raises `StorageError` at construction."""
    with pytest.raises(StorageError):
        QualityStore(str(tmp_path))


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    """A database from a newer version raises `StorageError`."""
    path = tmp_path / "astrometrics.db"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(StorageError):
        QualityStore(str(path))


def test_migration_is_idempotent_and_leaves_other_tables_alone(tmp_path: Path) -> None:
    """Opening twice keeps the data; a foreign table is unchanged."""
    path = tmp_path / "astrometrics.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO targets VALUES ('M31', 'Andromeda')")
    first = QualityStore(str(path))
    first.write_measurements([make_measurement()])
    second = QualityStore(str(path))
    assert len(second.query()) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT id, name FROM targets").fetchall() == [("M31", "Andromeda")]
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"quality_measurements", "quality_capture_setting", "quality_measurements_latest"} <= names


def test_from_configuration_uses_library_path(tmp_path: Path) -> None:
    """The configured library path decides where astrometrics.db lives."""

    class FakeConfiguration:
        """A stand-in configuration that only knows the library path."""

        def get_library_path(self) -> Path:
            """Return the temporary library folder.

            Returns
            -------
            path : `Path`
                The library folder.
            """
            return tmp_path

    store = QualityStore.from_configuration(FakeConfiguration())  # type: ignore[arg-type]
    assert store.db_path == str(tmp_path / "astrometrics.db")
    assert (tmp_path / "astrometrics.db").exists()


def test_quality_tables_exist_reads_without_creating(tmp_path: Path) -> None:
    """A missing file or table reports `False` and is left uncreated."""
    database = tmp_path / "astrometrics.db"
    assert quality_tables_exist(str(database)) is False
    assert not database.exists()

    QualityStore(str(database))
    assert quality_tables_exist(str(database)) is True
