"""Purpose: Check the capture history's frame reads against a real store.

Description: The capture history reads four metrics of every frame in the
library (`read_frame_measurements`) and keeps them with the science
library's opaque data version of those rows (`frame_measurement_version`).
The science library keeps its own reads and brings them up to date with
only the rows written since. These tests write a small quality database
with the science library's own store, add rows to it the way a live night
does, and check that a read after the change equals a fresh read with
nothing kept, and that the version changes exactly when the rows the
capture history reads change.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import CaptureSetting
from astrometricslib._internal.drivers import quality_store
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib._internal.quality import MeasurementOrigin, QualitySubject
from astrometricslib.api.quality import QualityMeasurements
from astrometricslib.reports import MeasurementStatus, QualityMeasurement, SubjectLevel
from wayfindinglib.analytics.capture_history import (
    BACKGROUND_METRIC,
    PINNED_FRAME_METRICS,
    SATURATED_FRACTION_METRIC,
    STAR_ELONGATION_METRIC,
    STAR_WIDTH_METRIC,
    frame_measurement_version,
    read_frame_measurements,
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


@pytest.fixture(autouse=True)
def keep_every_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the science library keep reads of any size, starting with none."""
    monkeypatch.setattr(quality_store, "LATEST_READ_MINIMUM_ROWS", 1)
    quality_store._LATEST_READS.clear()


def _library(tmp_path: Path) -> tuple[Any, QualityStore]:
    """Write a made-up library of two targets.

    Returns
    -------
    astrometrics, store
        The stand-in library (the real quality reader) and the store to add
        rows to.
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
    return astrometrics, store


def _fresh(astrometrics: Any) -> dict[str, Any]:
    """Read the numbers with nothing kept by the science library.

    Returns
    -------
    measurements : `dict`
        A full read.
    """
    quality_store._LATEST_READS.clear()
    return read_frame_measurements(astrometrics)


def test_the_read_picks_the_newest_usable_whole_frame_rows(tmp_path: Path) -> None:
    """A newer row wins; a color plane and another version are left out."""
    astrometrics, _ = _library(tmp_path)

    values = read_frame_measurements(astrometrics)

    assert set(values) == {"M 13/a.fits", "M 31/b.fits", "M 13/c.fits"}
    assert {metric: value for metric, (value, _) in values["M 13/c.fits"].items()} == {
        STAR_ELONGATION_METRIC: 1.3
    }
    assert set(values["M 13/a.fits"]) == set(PINNED_FRAME_METRICS)


def test_a_read_after_a_live_night_equals_a_fresh_read(tmp_path: Path) -> None:
    """Rows of a live night, read through the kept reads, equal a fresh read.

    The added rows cover a new frame, a newer row that replaces an older
    one, a newer not-measured row that hides a value, a row at another
    definition version, a color plane and another target's frame.
    """
    astrometrics, store = _library(tmp_path)
    kept = read_frame_measurements(astrometrics)
    kept_copy = {subject: dict(values) for subject, values in kept.items()}
    store.write_measurements([
        *_frame("M 13/new.fits", 60),
        _measurement(STAR_ELONGATION_METRIC, "M 13/a.fits", 1.6, 61),
        _measurement(STAR_WIDTH_METRIC, "M 31/b.fits", None, 61, target_id="M 31"),
        _measurement(BACKGROUND_METRIC, "M 13/a.fits", 80.0, 61, version=9),
        _measurement(STAR_WIDTH_METRIC, "M 13/new.fits", 9.9, 61, channel="G"),
        *_frame("M 57/x.fits", 62, target_id="M 57"),
    ])

    updated = read_frame_measurements(astrometrics)

    assert updated == _fresh(astrometrics)
    assert STAR_WIDTH_METRIC not in updated["M 31/b.fits"]
    assert BACKGROUND_METRIC not in updated["M 13/a.fits"]
    assert {subject: dict(values) for subject, values in kept.items()} == kept_copy


def test_frame_by_frame_reads_and_a_deleted_frame_equal_a_fresh_read(tmp_path: Path) -> None:
    """Each read during a night, and after a deleted frame, is exact."""
    astrometrics, store = _library(tmp_path)
    read_frame_measurements(astrometrics)
    for number in range(4):
        store.write_measurements(_frame(f"M 13/n{number}.fits", 70 + number))
        assert read_frame_measurements(astrometrics) == _fresh(astrometrics)
    store.delete_frame_measurements(["M 31/b.fits"])
    store.write_measurements(_frame("M 13/after.fits", 80))
    after = read_frame_measurements(astrometrics)
    assert after == _fresh(astrometrics)
    assert "M 31/b.fits" not in after


def test_the_version_follows_only_the_rows_the_history_reads(tmp_path: Path) -> None:
    """A stack row or another metric leaves the version; a frame row does not.

    A deleted frame and a row added with an old time change it too, and an
    equal version always means an equal read.
    """
    astrometrics, store = _library(tmp_path)
    version = frame_measurement_version(astrometrics)
    assert all(isinstance(part, str) for part in version)
    assert frame_measurement_version(astrometrics) == version

    store.write_measurements([
        _measurement("stacking.star_elongation", "M 13/stack.fits", 1.1, 90, level=SubjectLevel.STACK),
        _measurement("frame.sky_noise_e", "M 13/c.fits", 11.0, 90),
    ])
    assert frame_measurement_version(astrometrics) == version

    store.write_measurements([_measurement(STAR_ELONGATION_METRIC, "M 13/a.fits", 1.6, 0)])
    late = frame_measurement_version(astrometrics)
    assert late != version

    store.delete_frame_measurements(["M 31/b.fits"])
    assert frame_measurement_version(astrometrics) != late


def test_an_unmeasured_frame_row_changes_the_version_but_not_its_numbers(tmp_path: Path) -> None:
    """A not-measured row moves the version but adds no number."""
    astrometrics, store = _library(tmp_path)
    version = frame_measurement_version(astrometrics)
    store.write_measurements([_measurement(STAR_WIDTH_METRIC, "M 13/none.fits", None, 95)])

    assert frame_measurement_version(astrometrics) != version
    assert "M 13/none.fits" not in read_frame_measurements(astrometrics)
