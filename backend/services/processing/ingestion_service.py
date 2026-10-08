"""Orchestrates frame ingestion from Local or Remote sources.

REQ: BKD-3.4: The backend SHALL support ingestion of files from local and
remote directories. REQ: BKD-5.3: The backend SHALL manage a file index of
captured frames associated with targets.
"""

import logging
import os

from astrometricslib import (
    FITS_READ_ERRORS,
    AstrometricsError,
    ConfigurationError,
    ExternalServiceError,
    NotFoundError,
)
from backend.services.infrastructure.base_service import BaseBackgroundService

logger = logging.getLogger(__name__)


class IngestionService(BaseBackgroundService):
    """Orchestrates frame ingestion from Local or Remote sources.

    REQ: BKD-3.4: The backend SHALL support ingestion of files from local and
    remote directories. REQ: BKD-5.3: The backend SHALL manage a file index of
    captured frames associated with targets.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        target_service=None,  # ruff: ignore[missing-type-function-argument]
        config_service=None,  # ruff: ignore[missing-type-function-argument]
        calibration_library=None,  # ruff: ignore[missing-type-function-argument]
        stellar_service=None,  # ruff: ignore[missing-type-function-argument]
        job_service=None,  # ruff: ignore[missing-type-function-argument]
        image_processing_service=None,  # ruff: ignore[missing-type-function-argument]
        wayfinder=None,  # ruff: ignore[missing-type-function-argument]
    ):
        super().__init__(job_service=job_service)
        self._target_service = target_service
        self._config_service = config_service
        self._calibration_library = calibration_library
        self._stellar_service = stellar_service
        self._image_processing_service = image_processing_service
        self._wayfinder = wayfinder

    def _resolve_remote_folder(self, folder_name: str | None) -> str | None:
        """Find the remote folder a target or folder name refers to.

        Uses the library's name matching (`control.remote.list` with a
        `folder_name`), so a copy and this lookup always agree.

        Parameters
        ----------
        folder_name : `str` or `None`
            The target or folder name.

        Returns
        -------
        folder : `str` or `None`
            The matching remote folder name, or `None` if none matches.
        """
        if not folder_name:
            return None
        matches = self._wayfinder.control.remote.list("folders", folder_name=folder_name)
        return matches[0] if matches else None

    def scan_remote_targets(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return list of folders in remote Pictures.

        Returns
        -------
        folders : `list`
            Folder names found in the remote Pictures directory.
        """
        return self._wayfinder.control.remote.list("folders")

    def scan_remote_targets_rpc(self) -> dict:
        """RPC wrapper for scanning remote targets.

        Returns
        -------
        result : `dict`
            A dict with a ``"folders"`` key containing the folder list.
        """
        return {"folders": self.scan_remote_targets()}

    def _list_calibration_files(self) -> dict[str, list[str]]:
        """List remote FITS files for each Dark/Bias/Flat folder found.

        Shared by `get_remote_stats` and `list_remote_files` so the
        file count shown next to the target name and the file list
        the UI lets the user select from always agree.

        Returns
        -------
        files_by_folder : `dict`
            Maps each found remote calibration folder name (e.g.
            ``"Dark"``) to its list of FITS file relative paths.
            Folders not found on the telescope are omitted.
        """
        files_by_folder: dict[str, list[str]] = {}
        for remote_folder in self._wayfinder.control.remote.list("calibration_folders"):
            files = self._wayfinder.control.remote.list("files", folder_name=remote_folder)
            if files:
                files_by_folder[remote_folder] = files
        return files_by_folder

    def get_remote_stats(self, folder):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Return file count for a remote folder resolving the name first.

        REQ: IMG-6.1

        Returns
        -------
        stats : `dict`
            A dict with ``"fileCount"`` and ``"resolvedFolder"`` keys.
        """
        if folder and folder.lower() == "calibration":
            total_count = sum(len(files) for files in self._list_calibration_files().values())
            # Use "Calibration" as the resolved folder name so
            # frontend keeps it
            return {"fileCount": total_count, "resolvedFolder": "Calibration"}

        folder_name = self._resolve_remote_folder(folder)
        if not folder_name:
            folder_name = folder

        count = len(self._wayfinder.control.remote.list("files", folder_name=folder_name))
        return {"fileCount": count, "resolvedFolder": folder_name}

    def list_remote_files(self, folder: str) -> dict:
        """List individual FITS filenames in a remote target folder.

        Returns
        -------
        result : `dict`
            A dict with ``"files"`` and ``"resolvedFolder"`` keys.
        """
        if folder and folder.lower() == "calibration":
            # Prefix each file with its calibration folder ("Dark/foo.fits")
            # so the UI can tell Dark/Bias/Flat frames apart in one list,
            # and so a per-file selection can be routed back to the right
            # folder when ingestion actually downloads them.
            files = [
                f"{folder_name}/{file_path}"
                for folder_name, folder_files in self._list_calibration_files().items()
                for file_path in folder_files
            ]
            return {"files": files, "resolvedFolder": "Calibration"}

        folder_name = self._resolve_remote_folder(folder)
        if not folder_name:
            folder_name = folder
        files = self._wayfinder.control.remote.list("files", folder_name=folder_name)
        return {"files": files, "resolvedFolder": folder_name}

    def get_ingestion_status(self, job_id: str) -> dict:
        """Return ingestion job status, progress, and tail of log lines.

        Returns
        -------
        status : `dict`
            A dict with ``"status"``, ``"progress"``, and ``"logs"``
            keys.

        Raises
        ------
        ConfigurationError
            If no job service is set up, so no job can be looked up.
        NotFoundError
            If there is no job with id ``job_id``.
        """
        if not self._job_service:
            raise ConfigurationError("The job service is not set up, so ingestion jobs cannot be looked up.")

        job = self._job_service.get_job(job_id)
        if not job:
            raise NotFoundError(f"There is no ingestion job {job_id}.", details={"job_id": job_id})

        # We can fetch job log tail using image_processing_service
        logs = self._image_processing_service.fetch_job_log_tail(job_id, 100)
        progress_pct = 0
        if getattr(job, "progress_total", 0) > 0:
            progress_pct = int(
                (getattr(job, "progress_current", 0) / getattr(job, "progress_total", 1)) * 100
            )

        return {"status": getattr(job, "status", "unknown"), "progress": f"{progress_pct}%", "logs": logs}

    def start_ingestion_by_args(
        self,
        type: str,
        sourcePath: str,  # ruff: ignore[invalid-argument-name] -- must match the RPC param name dispatched by rpc_router.py
        targetName: str | None = None,  # ruff: ignore[invalid-argument-name] -- must match the RPC param name dispatched by rpc_router.py
        telescope: str | None = None,
        selectedFiles: list | None = None,  # ruff: ignore[invalid-argument-name] -- must match the RPC param name dispatched by rpc_router.py
    ) -> dict:
        """Start ingestion from structured arguments.

        Returns
        -------
        result : `dict`
            A dict with a ``"jobId"`` key for the submitted job.
        """
        payload = {
            "type": type,
            "sourcePath": sourcePath,
            "targetName": targetName,
            "telescope": telescope,
            "selectedFiles": selectedFiles,
        }
        job_id = self.start_ingestion(payload)
        return {"jobId": job_id}

    def start_ingestion(self, payload):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Start an ingestion background task.

        Returns
        -------
        job_id : `str`
            The id of the active or newly submitted job.
        """
        target_name = payload.get("targetName") or "unknown"
        if self._job_service:
            active_jobs = self._job_service.get_jobs_for_target(
                target_name, job_type="ingestion", status="started"
            )
            for job in active_jobs:
                return job.id

        # REQ: IMG-5.3: Isolated log file per job
        safe_target = target_name.replace(" ", "_").replace("/", "_")
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = self._config_service.get_logs_path()
        log_file = str(log_dir / f"ingest_{safe_target}_{timestamp}.log")

        return self._submit_job(
            target_name, "ingestion", self._run_ingestion, payload, log_file_path=log_file
        )

    def start_reindex(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Start a full library re-indexing background task.

        REQ: BKD-5.3

        Returns
        -------
        job_id : `str`
            The id of the active or newly submitted job.
        """
        if self._job_service:
            active_jobs = self._job_service.get_jobs_for_target(
                "global", job_type="reindex", status="started"
            )
            for job in active_jobs:
                # Check if it's actually in our memory pool
                if self.is_processing(job.id):
                    return job.id
                else:
                    # Stale job from previous run/crash! Mark as failed.
                    self._job_service.update_job(
                        job.id, status="failed", status_message="Backend restarted while job was running."
                    )

        # REQ: IMG-5.3: Isolated log file per job
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = self._config_service.get_logs_path()
        log_file = str(log_dir / f"reindex_{timestamp}.log")

        return self._submit_job("global", "reindex", self._run_reindex, log_file_path=log_file)

    def _run_reindex(
        self, job_id: str, target_id: str, log_file_path: str | None = None, **kwargs: object
    ) -> bool:
        """Background task to re-index all files in the library.

        Parameters
        ----------
        job_id : `str`
            The job this run records into.
        target_id : `str`
            Not used: a re-index covers the whole library.
        log_file_path : `str`, optional
            Where the job's own log file is written.
        **kwargs : `object`
            Not used; the job runner passes its own options.

        Returns
        -------
        success : `bool`
            `True` once re-indexing completes.
        """
        from astrometricslib import capture_job_logs

        with capture_job_logs(
            job_id=job_id,
            log_file_path=log_file_path,
            job_store=self._job_service.repository if self._job_service else None,
        ):
            return self._reindex_library(job_id)

    def _reindex_library(self, job_id: str) -> bool:
        """Re-index every target folder and calibration frame, with progress.

        Returns
        -------
        success : `bool`
            `True` once re-indexing completes.
        """
        self._log(job_id, "Starting full library re-index...")

        # 1. Reindex every target folder on disk. The library creates a
        # target for a new folder and saves each target as it finishes.
        def report_progress(index: int, total: int, folder: str) -> None:
            """Log the target being indexed and move the job's progress."""
            self._log(job_id, f"Indexing target {index + 1}/{total}: {folder}")
            if self._job_service:
                self._job_service.update_job(job_id, progress=int(((index + 1) / total) * 100))

        report = self._target_service.reindex_library(prune_missing=True, on_progress=report_progress)
        self._log(job_id, f"Light frames of {len(report.targets)} target folders indexed and saved.")

        # 3. Refresh calibration frames
        self._log(job_id, "Scanning dark frames...")
        self._calibration_library.refresh_dark_frames(prune_missing=True)

        self._log(job_id, "Scanning bias frames...")
        self._calibration_library.refresh_bias_frames(prune_missing=True)

        self._log(job_id, "Scanning flat frames...")
        self._calibration_library.refresh_flat_frames(prune_missing=True)

        self._log(job_id, "Calibration frames indexed. Saving library...")
        self._calibration_library.save_library()

        # 4. Migrate Stellar Objects to SQLite
        if hasattr(self._target_service, "stellar_service") and self._target_service.stellar_service:
            self._log(job_id, "Migrating stellar objects to SQLite...")
            self._target_service.stellar_service.save_objects()
        elif hasattr(self, "_stellar_service") and self._stellar_service:
            self._log(job_id, "Migrating stellar objects to SQLite...")
            self._stellar_service.save_objects()

        self._log(job_id, "Re-index complete.")
        return True

    def _log(self, job_id: str, message: str) -> None:
        """Show a progress line on the job and write it to the log.

        Inside the job's `capture_job_logs` block the line also reaches the
        job's own log file and rows.

        Parameters
        ----------
        job_id : `str`
            The job the line belongs to.
        message : `str`
            The line to write.
        """
        if self._job_service:
            self._job_service.update_job(job_id, status_message=message)
        logger.info("[Job %s] %s", job_id, message)

    def _run_ingestion(self, job_id, target_id, payload, log_file_path=None, **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-private-function]
        """Background worker for frame ingestion.

        Downloads and sorts frames from a remote or local source.

        Returns
        -------
        success : `bool`
            `True` once ingestion completes.
        """
        from astrometricslib import capture_job_logs

        # Flip the job out of its initial "started" row the moment work
        # actually begins -- without this the status the UI polls never
        # changes until the whole download finishes, so a long transfer
        # looks frozen even while it is progressing normally.
        if self._job_service:
            self._job_service.update_job(
                job_id, status="running", progress=0, status_message="Starting ingestion..."
            )

        # capture_job_logs attaches handlers to both this job's own logger
        # (the name `_log` below writes through) and the shared
        # "astrometricslib" logger, and writes rows to the jobs database so
        # the ingest dialog's log panel has something to show instead of
        # "No logs available.". See astrometricslib.foundation.jobs.runner.
        with capture_job_logs(
            job_id=job_id,
            log_file_path=log_file_path,
            job_store=self._job_service.repository if self._job_service else None,
        ):
            return self._run_ingestion_body(job_id, target_id, payload)

    def _run_ingestion_body(self, job_id, target_id, payload):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Do the actual ingestion work inside an already-captured job log.

        Returns
        -------
        success : `bool`
            `True` once ingestion completes.

        Raises
        ------
        NotFoundError
            If the remote or local source cannot be found.
        AstrometricsError
            If the remote download fails with a named error, which is
            passed on unchanged.
        ExternalServiceError
            If the remote download fails in any other way.
        """
        ingest_type = payload.get("type", "local")
        target_name = payload.get("targetName")
        selected_files = payload.get("selectedFiles", None)

        if target_name and self._target_service:
            # Create target immediately so it appears in UI during
            # long downloads
            target = self._target_service.get_targets(target_name)
            if not target:
                self._target_service.create_target(target_name)
                self._target_service.save_targets()

        # --- 1. Determine Source ---
        source_dir = ""

        if ingest_type == "remote":
            provided_source = payload.get("sourcePath")

            # Check for special 'Calibration' meta-target
            if target_name and target_name.lower() == "calibration":
                self._log(job_id, "Calibration Mode: Scanning for Dark, Bias, Flat folders...")

                # Same lookup get_remote_stats/list_remote_files use, so
                # what this job finds always matches what the dialog
                # showed the user before they clicked Start Ingestion.
                calibration_files_by_folder = self._list_calibration_files()
                for remote_folder, folder_files in calibration_files_by_folder.items():
                    self._log(job_id, f"Found {remote_folder} ({len(folder_files)} files)")

                downloaded_folders = list(calibration_files_by_folder.keys())
                if not downloaded_folders:
                    self._log(job_id, "No calibration folders found on telescope.")
                    raise NotFoundError("No calibration folders found on telescope.")

                # list_remote_files prefixes each calibration file with its
                # folder ("Dark/foo.fits") so a selection spanning
                # Dark/Bias/Flat can be split back out per folder here. A
                # folder with nothing selected in it is skipped rather than
                # downloaded anyway -- this is how "Darks only" works:
                # select only Dark files (e.g. via the "Dark Only" button).
                folder_selected_files_by_folder: dict[str, list[str] | None] = {}
                total_calibration_files = 0
                for rf in downloaded_folders:
                    if selected_files:
                        prefix = f"{rf}/"
                        folder_files = [f[len(prefix) :] for f in selected_files if f.startswith(prefix)]
                    else:
                        folder_files = None
                    folder_selected_files_by_folder[rf] = folder_files
                    if folder_files is not None:
                        total_calibration_files += len(folder_files)
                    else:
                        total_calibration_files += len(calibration_files_by_folder[rf])

                downloaded_count = 0

                for rf in downloaded_folders:
                    folder_selected_files = folder_selected_files_by_folder[rf]
                    if selected_files and not folder_selected_files:
                        self._log(job_id, f"Skipping {rf}: no files selected.")
                        continue

                    self._log(job_id, f"Downloading {rf} into the calibration library...")

                    def calibration_log_callback(msg):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
                        nonlocal downloaded_count
                        self._log(job_id, msg)
                        # rsync reports one "Downloading: <file>" line per
                        # transferred file, so counting them against the
                        # combined Dark+Bias+Flat file count gives real
                        # progress instead of sitting at 0% for the whole
                        # multi-folder transfer.
                        if msg.startswith("Downloading:") and self._job_service and total_calibration_files:
                            downloaded_count += 1
                            progress_pct = min(int((downloaded_count / total_calibration_files) * 100), 99)
                            self._job_service.update_job(job_id, progress=progress_pct)

                    try:
                        self._wayfinder.control.remote.sync_frames(
                            rf, files=folder_selected_files, log_callback=calibration_log_callback
                        )
                    except (AstrometricsError, OSError) as e:
                        self._log(job_id, f"Failed to download {rf}: {e}")

                self._log(job_id, "Download complete.")

            else:
                remote_target = self._resolve_remote_folder(target_name)
                if not remote_target:
                    remote_target = provided_source

                if not remote_target:
                    self._log(job_id, f"Could not find remote folder for '{target_name}'")
                    raise NotFoundError(
                        f"Could not find remote folder for '{target_name}'", details={"target": target_name}
                    )

                total_files = (
                    len(selected_files)
                    if selected_files
                    else len(self._wayfinder.control.remote.list("files", folder_name=remote_target))
                )
                self._log(job_id, f"Found {total_files} files in {remote_target}")

                # Fetch target instance
                target = self._target_service.get_targets(target_name)
                if not target:
                    target = self._target_service.create_target(target_name)

                self._log(job_id, f"Downloading telescope frames for Target {target_name}...")
                try:
                    downloaded_count = 0

                    def ingest_log_callback(msg):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
                        nonlocal downloaded_count
                        self._log(job_id, msg)
                        # rsync reports one "Downloading: <file>" line per
                        # transferred file (wayfindinglib's
                        # download_target_folder), so counting them against
                        # the file count found above gives real progress
                        # instead of sitting at 0% for the whole transfer.
                        if msg.startswith("Downloading:") and self._job_service and total_files:
                            downloaded_count += 1
                            progress_pct = min(int((downloaded_count / total_files) * 100), 99)
                            self._job_service.update_job(job_id, progress=progress_pct)

                    self._wayfinder.control.remote.sync_frames(
                        target_name, files=selected_files, log_callback=ingest_log_callback
                    )
                    self._log(job_id, "Download complete.")
                except AstrometricsError as e:
                    self._log(job_id, f"Download failed: {e}")
                    raise
                except Exception as e:  # rsync or ssh failed in a way the driver did not name
                    self._log(job_id, f"Download failed: {e}")
                    raise ExternalServiceError(f"Download failed: {e}") from e
        else:
            source_dir = payload.get("sourcePath")
            if not os.path.exists(source_dir):
                self._log(job_id, f"Source directory not found: {source_dir}")
                raise NotFoundError(f"Source directory not found: {source_dir}", details={"path": source_dir})

        # --- 2. Ingestion Logic ---
        if ingest_type != "remote":
            # Local ingestion goes through the same library entry point
            # as remote downloads.
            self._wayfinder.control.remote.sync_frames(target_name, local_path=source_dir)
            self._log(job_id, "Local frames ingested successfully.")

        # --- 3. Regeneration ---
        self._log(job_id, "Refreshing Shared State...")

        # Use the injected target_service directly
        if self._target_service:
            # 1. Update Calibration Library
            try:
                if self._calibration_library:
                    self._calibration_library.refresh_dark_frames()
                    self._calibration_library.refresh_bias_frames()
                    self._calibration_library.refresh_flat_frames()
                    self._calibration_library.save_library()
            except (AstrometricsError, *FITS_READ_ERRORS) as e:
                logger.warning("Failed to refresh calibration library in ingestion job: %s", e)

            # 2. Update Targets

            if target_name:
                target = self._target_service.get_targets(target_name)
                if not target:
                    target = self._target_service.create_target(target_name)
                self._target_service.refresh_target_images(target)

            self._target_service.save_targets()

        self._log(job_id, "Ingestion Complete.")
        return True
