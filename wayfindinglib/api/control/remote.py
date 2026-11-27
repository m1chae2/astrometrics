"""Purpose: `control.remote`, the observatory computer's files and logs.

Description: Lists the folders and files on the computer at the
telescope, checks that it can be reached, compares a target's frames
there with the drive and the library, and copies new frames and logs
into the library. Copying adds files and records and never deletes one.
Progress goes to the job the call runs in. The work is done in
`tasks.control_tasks.remote_sync`.
"""

from astrometricslib import Target
from astrometricslib.arguments import Page
from astrometricslib.jobs import background_job
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.session.control_results import (
    FrameLocationReport,
    FrameSyncResult,
    LogSyncResult,
    RemoteFile,
)

__all__ = ["RemoteControl"]


class RemoteControl(ControlChild):
    """Read and copy files from the computer at the telescope."""

    def check_connection(self) -> None:
        """Check that the observatory computer can be reached.

        Owner:
            wayfindinglib.tasks.control_tasks.remote_sync.require_connection

        Notes
        -----
        Raises `ExternalServiceError` when the computer does not answer.
        Its ``report`` is ``"status"``: an offline computer is a status,
        not an error.
        """
        from wayfindinglib.tasks.control_tasks.remote_sync import require_connection

        require_connection(self._context)

    def query_folders(self, kind: str = "imaging", text: str | None = None) -> Page[str]:
        """List folders on the observatory computer.

        Owner:
            wayfindinglib.tasks.control_tasks.remote_sync.query_folders

        Parameters
        ----------
        kind : `str`, optional
            ``"imaging"`` (target folders, the default), ``"calibration"``
            (Bias, Dark and Flat), ``"unassociated"`` (folders that match
            no library target) or ``"all"``.
        text : `str`, optional
            Keep only the folder this name refers to, matched the way a
            copy matches it (ignoring case, so ``"M 42"`` finds
            ``"M_42"``).

        Returns
        -------
        folders : `Page` [`str`]
            The folder names.

        Notes
        -----
        Raises `InvalidArgumentError` for an unknown `kind`.
        """
        from wayfindinglib.tasks.control_tasks.remote_sync import query_folders

        return query_folders(self._context, kind=kind, text=text)

    def query_files(self, folder: str) -> Page[RemoteFile]:
        """List the FITS files in one folder on the observatory computer.

        Owner:
            wayfindinglib.tasks.control_tasks.remote_sync.query_files

        Parameters
        ----------
        folder : `str`
            The folder, matched the way a copy matches it, so a made-up
            name never reaches the remote shell.

        Returns
        -------
        files : `Page` [`RemoteFile`]
            Each file's ``path`` inside the folder and ``size_bytes``.

        Notes
        -----
        Raises `NotFoundError` if the folder is not there and
        `ExternalServiceError` (a status) if the computer cannot be reached.
        """
        from wayfindinglib.tasks.control_tasks.remote_sync import query_files

        return query_files(self._context, folder)

    def get_frame_locations(self, target: Target) -> FrameLocationReport:
        """Show where a target's frames are: telescope, drive, library.

        Gives the count in each place and the frames that are in one place
        but not the next, so a missing frame can be traced to the step that
        lost it. It only reads. If the observatory computer cannot be
        reached, the drive and library parts are still given.

        Owner:
            wayfindinglib.tasks.control_tasks.remote_sync.get_frame_locations

        Parameters
        ----------
        target : `Target`
            The target.

        Returns
        -------
        locations : `FrameLocationReport`
            The counts and the differences, with a few file names each.
        """
        from wayfindinglib.tasks.control_tasks.remote_sync import get_frame_locations

        return get_frame_locations(self._context, target)

    @background_job("remote_sync", grace_period_seconds=8.0)
    def sync_frames(
        self,
        target: Target | None = None,
        kind: str | None = None,
        files: list[str] | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> FrameSyncResult:
        """Bring new frames from the observatory computer into the library.

        With a `target`, its folder is copied into the target's own
        library folder (only the files not already held, unless `force`),
        the frames are indexed, and the new frames are measured, which
        takes about 11 s per frame. With `kind`, that calibration folder
        goes to the calibration library. With neither, every folder is
        copied; one folder's failure does not stop the others. The run is
        recorded as a ``remote_sync`` job, or joins the job it runs in.

        Owner:
            wayfindinglib.tasks.control_tasks.remote_sync.sync_frames

        Parameters
        ----------
        target : `Target`, optional
            The target to copy.
        kind : `str`, optional
            ``"bias"``, ``"dark"`` or ``"flat"``: copy that calibration
            folder instead of a target.
        files : `list` [`str`], optional
            Only these remote file paths.
        dry_run : `bool`, optional
            Only report what would be copied (a target, without `files`).
        force : `bool`, optional
            Copy every file, even those already held.

        Returns
        -------
        result : `FrameSyncResult`
            Every folder: ``succeeded``, ``failed``, ``job_id`` and
            ``measurements``. Chosen files or `force`: ``success``,
            ``target`` and ``measurements``. Calibration: ``success`` and
            the counts. A target: the plan (``remote_folder``,
            ``remote_files``, ``already_held``, ``to_transfer``,
            ``examples``) and, for a real run, ``success``, ``transferred``
            and ``measurements``, the summary of each frame measurement run
            the copy started.

        Notes
        -----
        Raises `InvalidArgumentError` for a choice the copy does not use,
        `NotFoundError` when the target has no remote folder, and
        `StorageNotMountedError` when the frames drive is not mounted.
        """
        from wayfindinglib.tasks.control_tasks.remote_sync import sync_frames

        return sync_frames(self._context, target, kind=kind, files=files, dry_run=dry_run, force=force)

    @background_job("remote_sync", grace_period_seconds=8.0)
    def sync_logs(self, dry_run: bool = False) -> LogSyncResult:
        """Bring the guide and Ekos logs into the library's database.

        Downloads the new guide logs and Ekos analyze logs, then stores the
        guiding samples and one session record per analyze file. Safe to
        repeat: a second run leaves the same data, and nothing is deleted.
        It does not refit the mount's periodic error model; that is
        ``review.guiding.measure_spectrum``.

        Owner:
            wayfindinglib.tasks.control_tasks.remote_sync.sync_logs

        Parameters
        ----------
        dry_run : `bool`, optional
            Only report which logs on the observatory computer are new.

        Returns
        -------
        result : `LogSyncResult`
            The plan (how many logs exist and are new) and, for a real run,
            ``ingested`` (what was stored).

        Notes
        -----
        Raises `ExternalServiceError` (a status) when the observatory
        computer cannot be reached.
        """
        from wayfindinglib.tasks.control_tasks.remote_sync import sync_logs

        return sync_logs(self._context, dry_run=dry_run)
