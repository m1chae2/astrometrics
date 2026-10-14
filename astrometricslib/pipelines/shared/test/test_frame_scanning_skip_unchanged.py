"""Tests that re-indexing skips frames whose file has not changed.

A frame's size and modification time are stored when its header is read. A
later re-index that finds the same values reuses the record and does not open
the file, which matters when the files sit on a network drive.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared import frame_scanning, target_records


@pytest.fixture
def header_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the header readers with counters.

    Returns
    -------
    reads : `list` of `str`
        The paths whose headers were read, in order.
    """
    reads: list[str] = []

    def fake_create(path: str, camera: str | None = None, config: object = None) -> FrameRecord:
        """Count the read.

        Returns
        -------
        record : `FrameRecord`
            A bare record.
        """
        reads.append(path)
        return FrameRecord(path=path)

    def fake_refresh(frame: FrameRecord) -> bool:
        """Count the read.

        Returns
        -------
        refreshed : `bool`
            Always `True`.
        """
        reads.append(frame.path)
        return True

    monkeypatch.setattr(frame_scanning, "create_frame_record_from_fits", fake_create)
    monkeypatch.setattr(frame_scanning, "refresh_acquisition_conditions", fake_refresh)
    monkeypatch.setattr(frame_scanning, "_read_header_or_none", lambda path: None)
    return reads


def _make_target_folder(tmp_path: Path) -> Path:
    """Create the folder a target named "M 1" is scanned from.

    Returns
    -------
    folder : `Path`
        The new folder.
    """
    folder = tmp_path / "lights" / "M 1"
    folder.mkdir(parents=True)
    return folder


def _scan(target: Target, tmp_path: Path) -> None:
    """Run a refreshing scan of the target."""
    frame_scanning.scan_target_directory(target, str(tmp_path), refresh_headers=True)


def test_unchanged_frames_are_not_opened_again(tmp_path: Path, header_reads: list[str]) -> None:
    """A second scan with no change reads nothing."""
    folder = _make_target_folder(tmp_path)
    (folder / "a.fits").write_bytes(b"aaaa")
    target = Target(id="M 1")
    _scan(target, tmp_path)
    assert len(header_reads) == 1
    assert target.frames[0].file_size == 4
    header_reads.clear()
    _scan(target, tmp_path)
    assert header_reads == []


def test_changed_file_is_read_again(tmp_path: Path, header_reads: list[str]) -> None:
    """A file whose size changed is read again and its new size stored."""
    folder = _make_target_folder(tmp_path)
    frame_file = folder / "a.fits"
    frame_file.write_bytes(b"aaaa")
    target = Target(id="M 1")
    _scan(target, tmp_path)
    header_reads.clear()
    frame_file.write_bytes(b"aaaaaa")
    _scan(target, tmp_path)
    assert header_reads == [str(frame_file)]
    assert target.frames[0].file_size == 6


def test_modification_time_change_alone_is_read_again(tmp_path: Path, header_reads: list[str]) -> None:
    """A file with the same size but a new modification time is read again."""
    folder = _make_target_folder(tmp_path)
    frame_file = folder / "a.fits"
    frame_file.write_bytes(b"aaaa")
    target = Target(id="M 1")
    _scan(target, tmp_path)
    header_reads.clear()
    state = frame_file.stat()
    os.utime(frame_file, ns=(state.st_atime_ns, state.st_mtime_ns + 5_000_000_000))
    _scan(target, tmp_path)
    assert header_reads == [str(frame_file)]


def test_new_file_is_read_and_old_one_is_not(tmp_path: Path, header_reads: list[str]) -> None:
    """Only the newly added file is read."""
    folder = _make_target_folder(tmp_path)
    (folder / "a.fits").write_bytes(b"aaaa")
    target = Target(id="M 1")
    _scan(target, tmp_path)
    header_reads.clear()
    (folder / "b.fits").write_bytes(b"bb")
    _scan(target, tmp_path)
    assert header_reads == [str(folder / "b.fits")]
    assert len(target.frames) == 2


def test_deleted_file_is_dropped_by_pruning(tmp_path: Path, header_reads: list[str]) -> None:
    """A deleted file leaves the list when missing files are pruned."""
    folder = _make_target_folder(tmp_path)
    (folder / "a.fits").write_bytes(b"aaaa")
    (folder / "b.fits").write_bytes(b"bb")
    target = Target(id="M 1")
    _scan(target, tmp_path)
    (folder / "b.fits").unlink()
    catalog_access = SimpleNamespace(
        exists=lambda kind, query: os.path.exists(query["path"]),
        config=SimpleNamespace(get_frames_path=lambda: str(tmp_path)),
    )
    header_reads.clear()
    target_records.reindex_frames(
        target, prune_missing=True, catalog_access=catalog_access, refresh_headers=True
    )
    assert [os.path.basename(frame.path) for frame in target.frames] == ["a.fits"]
    assert header_reads == []


def test_old_record_is_read_once_then_reused(tmp_path: Path, header_reads: list[str]) -> None:
    """A record without stored size and time is read once, then reused."""
    folder = _make_target_folder(tmp_path)
    frame_file = folder / "a.fits"
    frame_file.write_bytes(b"aaaa")
    old_record = FrameRecord.model_validate({"path": str(frame_file)})
    assert old_record.file_size is None
    target = Target(id="M 1", frames=[old_record])
    _scan(target, tmp_path)
    assert header_reads == [str(frame_file)]
    header_reads.clear()
    _scan(target, tmp_path)
    assert header_reads == []


def test_new_fields_survive_a_round_trip() -> None:
    """The size and time are saved under camelCase names and load back."""
    record = FrameRecord(path="x", fileSize=7, fileModifiedNs=9)
    dumped = record.model_dump(by_alias=True)
    assert dumped["fileSize"] == 7
    assert FrameRecord.model_validate(dumped).file_modified_ns == 9
