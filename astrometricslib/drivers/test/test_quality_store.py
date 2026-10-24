"""Tests for the append-only quality measurement store.

Covers a full round trip of every field, re-run safety (including a missing
channel), the latest view, every query filter, capture-setting get-or-create,
`StorageError` on a failing write, and an idempotent migration that leaves
other tables alone.
"""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from astrometricslib.drivers.quality_store import SCHEMA_VERSION, QualityStore
from astrometricslib.foundation.errors import InvalidArgumentError, StorageError
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


def test_capture_settings_batch_read(store: QualityStore, tmp_path: Path) -> None:
    """One read gives several settings and creates no file."""
    first = store.get_or_create_capture_setting(CaptureSetting(camera="ASI533MM"))
    second = store.get_or_create_capture_setting(CaptureSetting(camera="ASI533MM", gain_setting=100))
    everything = store.get_capture_settings()
    assert set(everything) == {first, second}
    assert everything[second].gain_setting == 100
    assert set(store.get_capture_settings([second, 9999])) == {second}
    assert store.get_capture_settings([]) == {}
    missing = tmp_path / "absent" / "astrometrics.db"
    assert QualityStore(str(missing)).get_capture_settings() == {}
    assert not missing.exists()


def test_failing_write_raises_storage_error(store: QualityStore) -> None:
    """A damaged database raises `StorageError` and stores nothing."""
    store.write_measurements([make_measurement(run_id="before")])
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("DROP VIEW quality_measurements_latest")
        connection.execute("DROP TABLE quality_measurements")
    with pytest.raises(StorageError):
        store.write_measurements([make_measurement()])


def test_unopenable_database_raises_storage_error(tmp_path: Path) -> None:
    """A path that is a directory raises `StorageError` on a write."""
    with pytest.raises(StorageError):
        QualityStore(str(tmp_path)).write_measurements([make_measurement()])


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    """A database from a newer version raises `StorageError`."""
    path = tmp_path / "astrometrics.db"
    QualityStore(str(path)).write_measurements([make_measurement()])
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE quality_schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    with pytest.raises(StorageError):
        QualityStore(str(path)).write_measurements([make_measurement()])
    with pytest.raises(StorageError):
        QualityStore(str(path)).query()
    with pytest.raises(StorageError):
        QualityStore(str(path)).get_capture_setting(1)


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
        assert connection.execute("SELECT version FROM quality_schema_version").fetchall() == [
            (SCHEMA_VERSION,)
        ]
        assert connection.execute("SELECT id, name FROM targets").fetchall() == [("M31", "Andromeda")]
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"quality_measurements", "quality_capture_setting", "quality_measurements_latest"} <= names


def test_foreign_user_version_is_ignored_and_kept(tmp_path: Path) -> None:
    """A file with a user_version set by other code works and keeps it."""
    path = tmp_path / "astrometrics.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE stellar_objects (id TEXT)")
        connection.execute("PRAGMA user_version = 2")
    store = QualityStore(str(path))
    assert store.query() == []
    store.write_measurements([make_measurement()])
    assert len(store.query()) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_first_release_database_gets_version_table(tmp_path: Path) -> None:
    """Tables with no version table read as version 1, then upgrade."""
    path = tmp_path / "astrometrics.db"
    QualityStore(str(path)).write_measurements([make_measurement()])
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE quality_schema_version")
        connection.execute("PRAGMA user_version = 1")
    store = QualityStore(str(path))
    assert len(store.query()) == 1
    store.write_measurements([make_measurement(run_id="second")])
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version FROM quality_schema_version").fetchall() == [
            (SCHEMA_VERSION,)
        ]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        count = connection.execute("SELECT COUNT(*) FROM quality_measurements").fetchone()[0]
    assert count == 2


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
    assert not (tmp_path / "astrometrics.db").exists()
    store.write_measurements([make_measurement()])
    assert (tmp_path / "astrometrics.db").exists()


def test_read_path_creates_no_file_and_returns_empty(tmp_path: Path) -> None:
    """Reading a missing database creates nothing and gives empty answers."""
    database = tmp_path / "astrometrics.db"
    reader = QualityStore(str(database))
    assert reader.query() == []
    assert reader.get_capture_setting(1) is None
    assert not database.exists()
    assert not list(tmp_path.iterdir())


def test_read_path_does_not_migrate(tmp_path: Path) -> None:
    """A database with no quality tables reads empty and stays unchanged."""
    database = tmp_path / "astrometrics.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE targets (id TEXT)")
    reader = QualityStore(str(database))
    assert reader.query() == []
    assert reader.get_capture_setting(1) is None
    with sqlite3.connect(database) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert names == {"targets"}


def test_read_path_takes_no_write_lock(store: QualityStore) -> None:
    """A read succeeds while another connection holds the write lock."""
    store.write_measurements([make_measurement()])
    holder = sqlite3.connect(store.db_path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        assert len(store.query()) == 1
        assert store.get_capture_setting(1) is None
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_rolled_back_batch_stores_nothing(store: QualityStore) -> None:
    """A batch whose later row fails is rolled back entirely."""
    good = make_measurement(run_id="batch")
    bad = make_measurement(run_id="batch", metric="frame.other")
    object.__setattr__(bad, "definition_version", None)  # NOT NULL column rejects it
    with pytest.raises(StorageError):
        store.write_measurements([good, bad])
    assert store.query() == []


def test_concurrent_writers_leave_no_duplicates(store: QualityStore) -> None:
    """Two threads writing the same run store each row once."""
    batch = [make_measurement(metric=f"frame.m{index}") for index in range(20)]
    store.write_measurements([make_measurement(run_id="warm")])
    results: list[int] = []

    def write() -> None:
        """Write the shared batch with a store of its own."""
        results.append(QualityStore(store.db_path).write_measurements(batch))

    threads = [threading.Thread(target=write) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [0, 20]
    assert len(store.query(latest_only=False)) == 21


def test_concurrent_capture_setting_creation_gives_one_id(store: QualityStore) -> None:
    """Two threads creating the same capture setting get one id."""
    store.write_measurements([make_measurement()])
    setting = CaptureSetting(camera="ASI533MM", filter_name="L")
    ids: list[int] = []

    def create() -> None:
        """Create the shared setting with a store of its own."""
        ids.append(QualityStore(store.db_path).get_or_create_capture_setting(setting))

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(ids) == 2
    assert ids[0] == ids[1]


def test_since_equal_to_a_stored_time_includes_it(store: QualityStore) -> None:
    """`since` at exactly a stored time keeps that row, across time zones."""
    store.write_measurements([make_measurement()])
    assert len(store.query(since=BASE_TIME)) == 1
    plus_two = BASE_TIME.astimezone(timezone(timedelta(hours=2)))
    assert len(store.query(since=plus_two)) == 1
    assert store.query(since=BASE_TIME + timedelta(microseconds=1)) == []
    with sqlite3.connect(store.db_path) as connection:
        stored = connection.execute("SELECT measured_at FROM quality_measurements").fetchone()[0]
    assert stored.endswith("+00:00")


def test_naive_since_is_refused(store: QualityStore) -> None:
    """A `since` with no time zone is refused."""
    store.write_measurements([make_measurement()])
    with pytest.raises(InvalidArgumentError):
        store.query(since=datetime(2026, 10, 1))


def test_undecodable_row_raises_storage_error_naming_the_row(store: QualityStore) -> None:
    """A stored row that fails validation raises `StorageError` naming it."""
    store.write_measurements([make_measurement()])
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE quality_measurements SET status = 'bogus'")
    with pytest.raises(StorageError) as caught:
        store.query()
    assert caught.value.details["row_id"] == 1


# The version-1 schema, copied from the store as it was before the ``variant``
# column: the table, and the view that was made with ``SELECT *``.
_VERSION_1_TABLE_SQL = """
    CREATE TABLE quality_measurements (
        row_id INTEGER PRIMARY KEY,
        metric TEXT NOT NULL,
        definition_version INTEGER NOT NULL,
        subject_level TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        target_id TEXT,
        observing_night_id TEXT,
        capture_setting_id INTEGER,
        channel TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        value REAL,
        uncertainty REAL,
        sample_count INTEGER,
        reason TEXT NOT NULL DEFAULT '',
        check_outcome TEXT,
        check_limit_value REAL,
        check_limit_unit TEXT,
        check_limit_tier TEXT,
        check_limit_derivation TEXT,
        check_limit_sample_count INTEGER,
        check_limit_register_entry TEXT,
        action_taken TEXT,
        origin TEXT NOT NULL,
        pipeline_version TEXT,
        run_id TEXT NOT NULL,
        job_id TEXT,
        measured_at TEXT NOT NULL,
        UNIQUE (run_id, metric, subject_level, subject_id, channel)
    )
"""
_VERSION_1_VIEW_SQL = """
    CREATE VIEW quality_measurements_latest AS
    SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY subject_level, subject_id, metric, channel
            ORDER BY measured_at DESC, row_id DESC
        ) AS recency_rank
        FROM quality_measurements
    )
    WHERE recency_rank = 1
"""


def _make_version_1_database(path: Path) -> None:
    """Write a version-1 database with one library row and one subset row.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the database.
    """
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE quality_capture_setting (capture_setting_id INTEGER PRIMARY KEY, camera TEXT)"
        )
        connection.execute(_VERSION_1_TABLE_SQL)
        connection.execute(_VERSION_1_VIEW_SQL)
        connection.execute("CREATE TABLE quality_schema_version (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO quality_schema_version (version) VALUES (1)")
        for subject_id in ("M 57/M_57_L_Stacked.fits", "M 57/_subsets/M_57.nights-1/M_57.nights-1.fits"):
            connection.execute(
                "INSERT INTO quality_measurements (metric, definition_version, subject_level, subject_id, "
                "target_id, status, value, origin, run_id, measured_at) VALUES "
                "('stacking.frames_stacked', 1, 'stack', ?, 'M 57', 'measured', 5.0, 'measured', 'run-0', "
                "'2026-10-01T00:00:00.000000+00:00')",
                (subject_id,),
            )


def test_a_version_1_database_gains_the_variant_column_and_fills_it_for_subset_rows(tmp_path: Path) -> None:
    """A first write to a version-1 file adds the column and labels."""
    path = tmp_path / "astrometrics.db"
    _make_version_1_database(path)
    store = QualityStore(str(path))
    # A read never migrates: the old rows read as library rows.
    assert {row.subject.variant for row in store.query()} == {None}
    store.write_measurements([make_measurement(run_id="run-1")])
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version FROM quality_schema_version").fetchall() == [
            (SCHEMA_VERSION,)
        ]
    labels = {row.subject.subject_id: row.subject.variant for row in store.query()}
    assert labels["M 57/M_57_L_Stacked.fits"] is None
    assert labels["M 57/_subsets/M_57.nights-1/M_57.nights-1.fits"] == "M_57.nights-1"
    # The "latest" view was made again, so it carries the new column too.
    assert [row.subject.variant for row in store.query(variant="M_57.nights-1")] == ["M_57.nights-1"]


def test_the_variant_filters_pick_subset_rows_or_library_rows(store: QualityStore) -> None:
    """A subset row carries its label; the filters select by that field."""
    library = QualitySubject(level=SubjectLevel.STACK, subject_id="M31/stack.fits", target_id="M31")
    subset = QualitySubject(
        level=SubjectLevel.STACK,
        subject_id="M31/_subsets/half/half.fits",
        target_id="M31",
        variant="half",
    )
    store.write_measurements([make_measurement(subject=library), make_measurement(subject=subset)])
    assert len(store.query()) == 2
    assert [row.subject.variant for row in store.query(library_only=True)] == [None]
    assert [row.subject.variant for row in store.query(variant="half")] == ["half"]
    assert store.query(variant="other") == []
    with pytest.raises(InvalidArgumentError):
        store.query(variant="half", library_only=True)
