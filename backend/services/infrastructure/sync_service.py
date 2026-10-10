"""Purpose: Start frame and log syncs from the observatory computer.

Description: Copying frames and logs from the computer at the telescope
is `control.remote` in the wayfinding library: it decides the remote
folder, the library layout and the calibration folders, and records the
pointing error of each new plate-solved frame. This service only runs a
frame sync on a background thread (so ``telescope:is_syncing`` can report
it) and passes the log sync on, keeping the reply the app shows.
"""

import logging
import threading
from typing import Any

from astrometricslib import AstrometricsError
from backend.services.infrastructure import thread_management
from wayfindinglib import ObservatoryControl

logger = logging.getLogger(__name__)


class SyncService:
    """Run frame syncs in the background and pass log syncs on."""

    def __init__(self, observatory_api: ObservatoryControl) -> None:
        """Keep the observatory control.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`, which copies the files.
        """
        self._observatory = observatory_api

    def start_sync(self, object_id: str) -> dict[str, Any]:
        """Start copying one target's (or calibration folder's) new frames.

        Parameters
        ----------
        object_id : `str`
            The target, or ``Bias``, ``Dark`` or ``Flat``.

        Returns
        -------
        result : `dict`
            ``started`` and ``target_id``.
        """

        def run() -> None:
            """Copy the frames; the top of a background thread."""
            try:
                self._observatory.remote.sync_frames(object_id)
            except AstrometricsError as error:
                logger.warning("Sync of %s failed (%s): %s", object_id, error.code, error.message)
            except Exception:  # the top of a background thread: log it, never lose it
                logger.exception("Sync of %s failed", object_id)

        sync_thread = threading.Thread(target=run, name=f"sync-{object_id}", daemon=True)
        sync_thread.start()
        thread_management._syncing[object_id] = sync_thread
        return {"started": True, "target_id": object_id}

    def get_all_active_syncs(self) -> list[str]:
        """Return the target ids with a sync running.

        Returns
        -------
        sync_ids : `list` [`str`]
            The ids currently being synced.
        """
        return list(thread_management._syncing.keys())

    def is_syncing(self, object_id: str) -> bool:
        """Return whether `object_id` has a sync running.

        Returns
        -------
        is_active : `bool`
            `True` while its sync thread runs.
        """
        return thread_management.is_syncing(object_id)

    def sync_telescope_logs(self) -> dict[str, Any]:
        """Copy the new guide and Ekos logs and store their guiding samples.

        The pointing errors of plate-solved frames are recorded when the
        frames themselves are synced, so this reports none of its own.

        Returns
        -------
        summary : `dict` [`str`, `Any`]
            ``status``, ``guideLogsDownloaded``, ``guidingSamplesIngested``,
            ``fitsSolvesRecorded`` (always 0 here) and ``message``.
        """
        result = self._observatory.remote.sync_logs(register_job=False)
        guide_logs = result.get("guide_logs") or {}
        downloaded = guide_logs.get("to_download", 0) if guide_logs.get("supported") else 0
        samples = (result.get("ingested") or {}).get("guide_samples_stored", 0)
        return {
            "status": "success",
            "guideLogsDownloaded": downloaded,
            "guidingSamplesIngested": samples,
            "fitsSolvesRecorded": 0,
            "message": (
                f"Synced {downloaded} guide logs ({samples} samples). Plate-solve pointing errors are "
                "recorded when frames are synced."
            ),
        }
