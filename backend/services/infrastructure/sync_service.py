"""Synchronize frames between the backend and remote imaging computers."""

import logging
import os
import threading
from typing import Any

from backend.services.infrastructure import thread_management
from backend.services.infrastructure.remote_service import RemoteService


class SyncService:
    """Service responsible for synchronizing data between the backend and.

    external imaging computers (e.g. Astroberry/Raspberry Pi).
    """

    def __init__(self, remote_service: RemoteService = None, config_service=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the SyncService.

        Args:
            remote_service: Injected RemoteService instance.
            config_service: Injected AppConfiguration instance.
        """
        self._remote = remote_service
        self._config = config_service

    def _get_camera_name(self):  # ruff: ignore[missing-return-type-private-function]
        # Helper to get the camera name for path construction
        if not self._config:
            return "UnknownCamera"

        cam = self._config.get_value("Observatory.Camera", "default_primary_camera")
        if not cam:
            cam = self._config.get_value("Camera", "default_primary_camera", fallback="UnknownCamera")
        return cam

    def start_sync(self, object_id: str) -> dict[str, Any]:
        """Start a background synchronization task for a specific target.

        Returns
        -------
        result : `dict`
            Contains ``started`` and ``target_id``.

        Raises
        ------
        RuntimeError
            If the background sync thread fails to start.
        """
        try:
            sync_thread = threading.Thread(
                target=self._sync_target_frames_task, args=[object_id], daemon=True
            )
            sync_thread.start()

            thread_management._syncing[object_id] = sync_thread

            return {"started": True, "target_id": object_id}

        except Exception as e:
            logging.error(f"Failed to start sync for {object_id}: {e}")
            raise RuntimeError(f"Sync failed to start: {e}") from e

    def start_sync_calibration(self, sync_type: str) -> dict[str, Any]:
        """Start a background synchronization task for calibration frames.

        Returns
        -------
        result : `dict`
            Contains ``started``, ``type``, and ``task_id``.

        Raises
        ------
        ValueError
            If `sync_type` is not one of "bias", "dark", or "flat".
        RuntimeError
            If the background sync thread fails to start.
        """
        valid_types = ["bias", "dark", "flat"]
        if sync_type not in valid_types:
            raise ValueError(f"Invalid sync type: {sync_type}")

        try:
            task_id = f"calibration_{sync_type}"

            sync_thread = threading.Thread(target=self._sync_calibration_task, args=[sync_type], daemon=True)
            sync_thread.start()

            thread_management._syncing[task_id] = sync_thread

            return {"started": True, "type": sync_type, "task_id": task_id}

        except Exception as e:
            logging.error(f"Failed to start calibration sync for {sync_type}: {e}")
            raise RuntimeError(f"Sync failed to start: {e}") from e

    def sync_all(self, target_list: list[str]) -> dict[str, Any]:
        """Trigger background sync for all calibration types and targets.

        Returns
        -------
        summary : `dict`
            Contains ``status`` and the per-task ``tasks`` results.
        """
        results = []
        for c_type in ["bias", "dark", "flat"]:
            results.append(self.start_sync_calibration(c_type))

        for target_id in target_list:
            results.append(self.start_sync(target_id))

        return {"status": "all_sync_tasks_started", "tasks": results}

    def sync_all_from_remote(self) -> dict[str, Any]:
        """Trigger background sync of all calibration and remote targets.

        Returns
        -------
        summary : `dict`
            The sync task summary, or an error status if no
            `RemoteService` is configured.
        """
        if not self._remote:
            return {"status": "error", "message": "RemoteService not available"}

        target_list = self._remote.list_remote_targets()
        target_list = [t for t in target_list if t not in ["Bias", "Dark", "Flat"]]
        return self.sync_all(target_list)

    def get_all_active_syncs(self) -> list[str]:
        """Return the object/task ids with a currently running sync.

        Returns
        -------
        sync_ids : `list` of `str`
            The ids currently being synced.
        """
        return list(thread_management._syncing.keys())

    def is_syncing(self, object_id: str) -> bool:
        """Return whether `object_id` has an active sync thread.

        Returns
        -------
        is_active : `bool`
            `True` if `object_id` has a currently running sync.
        """
        return thread_management.is_syncing(object_id)

    # --- Worker Methods ---

    def _sync_target_frames_task(self, target_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Worker task to sync light frames."""
        if not self._config or not self._remote:
            return

        try:
            underscore_target_name = target_name.replace(" ", "_")
            remote_folder = f"{underscore_target_name}/Light"

            camera_name = self._get_camera_name()
            destination_path = os.path.join(
                self._config.get_frames_path(), f"lights/{target_name}/Apertura 75Q/{camera_name}/"
            )

            if not os.path.exists(destination_path):
                os.makedirs(destination_path)

            self._remote.download_target_folder(remote_folder, destination_path, log_callback=logging.info)

        except Exception as e:
            logging.error(f"Error syncing target {target_name}: {e}")

    def _sync_calibration_task(self, sync_type):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Worker task to sync calibration frames."""
        if not self._config or not self._remote:
            return

        try:
            camera_name = self._get_camera_name()
            folder_map = {"dark": "Dark", "bias": "Bias", "flat": "Flat"}
            remote_folder = folder_map.get(sync_type)

            dest_type_map = {"dark": "darks", "bias": "biases", "flat": "flats"}
            destination_path = os.path.join(
                self._config.get_frames_path(), f"{dest_type_map[sync_type]}/{camera_name}/"
            )

            if not os.path.exists(destination_path):
                os.makedirs(destination_path)

            self._remote.download_target_folder(remote_folder, destination_path, log_callback=logging.info)

        except Exception as e:
            logging.error(f"Error syncing {sync_type}: {e}")
