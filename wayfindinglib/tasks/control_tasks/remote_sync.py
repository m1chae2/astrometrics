"""Purpose: The work behind `control.remote`: list, check and copy.

Description: These functions are the owners of the `control.remote`
methods. They choose what to list or copy and hand the work to
`remote_transfer_tasks`, `frame_locations` and `ekos_log_ingestion`.
Progress goes to the job the call runs in: a copy run outside any job
records a ``remote_sync`` job of its own, and a caller that wants the
progress in its own job row runs the call inside
``astrometricslib.jobs.running_as_job``. Copying adds files and records and
never deletes one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrometricslib.arguments import Page
from astrometricslib.exceptions import ExternalServiceError, InvalidArgumentError, NotFoundError
from astrometricslib.jobs import get_current_job, registered_job
from wayfindinglib.models.session.control_results import (
    FrameLocationReport,
    FrameSyncResult,
    LogSyncResult,
    RemoteFile,
)
from wayfindinglib.tasks.control_tasks import remote_transfer_tasks as tasks

if TYPE_CHECKING:
    from astrometricslib import Target
    from wayfindinglib.api.control.context import ControlContext

__all__ = [
    "FOLDER_KINDS",
    "get_frame_locations",
    "query_files",
    "query_folders",
    "require_connection",
    "sync_frames",
    "sync_logs",
]

UNREACHABLE = "The observatory computer is not reachable."
"""The message of the status an unreachable observatory computer gives."""

FOLDER_KINDS = ("imaging", "calibration", "unassociated", "all")
"""The kinds of folder `query_folders` lists: target folders, Bias, Dark and
Flat folders, folders that match no library target, and every folder."""


def require_connection(context: ControlContext) -> None:
    """Check that the observatory computer can be reached.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the remote transfer driver.

    Raises
    ------
    ExternalServiceError
        If the computer does not answer. Its ``report`` is ``"status"``.
    """
    if not tasks.check_remote_connection(context):
        raise ExternalServiceError(UNREACHABLE, report="status", log_level="INFO")


def query_folders(context: ControlContext, kind: str = "imaging", text: str | None = None) -> Page[str]:
    """List folders on the observatory computer.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the remote listing and the target catalog.
    kind : `str`, optional
        One of `FOLDER_KINDS`.
    text : `str`, optional
        Keep only the folder this name refers to, matched the way a copy
        matches it (ignoring case and spaces, so ``"M 42"`` finds
        ``"M_42"``).

    Returns
    -------
    folders : `Page` [`str`]
        The folder names. Empty when the computer cannot be reached.

    Raises
    ------
    InvalidArgumentError
        If `kind` is not one of `FOLDER_KINDS`.
    """
    listings = {
        "imaging": tasks.list_remote_target_folders,
        "calibration": tasks.list_remote_calibration_folders,
        "unassociated": tasks.discover_unassociated_remote_targets,
        "all": tasks.list_remote_targets,
    }
    if kind not in listings:
        raise InvalidArgumentError(f"kind must be one of: {', '.join(FOLDER_KINDS)}.")
    folders = listings[kind](context)
    if text is not None:
        match = tasks.matching_remote_folder(context, text, folders)
        folders = [match] if match is not None else []
    return Page.of(folders)


def query_files(context: ControlContext, folder: str) -> Page[RemoteFile]:
    """List the FITS files in one folder on the observatory computer.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the remote listing.
    folder : `str`
        The folder, matched the way a copy matches it, so a made-up name
        never reaches the remote shell.

    Returns
    -------
    files : `Page` [`RemoteFile`]
        Each file's path inside the folder and its size in bytes.

    Raises
    ------
    NotFoundError
        If `folder` matches no folder on the observatory computer.
    ExternalServiceError
        If no folder matches because the computer cannot be reached.
    """
    match = tasks.matching_remote_folder(context, folder, tasks.list_remote_targets(context))
    if match is None:
        if not tasks.check_remote_connection(context):
            raise ExternalServiceError(UNREACHABLE, report="status", log_level="INFO")
        raise NotFoundError(f"No folder named {folder!r} on the observatory computer.")
    files = tasks.list_remote_files_with_sizes(context, match)
    return Page.of([RemoteFile(path=path, size_bytes=size) for path, size in files])


def get_frame_locations(context: ControlContext, target: Target) -> FrameLocationReport:
    """Show where a target's frames are: telescope, drive, library.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the remote listing and the library.
    target : `Target`
        The target.

    Returns
    -------
    locations : `FrameLocationReport`
        The counts and the differences, with a few file names each.
    """
    from wayfindinglib.tasks.control_tasks import frame_locations

    return FrameLocationReport.model_validate(frame_locations.build_frame_locations(context, target.id))


def _sync_folder(
    context: ControlContext, folder: str, files: list[str] | None, dry_run: bool, force: bool
) -> dict[str, Any]:
    """Copy or plan one target's or calibration folder's frames.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers and the library.
    folder : `str`
        The target id, or ``Bias``, ``Dark`` or ``Flat``.
    files : `list` [`str`] or `None`
        Remote file paths to copy, or `None` for every new file.
    dry_run : `bool`
        Only report the plan.
    force : `bool`
        Copy every file, even those already held.

    Returns
    -------
    result : `dict` [`str`, `Any`]
        See `sync_frames`.

    Raises
    ------
    InvalidArgumentError
        If a choice is given that the folder's kind of copy does not use.
    """
    if files is not None or force:
        if dry_run:
            raise InvalidArgumentError("dry_run plans a copy of new frames; it does not take files or force.")
        job = get_current_job()
        summaries: list[Any] = []
        success = tasks.download_remote_targets(
            context,
            folder,
            selected_files=files,
            log_callback=job.info if job is not None else None,
            incremental=not force,
            measurement_summaries=summaries,
        )
        return {
            "success": bool(success),
            "target": folder,
            "measurements": [summary.model_dump(mode="json") for summary in summaries],
        }
    if tasks.is_calibration_folder(folder):
        if dry_run:
            raise InvalidArgumentError("A calibration folder has no dry run.")
        return tasks.sync_calibration_folder(context, folder)
    return tasks.sync_target_frames(context, folder, dry_run)


def _calibration_folder(context: ControlContext, kind: str) -> str:
    """Find the remote folder that holds one kind of calibration frame.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the remote listing.
    kind : `str`
        ``"bias"``, ``"dark"`` or ``"flat"``.

    Returns
    -------
    folder : `str`
        The folder's name on the observatory computer, such as ``"Dark"``.

    Raises
    ------
    InvalidArgumentError
        If `kind` is not a calibration kind.
    NotFoundError
        If the observatory computer has no folder of that kind.
    ExternalServiceError
        If no folder matches because the computer cannot be reached.
    """
    if kind.strip().lower() not in tasks.CALIBRATION_FOLDER_KINDS:
        raise InvalidArgumentError('kind must be "bias", "dark" or "flat".')
    for folder in tasks.list_remote_calibration_folders(context):
        if folder.strip().lower() == kind.strip().lower():
            return folder
    if not tasks.check_remote_connection(context):
        raise ExternalServiceError(UNREACHABLE, report="status", log_level="INFO")
    raise NotFoundError(f"No {kind} folder on the observatory computer.")


def sync_frames(
    context: ControlContext,
    target: Target | None = None,
    kind: str | None = None,
    files: list[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> FrameSyncResult:
    """Bring new frames from the observatory computer into the library.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers and the library.
    target : `Target`, optional
        The target to copy.
    kind : `str`, optional
        ``"bias"``, ``"dark"`` or ``"flat"``: copy that calibration folder
        into the calibration library instead of a target. With neither
        `target` nor `kind`, every folder is copied.
    files : `list` [`str`], optional
        Only these remote file paths (one target or calibration folder).
    dry_run : `bool`, optional
        Only report what would be copied (one target, no `files`).
    force : `bool`, optional
        Copy every file, even those already held.

    Returns
    -------
    result : `FrameSyncResult`
        See `control.remote.sync_frames`.

    Raises
    ------
    InvalidArgumentError
        If both `target` and `kind` are given, `files`, `dry_run` or
        `force` is given for every folder, or a choice is given that the
        folder's kind of copy does not use.
    """
    if target is not None and kind is not None:
        raise InvalidArgumentError("Give a target or a calibration kind, not both.")
    if target is None and kind is None:
        if files is not None or dry_run or force:
            raise InvalidArgumentError("Copying every folder does not use files, dry_run or force.")
        job = get_current_job()
        return FrameSyncResult.model_validate(
            tasks.sync_all_remote_folders(context, job.info if job is not None else None)
        )
    folder = target.id if target is not None else _calibration_folder(context, kind or "")
    if dry_run:
        return FrameSyncResult.model_validate(_sync_folder(context, folder, files, dry_run, force))
    with registered_job(job_type="remote_sync", target_id=folder):
        return FrameSyncResult.model_validate(_sync_folder(context, folder, files, dry_run, force))


def sync_logs(context: ControlContext, dry_run: bool = False) -> LogSyncResult:
    """Bring the guide and Ekos logs into the library's database.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers and the log ingestion.
    dry_run : `bool`, optional
        Only report which logs on the observatory computer are new.

    Returns
    -------
    result : `LogSyncResult`
        The plan and, for a real run, ``ingested``.

    Notes
    -----
    Raises `ExternalServiceError` (a status) when the observatory computer
    cannot be reached.
    """
    if dry_run:
        return LogSyncResult.model_validate(tasks.sync_remote_logs(context, True))
    with registered_job(job_type="remote_sync", target_id="logs"):
        return LogSyncResult.model_validate(tasks.sync_remote_logs(context, False))
