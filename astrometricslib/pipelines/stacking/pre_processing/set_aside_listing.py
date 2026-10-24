"""Lists the frames in a target's `_excluded` folders, with their reasons.

The quarantine step (`frame_quarantine.py`) moves frames with clouds or
trailed stars into an `_excluded` folder and writes two records of the move:

- the folder's `excluded_frames.json` file, and
- the frame's `frame.quarantine_*` quality rows, which carry the action taken.

Frames moved before the rows carried the action have only the file. Frames
put into the folder by hand have neither. This module reads the folders and the
stored rows and says, for each frame, what is known and where it came from.

It is read-only: nothing is moved, written or created. It is the library's
answer to "which frames were set aside, and why?" for callers who must not
walk the folders themselves.
"""

import logging
import os
from collections.abc import Iterable, Sequence

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib.models.quality.excluded_frames import SetAsideFrameRecord
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id
from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    MANIFEST_FILE_NAME,
    parse_set_aside_action,
    read_quarantine_manifest,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["build_set_aside_records", "find_excluded_folders"]

_FRAME_EXTENSIONS = (".fits", ".fit")
"""File name endings that mark a frame (the library's frame scan uses them)."""


def find_excluded_folders(frames_path: str, target_names: Iterable[str]) -> list[str]:
    """Find every `_excluded` folder under a target's lights folders.

    Unlike `frame_quarantine.find_quarantine_folders`, a folder is listed
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
            if os.path.basename(directory) == QUARANTINE_FOLDER_NAME
        )
    return folders


def _header_exposure_seconds(path: str) -> float | None:
    """Read ``EXPTIME`` (or ``EXPOSURE``) from a frame's header.

    Parameters
    ----------
    path : `str`
        The frame file.

    Returns
    -------
    exposure_seconds : `float` or `None`
        The exposure, or `None` if the header has none or cannot be read.
    """
    try:
        header = read_header(path)
    except (*FITS_READ_ERRORS, *DATA_ERRORS, OSError) as error:
        logger.debug("Cannot read the header of '%s': %s", path, error)
        return None
    for card in ("EXPTIME", "EXPOSURE"):
        value = header.get(card)
        if isinstance(value, int | float):
            return float(value)
    return None


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
        The target's stored ``frame.quarantine_*`` rows, newest per subject
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

    records: list[SetAsideFrameRecord] = []
    for folder in folders:
        manifest = {
            entry["file"]: entry
            for entry in read_quarantine_manifest(os.path.join(folder, MANIFEST_FILE_NAME))
        }
        for file_name in sorted(os.listdir(folder)):
            if not file_name.lower().endswith(_FRAME_EXTENSIONS):
                continue
            path = os.path.join(folder, file_name)
            entry = manifest.get(file_name)
            original_path = entry["original_path"] if entry else None
            subject_id = frame_subject_id(original_path, target_id) if original_path else None
            row = rows_by_subject.get(subject_id) if subject_id else None
            kind: str | None = None
            reason = ""
            action_taken = None
            capture_setting_id = None
            exposure_seconds = None
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
                    exposure_seconds = capture_exposure_seconds.get(capture_setting_id)
                moved_at = moved_at or row.measured_at.isoformat(timespec="seconds")
            if exposure_seconds is None:
                exposure_seconds = _header_exposure_seconds(path)
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
                    exposure_seconds=exposure_seconds,
                    capture_setting_id=capture_setting_id,
                    moved_at=moved_at,
                )
            )
    return records
