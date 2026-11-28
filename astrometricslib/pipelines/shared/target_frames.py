"""Purpose: The owner functions of `TargetFrames`, a target's frame records.

Description: Each public method of ``Astrometrics.targets.frames`` is a thin
wrapper of one function here:

- `newest_frame` gives the frame taken last.
- `add_frame_files` brings new files into the library under a target: a file
  from outside the library is moved into the target's folder, a file already
  inside is only indexed. Every frame is then matched to its observatory
  configuration and the target is saved.
- `scan_frames` reads one target's folder, or every target folder, again.
- `read_frame_header` reads a FITS file's first header.
- `target_nights` groups a target's frames into observing sessions.
- `delete_frames` deletes frame files, drops their records and clears their
  stored quality measurements.

`stored_number` and `night_text` read a stored setting as a number and write
a night as text, for the frame query too.

The functions take the target catalog's state object (``catalog``), which
holds the settings (``_config``), the storage (``_catalog_access``) and the
targets held in memory, the way `target_records` does.
"""

import logging
import math
import os
from collections.abc import Sequence
from datetime import date
from typing import Any

from astropy.io import fits

from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.catalog_queries import ReindexReport, TargetReindexChange
from astrometricslib.models.image_deletion import FailedDeletion, ImageDeletionResult
from astrometricslib.models.observatory_configuration import ObservatoryConfiguration
from astrometricslib.models.page import Page
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared import target_records
from astrometricslib.pipelines.shared.target_sessions import TargetSession, derive_target_sessions

logger = logging.getLogger(__name__)

__all__ = [
    "add_frame_files",
    "delete_frames",
    "newest_frame",
    "night_text",
    "read_frame_header",
    "scan_frames",
    "stored_number",
    "target_nights",
]

FITS_SUFFIXES = (".fits", ".fit", ".fts")
"""File endings of a FITS frame."""


def stored_number(value: Any) -> float | None:
    """Read a stored setting (such as ``"800"`` or ``"300.0"``) as a number.

    Parameters
    ----------
    value : `Any`
        The stored value.

    Returns
    -------
    number : `float` or `None`
        The number, or `None` when the value is not one.
    """
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def night_text(night: str | date | None) -> str | None:
    """Write an observing night as ``YYYY-MM-DD``.

    Parameters
    ----------
    night : `str`, `date` or `None`
        The night.

    Returns
    -------
    night : `str` or `None`
        The night as text, or `None`.
    """
    if night is None:
        return None
    return night.isoformat() if isinstance(night, date) else str(night).strip()


def newest_frame(target: Target) -> FrameRecord | None:
    """Give the target's light frame taken last.

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    frame : `FrameRecord` or `None`
        The light frame with the latest start time, or `None` when the
        target has no timed light frame.
    """
    timed = [
        frame for frame in target.frames if str(frame.role).upper() == "LIGHT" and frame.timestamp is not None
    ]
    return max(timed, key=lambda frame: frame.timestamp or 0.0) if timed else None


def _default_telescope_name(config: Any, configuration: ObservatoryConfiguration | None) -> str:
    """Choose the telescope folder new light frames are moved into.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    configuration : `ObservatoryConfiguration` or `None`
        The equipment the caller named.

    Returns
    -------
    name : `str`
        The configuration's telescope, else the settings' telescope with the
        primary focal length, else the first telescope the settings list,
        else ``"Unknown"``.
    """
    if configuration is not None:
        return configuration.telescope.name
    telescopes = list(config.get_telescopes())
    primary = config.get_primary_focal_length_mm()
    if primary:
        for telescope in telescopes:
            if round(telescope.focal_length_mm) == round(primary):
                return telescope.name
    return telescopes[0].name if telescopes else "Unknown"


def _is_inside(path: str, folder: str) -> bool:
    """Say whether a path lies inside a folder.

    Parameters
    ----------
    path : `str`
        The path.
    folder : `str`
        The folder.

    Returns
    -------
    inside : `bool`
        `True` when ``path`` is the folder or below it.
    """
    real_path, real_folder = os.path.realpath(path), os.path.realpath(folder)
    return os.path.commonpath([real_path, real_folder]) == real_folder


def _is_sorted_light(path: str, frames_folder: str) -> bool:
    """Say whether a path already lies where the library keeps light frames.

    The library keeps a light frame at ``lights/<target>/<telescope>/
    <camera>/<file>``. A file there, or a telescope or camera folder there,
    is indexed where it is. Anything else (a file outside the library, or
    one loose in a target folder) is sorted into place first.

    Parameters
    ----------
    path : `str`
        A file or folder.
    frames_folder : `str`
        The library's frames folder.

    Returns
    -------
    sorted_light : `bool`
        `True` when the path is a telescope folder of a target, or below one.
    """
    lights = os.path.join(frames_folder, "lights")
    if not _is_inside(path, lights):
        return False
    depth = len(os.path.relpath(os.path.realpath(path), os.path.realpath(lights)).split(os.sep))
    return depth >= (4 if os.path.isfile(path) else 2)


def _fits_files(path: str) -> list[str]:
    """List the FITS files a path names: the file itself, or a folder's files.

    Parameters
    ----------
    path : `str`
        A FITS file or a folder.

    Returns
    -------
    files : `list` [`str`]
        The FITS files, sorted. Files in `_excluded` folders are left out.
    """
    if os.path.isfile(path):
        return [path] if path.lower().endswith(FITS_SUFFIXES) else []
    found = []
    for root, directories, files in os.walk(path):
        directories[:] = [name for name in directories if not name.startswith("_excluded")]
        found.extend(os.path.join(root, name) for name in files if name.lower().endswith(FITS_SUFFIXES))
    return sorted(found)


def add_frame_files(
    catalog: Any,
    target: Target,
    paths: Sequence[str],
    *,
    camera: str | None = None,
    configuration: ObservatoryConfiguration | None = None,
) -> Page[FrameRecord]:
    """Bring files into the library as frames of a target, and save it.

    A file (or folder) that is not yet where the library keeps light frames
    (``lights/<target>/<telescope>/<camera>``) is sorted into place: a light
    frame goes under the target, a dark, bias or flat frame to the
    calibration folders, and frames the stacker set aside are skipped. A
    file already in place is not moved. Every light frame
    that ends up under the target is indexed with what its header says, and
    each frame is matched to its observatory configuration.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog's state.
    target : `Target`
        The target the light frames belong to.
    paths : `Sequence` [`str`]
        FITS files, or folders to search.
    camera : `str`, optional
        The camera name, instead of the one in each header.
    configuration : `ObservatoryConfiguration`, optional
        The equipment the frames were taken with. Its telescope names the
        library folder that light frames are moved into. Left out, the
        settings' main telescope.

    Returns
    -------
    frames : `Page` [`FrameRecord`]
        The frame records of the files added or updated, in the order found.

    Raises
    ------
    InvalidArgumentError
        If ``paths`` is a single string, or names a picture (``.jpg``,
        ``.png``, ``.tiff``) rather than a FITS frame.
    NotFoundError
        If a path does not exist.
    """
    from astrometricslib.pipelines.shared.frame_grouping import add_frame
    from astrometricslib.pipelines.shared.frame_scanning import classify_and_sort_fits_files

    if isinstance(paths, str | bytes):
        raise InvalidArgumentError("paths must be a list of paths, not one string.")
    config = catalog._config
    frames_folder = str(config.get_frames_path())
    to_index: list[str] = []
    to_move: list[str] = []
    for path in map(os.fspath, paths):
        if target_records.is_processed_image(path):
            raise InvalidArgumentError(
                f"{path} is a picture, not a frame. "
                "Set it with targets.update(target, processed_image=path).",
                details={"path": path},
            )
        if not os.path.exists(path):
            raise NotFoundError(f"File not found: {path}", details={"path": path})
        if _is_sorted_light(path, frames_folder):
            to_index.extend(_fits_files(path))
        else:
            to_move.append(path)
    if to_move:
        moved: list[str] = []
        classify_and_sort_fits_files(
            to_move, target.id, config, _default_telescope_name(config, configuration), moved
        )
        lights = os.path.join(frames_folder, "lights")
        to_index.extend(path for path in moved if _is_inside(path, lights))
    records = [add_frame(target, path, "LIGHT", None, camera) for path in dict.fromkeys(to_index)]
    target_records.assign_configurations(catalog, target)
    target_records.mark_touched(catalog, target.id)
    target_records.save_targets(catalog)
    by_path = {frame.path: frame for frame in target.frames}
    return Page.of([by_path.get(record.path, record) for record in records])


def scan_frames(catalog: Any, target: Target | None = None, *, prune_missing: bool = False) -> ReindexReport:
    """Read the frames folder again and index what changed, then save.

    With a target, the folders named after it are read: new frames are
    added, the header of each listed frame whose file changed is read again,
    and the total exposure is recomputed. Without one, every target folder
    under the frames folder's ``lights`` folder is read, and a target is
    created for each folder that has none. When the call runs as a job, the
    job's progress counts the target folders.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog's state.
    target : `Target`, optional
        The target. Left out, every target folder.
    prune_missing : `bool`, optional
        Also drop the records of files that no longer exist.

    Returns
    -------
    report : `ReindexReport`
        Each target's frame count before and after.
    """
    from astrometricslib.foundation.jobs.runner import get_current_job

    if target is not None:
        before = len(target.frames)
        _scan_one(catalog, target, prune_missing)
        return ReindexReport(
            targets=[
                TargetReindexChange(
                    target_id=target.id, frames_before=before, frames_after=len(target.frames)
                )
            ]
        )
    lights_path = os.path.join(str(catalog._config.get_frames_path()), "lights")
    folders = (
        sorted(
            name
            for name in os.listdir(lights_path)
            if os.path.isdir(os.path.join(lights_path, name)) and name != "test_write"
        )
        if os.path.isdir(lights_path)
        else []
    )
    job = get_current_job()
    changes = []
    for index, folder in enumerate(folders):
        if job is not None:
            job.report_count(index, len(folders), f"Indexing target {index + 1}/{len(folders)}: {folder}")
        existing = target_records.get_target(catalog, folder)
        resolved = existing or target_records.create_target(catalog, folder)
        before = len(resolved.frames) if existing is not None else 0
        # Saved one target at a time, so an interrupted run keeps the
        # targets it already finished.
        _scan_one(catalog, resolved, prune_missing)
        changes.append(
            TargetReindexChange(
                target_id=resolved.id,
                frames_before=before,
                frames_after=len(resolved.frames),
                created=existing is None,
            )
        )
    return ReindexReport(targets=changes)


def _scan_one(catalog: Any, target: Target, prune_missing: bool) -> None:
    """Read one target's folders again, match the configurations and save.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog's state.
    target : `Target`
        The target.
    prune_missing : `bool`
        Also drop the records of files that no longer exist.
    """
    target_records.reindex_frames(
        target, prune_missing=prune_missing, catalog_access=catalog._catalog_access, refresh_headers=True
    )
    target_records.assign_configurations(catalog, target)
    target_records.mark_touched(catalog, target.id)
    target_records.save_targets(catalog)


def read_frame_header(path: str) -> fits.Header:
    """Read the first header (HDU 0) of a FITS file, without its pixels.

    Parameters
    ----------
    path : `str`
        The FITS file.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A copy of the file's first header.

    Raises
    ------
    NotFoundError
        If the file does not exist.

    Notes
    -----
    A file that is cut off or damaged raises one of
    `astrometricslib.exceptions.FITS_READ_ERRORS`.
    """
    from astrometricslib._internal.drivers.fits_access import read_header

    if not os.path.exists(path):
        raise NotFoundError(f"File not found: {path}", details={"path": path})
    return read_header(path, first_hdu_only=True)


def target_nights(target: Target, night: str | date | None = None) -> Page[TargetSession]:
    """Group a target's frames into sessions, one per night and setting.

    Frames go in the same session when they were taken on the same night
    (noon to noon, local time) with the same gain and offset. A change of
    filter or exposure length does not start a new session. Frames with no
    start time are ignored.

    Parameters
    ----------
    target : `Target`
        The target.
    night : `str` or `date`, optional
        Only the sessions of this night, ``YYYY-MM-DD``.

    Returns
    -------
    sessions : `Page` [`TargetSession`]
        The sessions, ordered by night.
    """
    wanted = night_text(night)
    found = derive_target_sessions(target.id, target.frames)
    return Page.of([
        session for session in found if wanted is None or session.night_date.isoformat() == wanted
    ])


def _target_of_path(catalog: Any, path: str) -> Target | None:
    """Find the target a library frame belongs to, from its folder.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog's state.
    path : `str`
        A frame path, ``<frames>/lights/<target>/...``.

    Returns
    -------
    target : `Target` or `None`
        The target the folder names, or `None` when the path is not under a
        ``lights`` folder or names no target.
    """
    parts = os.path.normpath(path).split(os.sep)
    positions = [
        index for index, part in enumerate(parts) if part.lower() == "lights" and index + 2 < len(parts)
    ]
    if not positions:
        return None
    return target_records.get_target(catalog, parts[positions[-1] + 1])


def delete_frames(
    catalog: Any, target: Target | None, frames: Sequence[FrameRecord | str]
) -> ImageDeletionResult:
    """Delete frame files, drop their records and clear their measurements.

    Each file is removed from disk (a file already gone counts as removed),
    its record is removed from its target, which is saved, and its stored
    quality measurements are removed. A frame's target is the one given, or
    else the one its ``lights/<target>`` folder names.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog's state.
    target : `Target` or `None`
        The target the frames belong to, or `None` to find each frame's
        target from its folder.
    frames : `Sequence` [`FrameRecord` or `str`]
        The frames, or their paths.

    Returns
    -------
    result : `ImageDeletionResult`
        ``deleted`` (the paths removed) and ``failed`` (each path that could
        not be removed, with the reason).
    """
    from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path

    paths = [item if isinstance(item, str) else item.path for item in frames]
    deleted: list[str] = []
    failed: list[FailedDeletion] = []
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
            deleted.append(path)
        except OSError as error:
            failed.append(FailedDeletion(path=path, reason=str(error)))
    subject_ids: list[str] = []
    changed: dict[str, Target] = {}
    for path in deleted:
        owner = target if target is not None else _target_of_path(catalog, path)
        if owner is None:
            continue
        changed[owner.id] = owner
        subject_ids.append(owner.frame_subject_id(path))
    gone = set(deleted)
    for owner in changed.values():
        owner.frames = [frame for frame in owner.frames if frame.path not in gone]
        target_records.mark_touched(catalog, owner.id)
    if changed:
        target_records.save_targets(catalog)
    if subject_ids:
        QualityStore(quality_database_path(catalog._config)).delete_frame_measurements(subject_ids)
    return ImageDeletionResult(deleted=deleted, failed=failed)
