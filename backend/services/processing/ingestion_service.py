"""Orchestrates frame ingestion from Local or Remote sources.

REQ: BKD-3.4: The backend SHALL support ingestion of files from local and
remote directories. REQ: BKD-5.3: The backend SHALL manage a file index of
captured frames associated with targets.
"""

import logging
import os

from astrometricslib import Target
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
        self.remote_service = None
        self._target_service = target_service
        self._config_service = config_service
        self._calibration_library = calibration_library
        self._stellar_service = stellar_service
        self._image_processing_service = image_processing_service
        self._wayfinder = wayfinder

    def _resolve_remote_folder(self, target_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Find a matching remote folder for a given target name.

        Normalization: Lowercase, remove spaces and underscores.

        Returns
        -------
        folder : `str` or `None`
            The matching remote folder name, or `None` if no match was
            found.
        """
        if not target_name:
            return None

        folders = self._wayfinder.control.list_remote_targets()

        normalized_target = target_name.lower().replace(" ", "").replace("_", "")

        # 1. Exact Match
        if target_name in folders:
            return target_name

        # 2. Normalized Match
        for folder in folders:
            normalized_folder = folder.lower().replace(" ", "").replace("_", "")
            if normalized_folder == normalized_target:
                return folder

        # 3. Contains Match (Fallback - be careful with this)
        return None

    def scan_remote_targets(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Return list of folders in remote Pictures.

        Returns
        -------
        folders : `list`
            Folder names found in the remote Pictures directory.
        """
        return self._wayfinder.control.list_remote_targets()

    def scan_remote_targets_rpc(self) -> dict:
        """RPC wrapper for scanning remote targets.

        Returns
        -------
        result : `dict`
            A dict with a ``"folders"`` key containing the folder list.
        """
        return {"folders": self.scan_remote_targets()}

    def get_remote_stats(self, folder):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Return file count for a remote folder resolving the name first.

        REQ: IMG-6.1

        Returns
        -------
        stats : `dict`
            A dict with ``"fileCount"`` and ``"resolvedFolder"`` keys.
        """
        if folder and folder.lower() == "calibration":
            # Check for Dark, Bias, Flat
            folders_to_check = ["Dark", "Bias", "Flat"]
            total_count = 0
            resolved_folders = []

            for f in folders_to_check:
                remote_folder = self._resolve_remote_folder(f)
                if remote_folder:
                    count = len(self._wayfinder.control.list_remote_files(remote_folder))
                    if count != -1:  # returns -1 on error or counts
                        total_count += count
                        resolved_folders.append(remote_folder)

            # Use "Calibration" as the resolved folder name so
            # frontend keeps it
            if resolved_folders:
                return {"fileCount": total_count, "resolvedFolder": "Calibration"}
            else:
                return {"fileCount": 0, "resolvedFolder": "Calibration"}

        folder_name = self._resolve_remote_folder(folder)
        if not folder_name:
            folder_name = folder

        count = len(self._wayfinder.control.list_remote_files(folder_name))
        return {"fileCount": count, "resolvedFolder": folder_name}

    def list_remote_files(self, folder: str) -> dict:
        """List individual FITS filenames in a remote target folder.

        Returns
        -------
        result : `dict`
            A dict with ``"files"`` and ``"resolvedFolder"`` keys.
        """
        folder_name = self._resolve_remote_folder(folder)
        if not folder_name:
            folder_name = folder
        files = self._wayfinder.control.list_remote_files(folder_name)
        return {"files": files, "resolvedFolder": folder_name}

    def get_ingestion_status(self, job_id: str) -> dict:
        """Return ingestion job status, progress, and tail of log lines.

        Returns
        -------
        status : `dict`
            A dict with ``"status"``, ``"progress"``, and ``"logs"``
            keys.
        """
        if not self._job_service:
            return {"status": "failed", "progress": "0%", "logs": []}

        job = self._job_service.get_job(job_id)
        if not job:
            return {"status": "failed", "progress": "0%", "logs": []}

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

    def _run_reindex(self, job_id, target_id, log_file_path=None, **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-private-function]
        """Background task to re-index all files in the library.

        Returns
        -------
        success : `bool`
            `True` once re-indexing completes.
        """
        self._setup_worker_logger(f"job_{job_id}", log_file_path)
        self._log(job_id, "Starting full library re-index...")

        # 1. Discover all targets on disk
        self._log(job_id, "Scanning lights directory...")
        found_target_ids = self._target_service.discover_all_targets()
        total_targets = len(found_target_ids)
        self._log(job_id, f"Found {total_targets} target folders on disk.")

        # 2. Refresh each target
        for i, target_id in enumerate(found_target_ids):
            progress_pct = int(((i + 1) / total_targets) * 100)
            self._log(job_id, f"Indexing target {i + 1}/{total_targets}: {target_id}")
            if self._job_service:
                self._job_service.update_job(job_id, progress=progress_pct)

            target = self._target_service.get_target(target_id)
            if not target:
                target = self._target_service.create_target(target_id)

            self._target_service.refresh_target_images(target, prune_missing=True)

            # REQ: BKD-5.3 - Save incrementally (now efficient with sharding)
            self._target_service.save_target(target)

        self._log(job_id, "Light frames indexed and saved.")

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
            self._target_service.stellar_service.load_stellar_objects()
            self._target_service.stellar_service.save_objects()
        elif hasattr(self, "_stellar_service") and self._stellar_service:
            self._log(job_id, "Migrating stellar objects to SQLite...")
            self._stellar_service.load_stellar_objects()
            self._stellar_service.save_objects()

        self._log(job_id, "Re-index complete.")
        return True

    def _log(self, job_id, message):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        if self._job_service:
            self._job_service.update_job(job_id, status_message=message)

        # Use isolated worker logger if it exists
        worker_logger = logging.getLogger(f"job_{job_id}")
        if worker_logger.handlers:
            worker_logger.info(message)
        else:
            logger.info(f"[Job {job_id}] {message}")

    def _run_ingestion(self, job_id, target_id, payload, log_file_path=None, **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-private-function]
        """Background worker for frame ingestion.

        Downloads and sorts frames from a remote or local source.

        Returns
        -------
        success : `bool`
            `True` once ingestion completes.

        Raises
        ------
        Exception
            If the remote/local source cannot be resolved or
            downloaded, or if a calibration/remote download fails.
        """
        worker_logger = self._setup_worker_logger(f"job_{job_id}", log_file_path)
        ingest_type = payload.get("type", "local")
        target_name = payload.get("targetName")
        telescope_name = payload.get("telescope", "Apertura 75Q")
        selected_files = payload.get("selectedFiles", None)

        if target_name and self._target_service:
            # Create target immediately so it appears in UI during
            # long downloads
            target = self._target_service.get_target(target_name)
            if not target:
                self._target_service.create_target(target_name)
                self._target_service.save_targets()

        # --- 1. Determine Source ---
        source_dir = ""
        temp_dir = ""

        # Use injected config service
        config = self._config_service
        if not config:
            from astrometricslib import get_configuration

            config = get_configuration()

        if ingest_type == "remote":
            provided_source = payload.get("sourcePath")

            # Check for special 'Calibration' meta-target
            if target_name and target_name.lower() == "calibration":
                folders_to_check = ["Dark", "Bias", "Flat"]
                downloaded_folders = []

                self._log(job_id, "Calibration Mode: Scanning for Dark, Bias, Flat folders...")

                total_file_count = 0
                dest_type_map = {"Dark": "darks", "Bias": "biases", "Flat": "flats"}
                downloaded_paths = []

                # Create dummy Target object for calibration
                # downloads to respect strict domain layers
                dummy_target = Target(id="Calibration")

                for folder in folders_to_check:
                    remote_folder = self._resolve_remote_folder(folder)
                    if remote_folder:
                        count = len(self._wayfinder.control.list_remote_files(remote_folder))
                        total_file_count += count
                        downloaded_folders.append(remote_folder)
                        self._log(job_id, f"Found {remote_folder} ({count} files)")
                    else:
                        self._log(job_id, f"Skipping {folder}: Not found on telescope.")

                if not downloaded_folders:
                    self._log(job_id, "No calibration folders found on telescope.")
                    raise Exception("No calibration folders found on telescope.")

                for rf in downloaded_folders:
                    type_dir = dest_type_map.get(rf, rf.lower() + "s")
                    self._log(job_id, f"Downloading {rf} into local subfolder {type_dir}...")
                    try:
                        self._wayfinder.control.download_remote_targets(rf)
                        downloaded_paths.append(os.path.join(config.get_frames_path(), "lights", rf))
                    except Exception as e:
                        self._log(job_id, f"Failed to download {rf}: {e}")

                source_dirs = downloaded_paths
                self._log(job_id, "Download complete.")

            else:
                remote_target = self._resolve_remote_folder(target_name)
                if not remote_target:
                    remote_target = provided_source

                if not remote_target:
                    self._log(job_id, f"Could not find remote folder for '{target_name}'")
                    raise Exception(f"Could not find remote folder for '{target_name}'")

                total_files = (
                    len(selected_files)
                    if selected_files
                    else len(self._wayfinder.control.list_remote_files(remote_target))
                )
                self._log(job_id, f"Found {total_files} files in {remote_target}")

                # Fetch target instance
                target = self._target_service.get_target(target_name)
                if not target:
                    target = self._target_service.create_target(target_name)

                self._log(job_id, f"Downloading telescope frames for Target {target_name}...")
                try:

                    def ingest_log_callback(msg):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
                        self._log(job_id, msg)

                    self._wayfinder.control.download_remote_targets(
                        target_id=target_name, selected_files=selected_files, log_callback=ingest_log_callback
                    )
                    source_dir = os.path.join(config.get_frames_path(), "lights", target_name)
                    self._log(job_id, "Download complete.")
                except Exception as e:
                    self._log(job_id, f"Download failed: {e}")
                    raise Exception(f"Download failed: {e}") from e
        else:
            source_dir = payload.get("sourcePath")
            if not os.path.exists(source_dir):
                self._log(job_id, f"Source directory not found: {source_dir}")
                raise Exception(f"Source directory not found: {source_dir}")

        # --- 2. Ingestion Logic ---
        if ingest_type != "remote":
            if "source_dirs" in locals() and source_dirs:
                scan_list = source_dirs
            else:
                scan_list = [source_dir]

            # Delegate local ingestion to the unified
            # download_remote_targets domain method
            self._wayfinder.control.download_remote_targets(target_id=target_name, local_path=source_dir)
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
            except Exception as e:
                logger.warning(f"Failed to refresh calibration library in ingestion job: {e}")

            # 2. Update Targets
            self._target_service.load_targets(refresh_images=False)

            if target_name:
                target = self._target_service.get_target(target_name)
                if not target:
                    target = self._target_service.create_target(target_name)
                self._target_service.refresh_target_images(target)

            self._target_service.save_targets()
            # Crucial Fix: Reload once more to ensure indices are
            # 100% fresh in shared memory.
            self._target_service.load_targets(refresh_images=False)

        self._log(job_id, "Ingestion Complete.")
        return True
