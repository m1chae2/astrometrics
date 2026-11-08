"""Tests for the library's write counters (`data_versions`).

Checks that the counters start at zero, count each kind on its own, are read
without writing, are seen by a second connection (another program), and are
bumped by every writer: the target and star Butler (`put`, `put_all`,
`merge_and_record`, `delete_by_ids`), the quality store, and the target
fixture writer.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib._internal.drivers.catalog_access import CatalogAccess
from astrometricslib._internal.drivers.local_database import save_target
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.storage.data_versions import (
    ALL_KINDS,
    QUALITY_KIND,
    STARS_KIND,
    TARGETS_KIND,
    bump_data_version,
    get_camera_data_versions,
    get_library_data_version,
    get_stack_quality_data_version,
    get_target_camera_names,
    get_target_data_version,
    read_data_versions,
)
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target


@pytest.fixture
def config(tmp_path: Path) -> AppConfiguration:
    """Give a configuration whose library is a temporary folder.

    Returns
    -------
    config : `AppConfiguration`
        The configuration.
    """
    library = tmp_path / "library"
    library.mkdir()
    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library)}})
    return configuration


def _counters(config: AppConfiguration) -> dict[str, int]:
    """Read every counter of the test library.

    Returns
    -------
    counters : `dict` [`str`, `int`]
        Each kind with its count (0 when never written).
    """
    return dict(get_library_data_version(config))


def test_a_library_that_was_never_written_reads_all_zero(config: AppConfiguration) -> None:
    """A missing file and a missing table both mean no writes yet."""
    assert _counters(config) == dict.fromkeys(ALL_KINDS, 0)
    assert read_data_versions(str(Path(str(config.get_library_path())) / "astrometrics.db")) == {}


def test_a_file_that_is_not_a_database_gives_an_unknown_version(tmp_path: Path) -> None:
    """An unreadable file gives `None`, so a cache keeps nothing."""
    broken = tmp_path / "astrometrics.db"
    broken.write_bytes(b"not a database" * 100)

    assert read_data_versions(str(broken)) is None
    assert get_library_data_version(SimpleNamespace(get_library_path=lambda: tmp_path)) is None


def test_the_counters_count_each_kind_on_their_own(tmp_path: Path) -> None:
    """A bump adds one to its kind and leaves the others alone."""
    database = tmp_path / "astrometrics.db"
    connection = sqlite3.connect(database)
    bump_data_version(connection, TARGETS_KIND)
    bump_data_version(connection, TARGETS_KIND)
    bump_data_version(connection, STARS_KIND)
    connection.commit()
    connection.close()

    assert read_data_versions(str(database)) == {TARGETS_KIND: 2, STARS_KIND: 1}
    config = SimpleNamespace(get_library_path=lambda: tmp_path)
    assert get_library_data_version(config, (STARS_KIND, QUALITY_KIND)) == (
        (QUALITY_KIND, 0),
        (STARS_KIND, 1),
    )


def test_a_rolled_back_write_does_not_count(tmp_path: Path) -> None:
    """The counter changes with the data, in the same transaction."""
    database = tmp_path / "astrometrics.db"
    connection = sqlite3.connect(database)
    bump_data_version(connection, TARGETS_KIND)
    connection.rollback()
    connection.close()

    assert not read_data_versions(str(database))


def test_target_butler_writers_each_count_a_write(config: AppConfiguration) -> None:
    """Each target write path bumps the targets counter."""
    access = CatalogAccess(config=config)
    other_program = CatalogAccess(config=config)

    access.put([Target(id="M 1")], "target_catalog", {})
    assert _counters(config)[TARGETS_KIND] == 1
    access.put(Target(id="M 2"), "target_record", {})
    assert _counters(config)[TARGETS_KIND] == 2
    access.merge_and_record("target_catalog", [Target(id="M 3")], lambda _old, new: new)
    assert other_program.get_dataset_version("target_catalog") == 3
    access.delete_by_ids("target_catalog", ["M 3"])
    assert other_program.get_dataset_version("target_catalog") == 4
    # Nothing to merge or delete is not a write.
    access.merge_and_record("target_catalog", [], lambda _old, new: new)
    access.delete_by_ids("target_catalog", [])
    assert other_program.get_dataset_version("target_catalog") == 4
    assert _counters(config)[STARS_KIND] == 0


def test_star_catalog_writers_count_in_the_stars_kind(config: AppConfiguration) -> None:
    """A star write bumps the stars counter and not the targets counter."""
    access = CatalogAccess(config=config)
    access.put([StellarObject(id="Vega")], "stellar_catalog", {})
    access.merge_and_record("stellar_catalog", [StellarObject(id="Altair")], lambda _old, new: new)
    access.delete_by_ids("stellar_catalog", ["Vega"])

    assert _counters(config)[STARS_KIND] == 3
    assert _counters(config)[TARGETS_KIND] == 0


def test_the_fixture_target_writer_counts_a_write(config: AppConfiguration) -> None:
    """`save_target` (used by test libraries) bumps the targets counter."""
    save_target(app_config=config, target=Target(id="M 1"))

    assert _counters(config)[TARGETS_KIND] == 1


def _measurement(value: float = 2.5) -> QualityMeasurement:
    """Build a frame measurement.

    Returns
    -------
    measurement : `QualityMeasurement`
        A measured star-width row.
    """
    return QualityMeasurement(
        metric="frame.star_width_moffat_arcsec",
        definition_version=1,
        subject=QualitySubject(level=SubjectLevel.FRAME, subject_id="M31/f.fits", target_id="M31"),
        status=MeasurementStatus.MEASURED,
        value=value,
        origin=MeasurementOrigin.MEASURED,
        run_id="run-1",
        measured_at=datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )


def test_the_quality_store_counts_new_rows_but_not_repeats(config: AppConfiguration) -> None:
    """New rows bump the quality counter; a repeat does not."""
    store = QualityStore.from_configuration(config)

    store.write_measurements([_measurement()])
    assert _counters(config)[QUALITY_KIND] == 1
    store.write_measurements([_measurement()])
    assert _counters(config)[QUALITY_KIND] == 1
    store.write_measurements([_measurement().model_copy(update={"run_id": "run-2"})])
    assert _counters(config)[QUALITY_KIND] == 2


def test_the_target_version_sees_only_that_targets_changes(config: AppConfiguration) -> None:
    """A write for one target changes its version, not another's."""
    store = QualityStore.from_configuration(config)
    save_target(config, Target(id="M 31"))
    save_target(config, Target(id="M 13"))
    before = get_target_data_version(config, "M 31")
    other_before = get_target_data_version(config, "M 13")
    assert before is not None
    assert get_target_data_version(config, "M 31") == before

    # A measurement of M 31 changes M 31's version only.
    measurement = _measurement()
    measurement = measurement.model_copy(
        update={"subject": measurement.subject.model_copy(update={"target_id": "M 31"})}
    )
    store.write_measurements([measurement])
    assert get_target_data_version(config, "M 31") != before
    assert get_target_data_version(config, "M 13") == other_before

    # A changed document (a new frame record) changes that target's version.
    after_measurement = get_target_data_version(config, "M 31")
    save_target(config, Target(id="M 31", common_name="Andromeda"))
    assert get_target_data_version(config, "M 31") != after_measurement
    assert get_target_data_version(config, "M 13") == other_before


def test_the_target_version_of_a_library_that_was_never_written_is_empty(config: AppConfiguration) -> None:
    """A missing database gives a stable version, not an error."""
    assert get_target_data_version(config, "M 31") == get_target_data_version(config, "M 31")
    assert get_target_data_version(config, "M 31") is not None


def test_a_file_that_is_not_a_database_gives_an_unknown_target_version(config: AppConfiguration) -> None:
    """An unreadable database means no version, so nothing is kept."""
    (Path(str(config.get_library_path())) / "astrometrics.db").write_text("not a database")
    assert get_target_data_version(config, "M 31") is None


def _camera_measurement(store: QualityStore, camera: str, target: str, run_id: str) -> QualityMeasurement:
    """Build a frame measurement of a target taken with a camera.

    Returns
    -------
    measurement : `QualityMeasurement`
        The measurement, whose capture setting names the camera.
    """
    setting_id = store.get_or_create_capture_setting(CaptureSetting(camera=camera))
    measurement = _measurement()
    return measurement.model_copy(
        update={
            "run_id": run_id,
            "subject": measurement.subject.model_copy(
                update={
                    "target_id": target,
                    "capture_setting_id": setting_id,
                    "subject_id": f"{target}/{run_id}",
                }
            ),
        }
    )


def test_the_camera_version_sees_only_that_cameras_measurements(config: AppConfiguration) -> None:
    """One camera's measurement changes only its own version."""
    store = QualityStore.from_configuration(config)
    store.write_measurements([_camera_measurement(store, "CamA", "M 31", "run-1")])
    before = get_camera_data_versions(config, ["CamA", "CamB"])
    assert before is not None
    assert get_camera_data_versions(config, ["CamA", "CamB"]) == before

    store.write_measurements([_camera_measurement(store, "CamB", "M 13", "run-2")])
    after_b = get_camera_data_versions(config, ["CamA", "CamB"])
    assert after_b is not None
    assert after_b["CamA"] == before["CamA"]
    assert after_b["CamB"] != before["CamB"]

    # A frame of another target taken with the same camera changes it too.
    store.write_measurements([_camera_measurement(store, "CamA", "M 13", "run-3")])
    after_a = get_camera_data_versions(config, ["CamA", "CamB"])
    assert after_a is not None
    assert after_a["CamA"] != after_b["CamA"]
    assert after_a["CamB"] == after_b["CamB"]


def _night_measurement(
    store: QualityStore, camera: str, target: str, run_id: str, night: str
) -> QualityMeasurement:
    """Build a frame measurement of an observing night.

    Returns
    -------
    measurement : `QualityMeasurement`
        The measurement, with the observing night set.
    """
    measurement = _camera_measurement(store, camera, target, run_id)
    return measurement.model_copy(
        update={"subject": measurement.subject.model_copy(update={"observing_night_id": night})}
    )


def test_the_finished_night_camera_version_ignores_the_night_in_progress(config: AppConfiguration) -> None:
    """A frame tonight keeps the version; an old night changes it."""
    store = QualityStore.from_configuration(config)
    store.write_measurements([_night_measurement(store, "CamA", "M 31", "run-1", "2026-11-05")])

    def version(tonight: str) -> tuple[str | int, ...]:
        """Read CamA's finished-night version for a given tonight.

        Returns
        -------
        version : `tuple`
            The version.
        """
        found = get_camera_data_versions(config, ["CamA"], finished_before_night=tonight)
        assert found is not None
        return found["CamA"]

    before = version("2026-11-07")
    store.write_measurements([_night_measurement(store, "CamA", "M 13", "run-2", "2026-11-07")])
    assert version("2026-11-07") == before
    # The all-data version does see tonight's frame.
    all_data = get_camera_data_versions(config, ["CamA"])
    assert all_data is not None and all_data["CamA"][0] == 2

    # A frame of a finished night is counted.
    store.write_measurements([_night_measurement(store, "CamA", "M 13", "run-3", "2026-11-06")])
    after_finished = version("2026-11-07")
    assert after_finished != before

    # When the night rolls over, tonight's frame starts to count, once.
    rolled = version("2026-11-08")
    assert rolled != after_finished
    assert version("2026-11-08") == rolled
    assert rolled[0] == 3


def test_the_camera_version_sees_a_calibration_record_of_that_camera(config: AppConfiguration) -> None:
    """A camera's calibration record changes its version."""
    store = QualityStore.from_configuration(config)
    store.write_measurements([_camera_measurement(store, "CamA", "M 31", "run-1")])
    before = get_camera_data_versions(config, ["CamA", "CamB"])
    connection = sqlite3.connect(str(config.get_library_path()) + "/astrometrics.db")
    connection.execute(
        "INSERT INTO quality_calibration_identity (run_id, subject_id, target_id, camera, recorded_at) "
        "VALUES ('run-9', 'M 31/stack', 'M 31', 'CamA', '2026-10-02T00:00:00')"
    )
    connection.commit()
    connection.close()
    after = get_camera_data_versions(config, ["CamA", "CamB"])
    assert before is not None and after is not None
    assert after["CamA"] != before["CamA"]
    assert after["CamB"] == before["CamB"]


def test_the_cameras_of_a_target_come_from_its_measurements(config: AppConfiguration) -> None:
    """A target's cameras are those named by its rows' capture settings."""
    assert get_target_camera_names(config, "M 31") == ()
    store = QualityStore.from_configuration(config)
    store.write_measurements([
        _camera_measurement(store, "CamB", "M 31", "run-1"),
        _camera_measurement(store, "CamA", "M 31", "run-2"),
        _camera_measurement(store, "CamC", "M 13", "run-3"),
    ])
    assert get_target_camera_names(config, "M 31") == ("CamA", "CamB")
    assert get_target_camera_names(config, "Nothing") == ()


def test_unreadable_camera_data_gives_no_version(config: AppConfiguration) -> None:
    """A file that is not a database gives no version and no cameras."""
    (Path(str(config.get_library_path())) / "astrometrics.db").write_text("not a database")
    assert get_camera_data_versions(config, ["CamA"]) is None
    assert get_target_camera_names(config, "M 31") is None


def test_the_stack_version_ignores_frame_rows_and_sees_stack_rows(config: AppConfiguration) -> None:
    """A frame row leaves the stack version; a stack row changes it."""
    store = QualityStore.from_configuration(config)
    store.write_measurements([_camera_measurement(store, "CamA", "M 31", "run-1")])
    before = get_stack_quality_data_version(config)
    assert before is not None
    assert get_stack_quality_data_version(config) == before

    store.write_measurements([_camera_measurement(store, "CamA", "M 13", "run-2")])
    assert get_stack_quality_data_version(config) == before

    stack_row = _camera_measurement(store, "CamA", "M 31", "run-3")
    stack_row = stack_row.model_copy(
        update={"subject": stack_row.subject.model_copy(update={"level": SubjectLevel.STACK})}
    )
    store.write_measurements([stack_row])
    after = get_stack_quality_data_version(config)
    assert after is not None
    assert after != before


def test_the_stack_version_sees_a_new_capture_setting(config: AppConfiguration) -> None:
    """The settings the stack rows point at are part of the version."""
    store = QualityStore.from_configuration(config)
    store.write_measurements([_camera_measurement(store, "CamA", "M 31", "run-1")])
    before = get_stack_quality_data_version(config)
    store.get_or_create_capture_setting(CaptureSetting(camera="CamB"))
    assert get_stack_quality_data_version(config) != before


def test_the_stack_version_of_a_missing_library_is_empty(tmp_path: Path) -> None:
    """No database file gives zeros."""
    empty = AppConfiguration()
    empty.update_config({"Image Library": {"path": str(tmp_path / "nothing")}})
    assert get_stack_quality_data_version(empty) == (0, 0, "", 0, "", 0)
