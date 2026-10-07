"""Purpose: `control.remote`, the observatory computer's files and logs.

Description: Lists the folders and files on the computer at the
telescope, checks that it can be reached, compares a target's frames
there with the drive and the library, and copies new frames and logs
into the library. Copying adds files and records and never deletes one.
The work is done in `tasks.control_tasks.remote_transfer_tasks`,
`ekos_log_ingestion` and `guiding_log_ingestion`.
"""

import builtins
from collections.abc import Callable
from typing import Any, Literal

from astrometricslib import (
    InvalidArgumentError,
    NotFoundError,
    Target,
    background_job,
    get_current_job,
    registered_job,
)
from wayfindinglib.api.control.context import ControlChild

__all__ = ["RemoteControl"]

ListKind = Literal["folders", "target_folders", "calibration_folders", "unassociated_folders", "files"]
"""What `RemoteControl.list` can list."""


def _target_id(target: str | Target) -> str:
    """Return a target's id, given the target or its id.

    Parameters
    ----------
    target : `str` or `Target`
        The target or its id.

    Returns
    -------
    target_id : `str`
        The id.
    """
    return target if isinstance(target, str) else target.id


def _refuse(kind: str, **arguments: object) -> None:
    """Refuse arguments that a kind of call does not use.

    Parameters
    ----------
    kind : `str`
        Names the kind of call in the message.
    **arguments : `object`
        Each argument that does not apply, with its value. `None` means it
        was not given.

    Raises
    ------
    InvalidArgumentError
        If any of `arguments` was given.
    """
    given = [name for name, value in arguments.items() if value is not None]
    if given:
        raise InvalidArgumentError(f"{kind} does not use {', '.join(given)}.")


class RemoteControl(ControlChild):
    """Read and copy files from the computer at the telescope."""

    def list(
        self, kind: ListKind, folder_name: str | None = None, sizes: bool = False
    ) -> builtins.list[str] | builtins.list[tuple[str, int]]:
        """List folders or files on the observatory computer.

        Parameters
        ----------
        kind : `str`
            ``"folders"`` (every folder), ``"target_folders"`` (folders
            that are not Bias, Dark or Flat), ``"calibration_folders"``
            (Bias, Dark and Flat), ``"unassociated_folders"`` (folders that
            match no library target) or ``"files"`` (the FITS files in
            one folder).
        folder_name : `str`, optional
            For ``"files"`` (where it is needed), the folder to list. For
            the folder kinds, keep only the folder this name refers to.
            Either way the name is matched the way a copy matches it
            (ignoring case, so ``"M 42"`` finds ``"M_42"``), so a made-up
            name never reaches the remote shell.
        sizes : `bool`, optional
            Pair each file with its size in bytes. Used only by
            ``"files"``.

        Returns
        -------
        names : `list` [`str`] or `list` [`tuple` [`str`, `int`]]
            Folder names (with `folder_name`, the one folder it matches,
            or none), file paths relative to the folder, or
            ``(path, size_in_bytes)`` pairs.

        Raises
        ------
        InvalidArgumentError
            If `kind` is unknown, ``"files"`` has no `folder_name`, or an
            argument is given that `kind` does not use.
        NotFoundError
            If `folder_name` matches no folder on the observatory computer.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks as tasks

        if kind == "files":
            if not folder_name:
                raise InvalidArgumentError('kind="files" needs a folder_name.')
            folder = tasks.matching_remote_folder(
                self._context, folder_name, tasks.list_remote_targets(self._context)
            )
            if folder is None:
                raise NotFoundError(f"No folder named {folder_name!r} on the observatory computer.")
            if sizes:
                return tasks.list_remote_files_with_sizes(self._context, folder)
            return tasks.list_remote_files(self._context, folder)
        folder_lists = {
            "folders": tasks.list_remote_targets,
            "target_folders": tasks.list_remote_target_folders,
            "calibration_folders": tasks.list_remote_calibration_folders,
            "unassociated_folders": tasks.discover_unassociated_remote_targets,
        }
        if kind not in folder_lists:
            raise InvalidArgumentError(f"kind must be one of: {', '.join([*folder_lists, 'files'])}.")
        _refuse(f"kind={kind!r}", sizes=sizes or None)
        folders = folder_lists[kind](self._context)
        if folder_name is None:
            return folders
        match = tasks.matching_remote_folder(self._context, folder_name, folders)
        return [match] if match is not None else []

    def check_connection(self) -> bool:
        """Check that the observatory computer can be reached.

        Returns
        -------
        connected : `bool`
            Whether the computer answered.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        return remote_transfer_tasks.check_remote_connection(self._context)

    def frame_status(self, target: str | Target) -> dict[str, Any]:
        """Show where a target's frames are: telescope, drive, library.

        Gives the count in each place and the frames that are in one place
        but not the next, so a missing frame can be traced to the step that
        lost it. It only reads. If the observatory computer cannot be
        reached, the drive and library parts are still given.

        Parameters
        ----------
        target : `str` or `Target`
            The target, such as ``"M 57"``.

        Returns
        -------
        status : `dict` [`str`, `Any`]
            The counts and the differences, with a few file names each.
        """
        from wayfindinglib.tasks.control_tasks import frame_status

        return frame_status.build_frame_status(self._context, _target_id(target))

    @background_job("remote_sync", grace_period_seconds=8.0)
    def sync_frames(
        self,
        target: str | Target | None = None,
        dry_run: bool = False,
        files: builtins.list[str] | None = None,
        local_path: str | None = None,
        incremental: bool | None = None,
        log_callback: Callable[[str], None] | None = None,
        register_job: bool | None = None,
    ) -> dict[str, Any]:
        """Bring new frames from the observatory computer into the library.

        The arguments choose what is copied:

        - No `target`: every folder. Bias, Dark and Flat folders go to the
          calibration library, every other folder to its target. One
          folder's failure does not stop the others. Uses `log_callback`
          and `register_job` (record a job in the logs database; default
          `True`).
        - `local_path`: no copy. The FITS files already in that local
          folder are sorted into `target` and indexed.
        - `files`, `log_callback` or `incremental`: copy `target`'s folder
          (only `files` when given; only files not already held unless
          ``incremental=False``) into the target's own library folder,
          whatever the folder is called, and index the frames.
        - A Bias, Dark or Flat `target`: copy that folder into the
          calibration library.
        - Otherwise: copy the new frames of `target`, which must match a
          target folder on the observatory computer. ``dry_run=True`` only
          reports what would be copied.

        Parameters
        ----------
        target : `str` or `Target`, optional
            The target or folder to copy, such as ``"M 13"``.
        dry_run : `bool`, optional
            Only report the plan. Used only by the last case.
        files : `list` [`str`], optional
            Remote file paths to copy.
        local_path : `str`, optional
            A local folder to index instead of copying.
        incremental : `bool`, optional
            `False` copies every file, even those already held.
        log_callback : `Callable` [[`str`], `None`], optional
            Receives progress messages.
        register_job : `bool`, optional
            Whether a copy of every folder records a job.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            Every folder: ``succeeded``, ``failed`` and ``job_id``. A local
            folder or chosen files: ``success`` and ``target``. Calibration:
            ``success`` and the counts. A target: the plan (``remote_folder``,
            ``remote_files``, ``already_held``, ``to_transfer``, ``examples``)
            and, for a real run, ``success`` and ``transferred``. An
            argument that the chosen case does not use is refused with
            `InvalidArgumentError`. A target with no remote folder raises
            `NotFoundError`, and a real run with the frames drive not
            mounted raises `StorageNotMountedError`.
        """
        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks as tasks

        context = self._context
        if target is None:
            _refuse("Copying every folder", files=files, local_path=local_path, incremental=incremental)
            _refuse("Copying every folder", dry_run=dry_run or None)
            return tasks.sync_all_remote_folders(
                context, log_callback, True if register_job is None else register_job
            )
        _refuse("Copying one target", register_job=register_job)
        target_id = _target_id(target)
        if local_path is not None:
            _refuse("Indexing a local folder", dry_run=dry_run or None, files=files, incremental=incremental)
            success = tasks.download_remote_targets(context, target_id, local_path=local_path)
            return {"success": bool(success), "target": target_id}
        if files is not None or log_callback is not None or incremental is not None:
            _refuse("Copying chosen files", dry_run=dry_run or None)
            success = tasks.download_remote_targets(
                context,
                target_id,
                selected_files=files,
                log_callback=log_callback,
                incremental=True if incremental is None else incremental,
            )
            return {"success": bool(success), "target": target_id}
        if tasks.is_calibration_folder(target_id):
            _refuse("Copying a calibration folder", dry_run=dry_run or None)
            return tasks.sync_calibration_folder(context, target_id)
        return tasks.sync_target_frames(context, target_id, dry_run)

    @background_job("remote_sync", grace_period_seconds=8.0)
    def sync_logs(
        self,
        dry_run: bool = False,
        download: bool = True,
        destination_dir: str | None = None,
        register_job: bool = True,
    ) -> dict[str, Any]:
        """Bring the guide and Ekos logs into the library's database.

        Downloads the new guide logs and Ekos analyze logs, then stores the
        guiding samples and one session record per analyze file. Safe to
        repeat: a second run leaves the same data, and nothing is deleted.
        It does not refit the mount's periodic error model; that is
        `control.guiding.refit_spectrum`.

        Parameters
        ----------
        dry_run : `bool`, optional
            Only report which logs on the observatory computer are new.
        download : `bool`, optional
            `False` reads only what is already in `destination_dir`,
            without contacting the observatory computer.
        destination_dir : `str`, optional
            Local folder for the logs. ``ekos_logs`` in the wayfinding
            library's data folder when omitted.
        register_job : `bool`, optional
            Record a real run as a job in the job history, with its log.
            Defaults to `True`.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            The plan (how many logs exist and are new) and, for a real run,
            ``ingested`` (what was stored). Without downloading, only what
            was stored. An unreachable observatory computer raises
            `ExternalServiceError`.

        Raises
        ------
        InvalidArgumentError
            If `dry_run` is asked for without downloading.
        """
        from wayfindinglib.tasks.control_tasks import ekos_log_ingestion, remote_transfer_tasks

        context = self._context
        directory = destination_dir or context.ekos_log_directory()
        if not download and dry_run:
            raise InvalidArgumentError(
                "dry_run compares with the observatory computer, so it needs download."
            )
        with registered_job(
            enabled=register_job and not dry_run and get_current_job() is None,
            job_type="remote_sync",
            target_id="logs",
            package_logger_name="wayfindinglib",
        ):
            if not download:
                return ekos_log_ingestion.ingest_ekos_logs(context, directory, download=False)
            return remote_transfer_tasks.sync_remote_logs(context, dry_run, directory)
