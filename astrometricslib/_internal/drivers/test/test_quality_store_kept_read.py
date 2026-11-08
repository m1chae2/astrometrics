"""Tests for the kept read of a target's newest quality rows.

A read of one target's newest rows is kept and brought up to date by reading
only what was written since (`QualityStore.query`). These tests check that the
kept read always equals a full read, after new frames, a re-measured frame,
a frame that moved to another target, a deleted frame, a deleted newest row
whose row number is used again, and a database file that was replaced.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from astrometricslib._internal.drivers import quality_store as module
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)

BASE_TIME = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)
METRICS = ("frame.star_width_moffat_arcsec", "frame.sky_noise_e", "frame.read_noise_e")
TARGET = "M31"


@pytest.fixture(autouse=True)
def small_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every read, however small, and start with nothing kept."""
    monkeypatch.setattr(module, "LATEST_READ_MINIMUM_ROWS", 1)
    module._LATEST_READS.clear()


@pytest.fixture
def store(tmp_path: Path) -> QualityStore:
    """Provide a store on a temporary database.

    Returns
    -------
    store : `QualityStore`
        A fresh store.
    """
    return QualityStore(str(tmp_path / "astrometrics.db"))


def frame_rows(
    frame: str, target: str, run: str, minutes: float, value: float = 1.0
) -> list[QualityMeasurement]:
    """Build the rows of one frame in one run.

    Parameters
    ----------
    frame : `str`
        The frame's subject id.
    target : `str`
        The target the rows name.
    run : `str`
        The run id.
    minutes : `float`
        The measurement time, in minutes after a fixed start.
    value : `float`, optional
        The value of every row.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        One row for each metric.
    """
    return [
        QualityMeasurement(
            metric=metric,
            definition_version=1,
            subject=QualitySubject(level=SubjectLevel.FRAME, subject_id=frame, target_id=target),
            status=MeasurementStatus.MEASURED,
            value=value,
            origin=MeasurementOrigin.MEASURED,
            run_id=run,
            measured_at=BASE_TIME + timedelta(minutes=minutes),
        )
        for metric in METRICS
    ]


def full_read(store: QualityStore, target: str = TARGET) -> list[QualityMeasurement]:
    """Read a target's newest rows the slow way, without the kept read.

    Parameters
    ----------
    store : `QualityStore`
        The store.
    target : `str`, optional
        The target.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The newest library rows of the target: a read of every target's
        newest rows (which is never kept), cut to the target.
    """
    return [row for row in store.query() if row.subject.target_id == target and row.subject.variant is None]


def test_a_kept_read_follows_new_frames(store: QualityStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """New frames are read incrementally and match a full read."""
    store.write_measurements([
        *frame_rows("M31/a.fits", TARGET, "r1", 0),
        *frame_rows("M13/b.fits", "M13", "r1", 1),
    ])
    assert store.query(target_id=TARGET, library_only=True) == full_read(store)
    calls = []
    original = QualityStore._changed_subjects

    def spy(self: QualityStore, connection: object, kept: object) -> set[str] | None:
        """Record what the check for changed subjects found.

        Returns
        -------
        changed : `set` [`str`] or `None`
            What the real check found.
        """
        found = original(self, connection, kept)  # type: ignore[arg-type]
        calls.append(found)
        return found

    monkeypatch.setattr(QualityStore, "_changed_subjects", spy)
    store.write_measurements(frame_rows("M31/c.fits", TARGET, "r2", 5))
    assert store.query(target_id=TARGET, library_only=True) == full_read(store)
    for index in range(4):
        store.write_measurements(frame_rows(f"M31/n{index}.fits", TARGET, f"r3-{index}", 10 + index))
        store.write_measurements(frame_rows(f"M13/n{index}.fits", "M13", f"r3-{index}", 10 + index))
    assert store.query(target_id=TARGET, library_only=True) == full_read(store)
    assert calls == [
        {"M31/c.fits"},
        {f"M31/n{i}.fits" for i in range(4)} | {f"M13/n{i}.fits" for i in range(4)},
    ]


def test_a_kept_read_gives_copies(store: QualityStore) -> None:
    """Changing a returned row does not change the next read."""
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r1", 0))
    first = store.query(target_id=TARGET, library_only=True)
    first[0].unit = "changed"
    second = store.query(target_id=TARGET, library_only=True)
    assert second[0].unit != "changed"
    assert second == full_read(store)


def test_a_re_measured_frame_replaces_its_rows(store: QualityStore) -> None:
    """A restack's newer rows replace the old ones; an older row does not."""
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r1", 0, value=1.0))
    store.write_measurements(frame_rows("M31/b.fits", TARGET, "r1", 1, value=1.0))
    assert store.query(target_id=TARGET, library_only=True) == full_read(store)
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r2", 20, value=2.0))
    result = store.query(target_id=TARGET, library_only=True)
    assert result == full_read(store)
    assert {row.value for row in result if row.subject.subject_id == "M31/a.fits"} == {2.0}
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r3", 10, value=3.0))
    result = store.query(target_id=TARGET, library_only=True)
    assert result == full_read(store)
    assert {row.value for row in result if row.subject.subject_id == "M31/a.fits"} == {2.0}


def test_a_frame_that_moved_to_another_target_leaves_the_read(store: QualityStore) -> None:
    """Newer rows that name another target hide the old rows."""
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r1", 0))
    store.write_measurements(frame_rows("M31/b.fits", TARGET, "r1", 1))
    assert len(store.query(target_id=TARGET, library_only=True)) == 6
    store.write_measurements(frame_rows("M31/a.fits", "M13", "r2", 30))
    assert store.query(target_id=TARGET, library_only=True) == full_read(store)
    assert {row.subject.subject_id for row in store.query(target_id=TARGET, library_only=True)} == {
        "M31/b.fits"
    }
    assert store.query(target_id="M13", library_only=True) == full_read(store, "M13")


def test_a_deleted_frame_gives_a_full_read(store: QualityStore) -> None:
    """A deleted frame shows its older rows again, as a full read does."""
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r1", 0, value=1.0))
    store.write_measurements(frame_rows("M31/b.fits", TARGET, "r1", 1, value=1.0))
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r2", 20, value=2.0))
    assert store.query(target_id=TARGET, library_only=True) == full_read(store)
    assert store.delete_frame_measurements(["M31/a.fits"]) == 6
    after = store.query(target_id=TARGET, library_only=True)
    assert after == full_read(store)
    assert {row.subject.subject_id for row in after} == {"M31/b.fits"}


def test_a_deleted_older_frame_gives_a_full_read(store: QualityStore) -> None:
    """A frame deleted below the newest row leaves the read in step."""
    for index, frame in enumerate(["a", "b", "c"]):
        store.write_measurements(frame_rows(f"M31/{frame}.fits", TARGET, f"r{index}", index))
    assert len(store.query(target_id=TARGET, library_only=True)) == 9
    assert store.delete_frame_measurements(["M31/a.fits"]) == 3
    after = store.query(target_id=TARGET, library_only=True)
    assert after == full_read(store)
    assert {row.subject.subject_id for row in after} == {"M31/b.fits", "M31/c.fits"}


def test_a_deleted_newest_row_whose_number_is_used_again_is_noticed(store: QualityStore) -> None:
    """Row numbers used again after a delete still give the full answer."""
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r1", 0))
    store.write_measurements(frame_rows("M31/b.fits", TARGET, "r1", 1))
    assert store.query(target_id=TARGET, library_only=True) == full_read(store)
    store.delete_frame_measurements(["M31/b.fits"])
    store.write_measurements(frame_rows("M31/c.fits", TARGET, "r2", 2))
    result = store.query(target_id=TARGET, library_only=True)
    assert result == full_read(store)
    assert {row.subject.subject_id for row in result} == {"M31/a.fits", "M31/c.fits"}


def test_a_replaced_database_file_gives_a_full_read(tmp_path: Path) -> None:
    """A new file at the same path is not read through the old kept rows."""
    path = tmp_path / "astrometrics.db"
    store = QualityStore(str(path))
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r1", 0))
    store.write_measurements(frame_rows("M31/b.fits", TARGET, "r1", 1))
    assert len(store.query(target_id=TARGET, library_only=True)) == 6
    path.unlink()
    store.write_measurements(frame_rows("M31/x.fits", TARGET, "r9", 2))
    store.write_measurements(frame_rows("M31/y.fits", TARGET, "r9", 3))
    result = store.query(target_id=TARGET, library_only=True)
    assert result == full_read(store)
    assert {row.subject.subject_id for row in result} == {"M31/x.fits", "M31/y.fits"}


def test_the_kept_read_is_filtered_like_the_question(store: QualityStore) -> None:
    """Reads with different filters are kept apart."""
    store.write_measurements(frame_rows("M31/a.fits", TARGET, "r1", 0))
    metric = METRICS[0]
    assert len(store.query(target_id=TARGET, metric=metric)) == 1
    store.write_measurements(frame_rows("M31/b.fits", TARGET, "r2", 1))
    only = store.query(target_id=TARGET, metric=metric)
    assert [row.subject.subject_id for row in only] == ["M31/a.fits", "M31/b.fits"]
    assert len(store.query(target_id=TARGET)) == 6


@pytest.mark.parametrize("seed", range(6))
def test_random_changes_always_match_a_full_read(
    store: QualityStore, seed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After each of many mixed changes the kept read equals a full read."""
    incremental_reads = []
    original = QualityStore._changed_subjects

    def spy(self: QualityStore, connection: object, kept: object) -> set[str] | None:
        """Record whether the kept read could be brought up to date.

        Returns
        -------
        changed : `set` [`str`] or `None`
            What the real check found.
        """
        found = original(self, connection, kept)  # type: ignore[arg-type]
        incremental_reads.append(found is not None)
        return found

    monkeypatch.setattr(QualityStore, "_changed_subjects", spy)
    generator = np.random.default_rng(seed)
    owner: dict[str, str] = {}
    for step in range(40):
        action = str(generator.choice(["new", "new", "other", "again", "older", "move", "delete"]))
        if action == "new" or not owner:
            frame = f"M31/f{step}.fits"
            owner[frame] = TARGET
            store.write_measurements(frame_rows(frame, TARGET, f"r{step}", step))
        elif action == "other":
            store.write_measurements(frame_rows(f"M13/f{step}.fits", "M13", f"r{step}", step))
        elif action == "delete":
            frame = str(generator.choice(sorted(owner)))
            store.delete_frame_measurements([frame])
            del owner[frame]
        else:
            frame = str(generator.choice(sorted(owner)))
            if action == "move":
                owner[frame] = str(generator.choice(["M31", "M13"]))
            minutes = step + 100 if action != "older" else -step
            store.write_measurements(frame_rows(frame, owner[frame], f"r{step}", minutes, value=float(step)))
        for target in (TARGET, "M13"):
            assert store.query(target_id=target, library_only=True) == full_read(store, target), (seed, step)
    assert any(incremental_reads), "the incremental path was never used"
