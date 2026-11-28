"""Purpose: Check the kept frame measurements against a real quality store.

Description: The capture history reads four metrics of every frame in the
library (`read_frame_measurements`). A cache keeps them as a
`FrameMeasurements` table and brings it up to date by reading only the rows
added since (`update_frame_measurements`). These tests write a small quality
database with the science library's own store, add rows to it the way a
live night does, and check that the kept table brought up to date equals a
new full read. They also check that a removed row, a reused row number, a
row measured before the kept newest time, or rows that changed while they
were read make the update give up, so the caller reads everything again.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrometricslib import CaptureSetting
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib._internal.quality import MeasurementOrigin, QualitySubject
from astrometricslib.api.quality import QualityMeasurements
from astrometricslib.drivers import get_frame_metric_data_versions
from astrometricslib.reports import MeasurementStatus, QualityMeasurement, SubjectLevel
from wayfindinglib.analytics.capture_history import (
    BACKGROUND_METRIC,
    PINNED_FRAME_METRICS,
    SATURATED_FRACTION_METRIC,
    STAR_ELONGATION_METRIC,
    STAR_WIDTH_METRIC,
    FrameMeasurements,
    read_frame_measurement_table,
    read_frame_measurements,
    update_frame_measurements,
)

START = datetime(2026, 11, 1, 6, tzinfo=UTC)
"""When the first made-up frame was measured."""


def _measurement(
    metric: str,
    subject: str,
    value: float | None,
    minutes: int,
    *,
    target_id: str = "M 13",
    version: int = 1,
    channel: str | None = None,
    level: SubjectLevel = SubjectLevel.FRAME,
) -> QualityMeasurement:
    """Build one stored measurement.

    Parameters
    ----------
    metric : `str`
        The metric name.
    subject : `str`
        The subject id.
    value : `float` or `None`
        The value; `None` makes a not-measured row.
    minutes : `int`
        When it was measured, in minutes after `START`. It also names the run.
    target_id, version, channel, level
        The other fields of the row.

    Returns
    -------
    measurement : `QualityMeasurement`
        The row, ready to write.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=version,
        subject=QualitySubject(level=level, subject_id=subject, target_id=target_id, channel=channel),
        status=MeasurementStatus.MEASURED if value is not None else MeasurementStatus.NOT_MEASURED,
        value=value,
        reason="" if value is not None else "no stars found",
        origin=MeasurementOrigin.MEASURED,
        run_id=f"run-{minutes}",
        measured_at=START + timedelta(minutes=minutes),
    )


def _frame(
    subject: str, minutes: int, target_id: str = "M 13", elongation: float = 1.2
) -> list[QualityMeasurement]:
    """Build the four pinned rows of one measured frame.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        Width, elongation, saturation and background rows.
    """
    return [
        _measurement(STAR_WIDTH_METRIC, subject, 2.4, minutes, target_id=target_id),
        _measurement(STAR_ELONGATION_METRIC, subject, elongation, minutes, target_id=target_id),
        _measurement(SATURATED_FRACTION_METRIC, subject, 0.0, minutes, target_id=target_id),
        _measurement(BACKGROUND_METRIC, subject, 110.0, minutes, target_id=target_id),
    ]


def _library(tmp_path: Path) -> tuple[Any, QualityStore, Any]:
    """Write a made-up library of two targets and read its table once.

    Returns
    -------
    astrometrics, store, read_version
        The stand-in library (the real quality reader), the store to add
        rows to, and a function that reads the pinned metrics' version.
    """
    library = tmp_path / "library"
    library.mkdir()
    store = QualityStore(str(library / "astrometrics.db"))
    store.get_or_create_capture_setting(CaptureSetting(camera="CamA"))
    rows = [
        *_frame("M 13/a.fits", 1),
        *_frame("M 31/b.fits", 2, target_id="M 31"),
        # An older elongation that a newer row replaces, a color plane and
        # a row at another definition version.
        _measurement(STAR_ELONGATION_METRIC, "M 13/c.fits", 1.9, 3),
        _measurement(STAR_ELONGATION_METRIC, "M 13/c.fits", 1.3, 4),
        _measurement(STAR_WIDTH_METRIC, "M 13/c.fits", 2.9, 4, channel="R"),
        _measurement(BACKGROUND_METRIC, "M 13/c.fits", 99.0, 4, version=7),
        # A stack row and a metric the history does not read.
        _measurement("stacking.star_elongation", "M 13/stack.fits", 1.05, 5, level=SubjectLevel.STACK),
        _measurement("frame.sky_noise_e", "M 13/a.fits", 12.0, 5),
    ]
    store.write_measurements(rows)
    config = SimpleNamespace(get_library_path=lambda: str(library))
    astrometrics = SimpleNamespace(quality=SimpleNamespace(records=QualityMeasurements(config)))

    def read_version() -> Any:
        """Read the version of the pinned metrics.

        Returns
        -------
        version : `Any`
            See `get_frame_metric_data_versions`.
        """
        return get_frame_metric_data_versions(config, tuple(PINNED_FRAME_METRICS))

    return astrometrics, store, read_version


def _full(astrometrics: Any, read_version: Any) -> FrameMeasurements:
    """Read the table from scratch.

    Returns
    -------
    measurements : `FrameMeasurements`
        A full read.
    """
    return read_frame_measurement_table(astrometrics, read_version)


def test_the_table_holds_what_read_frame_measurements_gives(tmp_path: Path) -> None:
    """The kept table's numbers are the capture history's usual read."""
    astrometrics, _, read_version = _library(tmp_path)

    table = _full(astrometrics, read_version)

    assert table.values == read_frame_measurements(astrometrics)
    assert table.version == read_version()
    assert {metric: value for metric, (value, _) in table.values["M 13/c.fits"].items()} == {
        STAR_ELONGATION_METRIC: 1.3
    }
    assert set(table.marks) == set(PINNED_FRAME_METRICS)


def test_added_rows_merged_into_the_table_equal_a_full_read(tmp_path: Path) -> None:
    """Rows of a live night, merged in, give exactly what a full read gives.

    The added rows cover a new frame, a newer row that replaces an older
    one, a newer not-measured row that hides a value, a row at another
    definition version, a color plane and another target's frame.
    """
    astrometrics, store, read_version = _library(tmp_path)
    kept = _full(astrometrics, read_version)
    kept_values = {subject: dict(values) for subject, values in kept.values.items()}
    store.write_measurements([
        *_frame("M 13/new.fits", 60),
        _measurement(STAR_ELONGATION_METRIC, "M 13/a.fits", 1.6, 61),
        _measurement(STAR_WIDTH_METRIC, "M 31/b.fits", None, 61, target_id="M 31"),
        _measurement(BACKGROUND_METRIC, "M 13/a.fits", 80.0, 61, version=9),
        _measurement(STAR_WIDTH_METRIC, "M 13/new.fits", 9.9, 61, channel="G"),
        *_frame("M 57/x.fits", 62, target_id="M 57"),
    ])

    updated = update_frame_measurements(astrometrics, kept, read_version())
    fresh = _full(astrometrics, read_version)

    assert updated is not None
    assert updated == fresh
    assert updated.values == read_frame_measurements(astrometrics)
    assert STAR_WIDTH_METRIC not in updated.values["M 31/b.fits"]
    assert BACKGROUND_METRIC not in updated.values["M 13/a.fits"]
    assert {subject: dict(values) for subject, values in kept.values.items()} == kept_values


def test_updating_frame_by_frame_still_equals_a_full_read(tmp_path: Path) -> None:
    """An updated table can be brought up to date again, frame by frame."""
    astrometrics, store, read_version = _library(tmp_path)
    kept = _full(astrometrics, read_version)

    for number in range(4):
        store.write_measurements(_frame(f"M 13/n{number}.fits", 70 + number))
        updated = update_frame_measurements(astrometrics, kept, read_version())
        assert updated is not None
        assert updated == _full(astrometrics, read_version)
        kept = updated


def test_rows_of_one_metric_alone_are_merged(tmp_path: Path) -> None:
    """A metric whose rows did not change keeps its part as it was."""
    astrometrics, store, read_version = _library(tmp_path)
    kept = _full(astrometrics, read_version)
    store.write_measurements([_measurement(STAR_ELONGATION_METRIC, "M 13/a.fits", 1.7, 80)])

    updated = update_frame_measurements(astrometrics, kept, read_version())

    assert updated == _full(astrometrics, read_version)


def test_a_deleted_frame_makes_the_update_give_up(tmp_path: Path) -> None:
    """Rows removed (a deleted frame) as rows were added need a full read."""
    astrometrics, store, read_version = _library(tmp_path)
    kept = _full(astrometrics, read_version)
    store.delete_frame_measurements(["M 31/b.fits"])
    store.write_measurements(_frame("M 13/new.fits", 60))

    assert update_frame_measurements(astrometrics, kept, read_version()) is None


def test_a_removed_newest_row_whose_number_is_used_again_makes_it_give_up(tmp_path: Path) -> None:
    """When the kept newest row is gone, its row number may be used again."""
    astrometrics, store, read_version = _library(tmp_path)
    store.write_measurements(_frame("M 13/e.fits", 60))
    kept = _full(astrometrics, read_version)
    store.delete_frame_measurements(["M 13/e.fits"])
    store.write_measurements(_frame("M 13/f.fits", 61, elongation=1.8))

    assert update_frame_measurements(astrometrics, kept, read_version()) is None


def test_a_row_measured_before_the_kept_newest_time_makes_it_give_up(tmp_path: Path) -> None:
    """A row added late with an old time is missed, so the update gives up."""
    astrometrics, store, read_version = _library(tmp_path)
    kept = _full(astrometrics, read_version)
    store.write_measurements([_measurement(STAR_ELONGATION_METRIC, "M 13/a.fits", 1.6, 0)])

    assert update_frame_measurements(astrometrics, kept, read_version()) is None


def test_a_metric_with_no_rows_before_makes_it_give_up(tmp_path: Path) -> None:
    """A metric that had no rows has no kept newest row to check against."""
    library = tmp_path / "library"
    library.mkdir()
    store = QualityStore(str(library / "astrometrics.db"))
    store.write_measurements([_measurement(STAR_WIDTH_METRIC, "M 13/a.fits", 2.4, 1)])
    config = SimpleNamespace(get_library_path=lambda: str(library))
    astrometrics = SimpleNamespace(quality=SimpleNamespace(records=QualityMeasurements(config)))

    def read_version() -> Any:
        """Read the version of the pinned metrics.

        Returns
        -------
        version : `Any`
            See `get_frame_metric_data_versions`.
        """
        return get_frame_metric_data_versions(config, tuple(PINNED_FRAME_METRICS))

    kept = _full(astrometrics, read_version)
    store.write_measurements([_measurement(STAR_ELONGATION_METRIC, "M 13/a.fits", 1.2, 2)])

    assert update_frame_measurements(astrometrics, kept, read_version()) is None


def test_no_new_rows_keeps_the_numbers(tmp_path: Path) -> None:
    """With no row added, the update gives the same numbers."""
    astrometrics, _, read_version = _library(tmp_path)
    kept = _full(astrometrics, read_version)

    assert update_frame_measurements(astrometrics, kept, read_version()) == kept


def test_rows_read_while_the_data_changed_record_no_version(tmp_path: Path) -> None:
    """When the version changed while reading, the table cannot be updated."""
    astrometrics, _, read_version = _library(tmp_path)
    real = read_version()
    versions = iter([
        real,
        {**real, STAR_WIDTH_METRIC: real[STAR_WIDTH_METRIC]._replace(measurement_count=99)},
    ])

    table = read_frame_measurement_table(astrometrics, lambda: next(versions))

    assert table.version is None
    assert update_frame_measurements(astrometrics, table, real) is None
