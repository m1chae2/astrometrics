"""Lists the frames in a target's `_excluded` folders, with their reasons.

The set-aside step (`frame_set_aside.py`) moves frames with clouds or
trailed stars into an `_excluded` folder and writes two records of the move:

- the folder's `excluded_frames.json` file, and
- the frame's `frame.set_aside_*` quality rows, which carry the action taken.

Frames moved before the rows carried the action have only the file. Frames
put into the folder by hand have neither. This module reads the folders and the
stored rows and says, for each frame, what is known and where it came from.

It is read-only: nothing is moved, written or created. It is the library's
answer to "which frames were set aside, and why?" for callers who must not
walk the folders themselves.
"""

import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.models.quality.excluded_frames import SetAsideFrameRecord
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
from astrometricslib.pipelines.shared.set_aside_path import SET_ASIDE_FOLDER_NAME
from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import (
    MANIFEST_FILE_NAME,
    parse_set_aside_action,
    read_set_aside_manifest,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["build_set_aside_records", "clear_set_aside_cache", "find_excluded_folders"]

_FRAME_EXTENSIONS = (".fits", ".fit")
"""File name endings that mark a frame (the library's frame scan uses them)."""


def find_excluded_folders(frames_path: str, target_names: Iterable[str]) -> list[str]:
    """Find every `_excluded` folder under a target's lights folders.

    Unlike `frame_set_aside.find_set_aside_folders`, a folder is listed
    even when it has no `excluded_frames.json` file.

    Parameters
    ----------
    frames_path : `str`
        The library's frames folder.
    target_names : `Iterable` [`str`]
        The folder names the target's lights may lie under.

    Returns
    -------
    folders : `list` [`str`]
        The folders found, in walk order. An empty list if there are none.
    """
    folders: list[str] = []
    for name in target_names:
        root = os.path.join(frames_path, "lights", name)
        if not os.path.isdir(root):
            continue
        folders.extend(
            directory
            for directory, _, _ in os.walk(root)
            if os.path.basename(directory) == SET_ASIDE_FOLDER_NAME
        )
    return folders


@dataclass(frozen=True)
class _FrameHeaderFacts:
    """What one set-aside frame's header says, before any setting is applied.

    Attributes
    ----------
    exposure_s : `float` or `None`
        ``EXPTIME`` (or ``EXPOSURE``), or `None` when the header has none.
    camera_text : `str`
        The camera as the header writes it, ``"Unknown"`` when it names none.
        The record spelling is applied on each listing, because it depends
        on the camera settings, which can change while a file does not.
    filter_name : `str`
        The filter name a frame record shows (``"None"`` when unknown).
    date : `str`
        The capture time as ``YYYY-MM-DD HH:MM:SS``, from the header, else
        from the file name. When neither gives one, or the header cannot be
        read, it is the frame record's own stand-in (``"Unknown"``).
    readable : `bool`
        False when the header could not be read. The camera then shows as
        unknown, as it does on a frame record.
    """

    exposure_s: float | None
    camera_text: str
    filter_name: str
    date: str
    readable: bool


@dataclass(frozen=True)
class _FolderSnapshot:
    """The parts of one `_excluded` folder that come from its files.

    Attributes
    ----------
    state : `tuple`
        The sorted ``(name, size, modification time in ns)`` of every file in
        the folder when it was read. A different state means the folder must
        be read again.
    manifest : `dict` [`str`, `dict`]
        The folder's `excluded_frames.json` entries by file name.
    frames : `dict` [`str`, `_FrameHeaderFacts`]
        The header facts of each frame file, by file name.
    """

    state: tuple[tuple[str, int, int], ...]
    manifest: dict[str, dict[str, Any]]
    frames: dict[str, _FrameHeaderFacts]


_UNKNOWN_DATE: str = FrameRecord.model_fields["date"].default
"""The capture time a frame record shows when none is known."""

_SNAPSHOT_CACHE_SIZE = 64
"""The most `_excluded` folders whose snapshots are kept in memory.

A target has one or a few such folders, and each snapshot holds a few short
strings per frame, so 64 covers every target opened in a session for well
under a megabyte.
"""

_snapshot_cache: OrderedDict[str, _FolderSnapshot] = OrderedDict()
_snapshot_cache_lock = threading.Lock()


def clear_set_aside_cache() -> None:
    """Forget every kept folder snapshot, so the next listing reads files."""
    with _snapshot_cache_lock:
        _snapshot_cache.clear()


def _folder_state(folder: str) -> tuple[tuple[str, int, int], ...]:
    """Describe a folder's files cheaply, without opening any of them.

    Parameters
    ----------
    folder : `str`
        The folder.

    Returns
    -------
    state : `tuple`
        The sorted ``(name, size, modification time in ns)`` of each file.
        Adding, removing, rewriting or touching a file changes it.
    """
    state: list[tuple[str, int, int]] = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file():
                status = entry.stat()
                state.append((entry.name, status.st_size, status.st_mtime_ns))
    return tuple(sorted(state))


def _read_frame_header_facts(path: str) -> _FrameHeaderFacts:
    """Read a frame's exposure, camera, filter name and capture time.

    The header is read once, without the pixels or a sky map. The filter
    and capture time go through the same helpers as the target's frame
    records (`frame_scanning.create_frame_record_from_fits`), so a
    set-aside frame shows the same values a normal frame does.

    Parameters
    ----------
    path : `str`
        The frame file.

    Returns
    -------
    facts : `_FrameHeaderFacts`
        What the header says. A file that cannot be read gives what its
        frame record gives: no exposure, and an unknown camera, filter and
        capture time.
    """
    from astrometricslib._internal.drivers.filter_detection import get_filter_type
    from astrometricslib._internal.drivers.image import read_header_with_fixes, timestamp_from_header
    from astrometricslib.foundation.enums import FilterType
    from astrometricslib.pipelines.shared.frame_scanning import frame_capture_date, header_camera_text

    file_name = os.path.basename(path)
    header = read_header_with_fixes(path)
    if header is None:
        logger.debug("Cannot read the header of '%s'", path)
        return _FrameHeaderFacts(None, "Unknown", "None", _UNKNOWN_DATE, False)
    try:
        exposure_s = None
        for card in ("EXPTIME", "EXPOSURE"):
            value = header.get(card)
            if isinstance(value, int | float):
                exposure_s = float(value)
                break
        filter_type = get_filter_type(header)
        date = frame_capture_date(timestamp_from_header(header), file_name) or _UNKNOWN_DATE
        camera_text = header_camera_text(header)
    except (*FITS_READ_ERRORS, *DATA_ERRORS) as error:
        logger.debug("Cannot read the header of '%s': %s", path, error)
        return _FrameHeaderFacts(None, "Unknown", "None", _UNKNOWN_DATE, False)
    filter_name = "None" if filter_type == FilterType.NONE else filter_type.name
    return _FrameHeaderFacts(exposure_s, camera_text, filter_name, date, True)


def _folder_snapshot(folder: str) -> _FolderSnapshot:
    """Give a folder's manifest and frame header facts, reading only if needed.

    The answer is kept in memory with the folder's state. While no file in
    the folder is added, removed or changed, the kept answer is given and
    no file is opened. The state is taken before the files are read, so a
    file that changes during the read makes the next call read again.

    Parameters
    ----------
    folder : `str`
        An `_excluded` folder.

    Returns
    -------
    snapshot : `_FolderSnapshot`
        The folder's manifest entries and frame header facts.
    """
    key = os.path.abspath(folder)
    state = _folder_state(folder)
    with _snapshot_cache_lock:
        kept = _snapshot_cache.get(key)
        if kept is not None and kept.state == state:
            _snapshot_cache.move_to_end(key)
            return kept
    manifest = {
        entry["file"]: entry for entry in read_set_aside_manifest(os.path.join(folder, MANIFEST_FILE_NAME))
    }
    frames = {
        name: _read_frame_header_facts(os.path.join(folder, name))
        for name, _, _ in state
        if name.lower().endswith(_FRAME_EXTENSIONS)
    }
    snapshot = _FolderSnapshot(state=state, manifest=manifest, frames=frames)
    with _snapshot_cache_lock:
        _snapshot_cache[key] = snapshot
        _snapshot_cache.move_to_end(key)
        while len(_snapshot_cache) > _SNAPSHOT_CACHE_SIZE:
            _snapshot_cache.popitem(last=False)
    return snapshot


def build_set_aside_records(
    target_id: str,
    folders: Sequence[str],
    stored_rows: Sequence[QualityMeasurement],
    capture_exposure_seconds: dict[int, float | None],
) -> list[SetAsideFrameRecord]:
    """Describe each frame in the `_excluded` folders.

    Parameters
    ----------
    target_id : `str`
        The target's id.
    folders : `Sequence` [`str`]
        The `_excluded` folders (see `find_excluded_folders`).
    stored_rows : `Sequence` [`QualityMeasurement`]
        The target's stored ``frame.set_aside_*`` rows, newest per subject
        and metric. Only rows with a set-aside action count.
    capture_exposure_seconds : `dict` [`int`, `float` or `None`]
        The exposure of each capture setting id that the rows point at.

    Returns
    -------
    records : `list` [`SetAsideFrameRecord`]
        One record per frame file found, in folder order. A frame with a
        stored row takes its reason from the row; else from the manifest;
        else the reason stays empty and the source is ``folder_only``.
    """
    rows_by_subject: dict[str, QualityMeasurement] = {}
    for row in stored_rows:
        if parse_set_aside_action(row.action_taken) is None:
            continue
        existing = rows_by_subject.get(row.subject.subject_id)
        if existing is None or row.measured_at > existing.measured_at:
            rows_by_subject[row.subject.subject_id] = row

    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.shared.frame_scanning import frame_camera_name

    config = get_configuration()
    records: list[SetAsideFrameRecord] = []
    for folder in folders:
        snapshot = _folder_snapshot(folder)
        manifest = snapshot.manifest
        for file_name, facts in snapshot.frames.items():
            path = os.path.join(folder, file_name)
            entry = manifest.get(file_name)
            original_path = entry["original_path"] if entry else None
            subject_id = frame_subject_id(original_path, target_id) if original_path else None
            row = rows_by_subject.get(subject_id) if subject_id else None
            kind: str | None = None
            reason = ""
            action_taken = None
            capture_setting_id = None
            exposure_s = None
            moved_at = entry.get("moved_at") if entry else None
            if entry:
                kind, reason = entry["kind"], entry["reason"]
            reason_source = "manifest" if entry else "folder_only"
            if row is not None:
                parsed = parse_set_aside_action(row.action_taken)
                if parsed is not None:
                    kind, reason = parsed
                reason_source = "stored_row"
                action_taken = row.action_taken
                capture_setting_id = row.subject.capture_setting_id
                if capture_setting_id is not None:
                    exposure_s = capture_exposure_seconds.get(capture_setting_id)
                moved_at = moved_at or row.measured_at.isoformat(timespec="seconds")
            if exposure_s is None:
                exposure_s = facts.exposure_s
            camera = frame_camera_name(facts.camera_text, config) if facts.readable else "Unknown"
            camera = "" if camera == "Unknown" else camera
            filter_name, date = facts.filter_name, facts.date
            records.append(
                SetAsideFrameRecord(
                    file=file_name,
                    path=path,
                    subject_id=subject_id,
                    original_path=original_path,
                    kind=kind,
                    reason=reason,
                    reason_source=reason_source,
                    action_taken=action_taken,
                    exposure_s=exposure_s,
                    capture_setting_id=capture_setting_id,
                    moved_at=moved_at,
                    camera=camera,
                    filter=filter_name,
                    date=date,
                    restorable=entry is not None,
                    restore_note=(
                        ""
                        if entry is not None
                        else "No excluded_frames.json entry records where this frame came from, "
                        "so it cannot be restored automatically. Move it back by hand."
                    ),
                )
            )
    return records
