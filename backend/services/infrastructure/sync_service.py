"""Purpose: Start frame and log syncs from the observatory computer.

Description: Copying frames and logs from the computer at the telescope
is `control.remote` in the wayfinding library: it decides the remote
folder, the library layout and the calibration folders, and records the
pointing error of each new plate-solved frame. This service only runs a
frame sync on a background thread (so ``telescope:is_syncing`` can report
it) and passes the log sync on, keeping the reply the app shows. A log sync
can also start on a background thread, one at a time, for the automatic
download.
"""

import logging
import threading
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from astrometricslib.exceptions import AstrometricsError
from backend.services.infrastructure import thread_management
from backend.services.infrastructure.background_logging import background_log_context
from wayfindinglib import ObservatoryControl

if TYPE_CHECKING:
    from backend.services.processing.quality_measurement_service import MeasurementAnnouncer

logger = logging.getLogger(__name__)

CALIBRATION_FOLDERS = frozenset({"Bias", "Dark", "Flat"})
"""Sync names that are calibration folders, not targets."""

CALIBRATION_FOLDER_NAMES = frozenset(name.lower() for name in CALIBRATION_FOLDERS)
"""The same names in lower case, to compare without regard to case."""

LIBRARY_FOLDER_KINDS = {"darks": "dark", "biases": "bias"}
"""Library folders whose new frames are measured, with their kind."""


class SyncService:
    """Run frame syncs in the background and pass log syncs on."""

    def __init__(
        self,
        observatory_api: ObservatoryControl,
        measurement_announcer: MeasurementAnnouncer | None = None,
        suggestion_trigger: Callable[[list[str]], None] | None = None,
        socket_manager: Any = None,
        calibration_trigger: Callable[[Iterable[str]], Any] | None = None,
    ) -> None:
        """Keep the observatory control.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`, which copies the files.
        measurement_announcer : `MeasurementAnnouncer`, optional
            Told what the sync's measurement runs stored, so it can announce
            the quality rows. Nothing is announced without it.
        suggestion_trigger : `Callable` [[`list` [`str`]], `None`], optional
            Called with the synced target's id when a target sync finishes,
            so the package suggestions can be refreshed (new frames change
            what is complete). It must not raise; a failure is logged here too.
        socket_manager : `SocketManager`, optional
            Sends the ``library:frames_updated`` event as soon as new frames
            are copied and indexed, before they are measured, and the
            ``library:guiding_updated`` event when a log sync stores guiding
            samples. None sends nothing.
        calibration_trigger : `Callable`, optional
            Called with the set of frame kinds (``"dark"``, ``"bias"``) a
            calibration folder sync added, so they are measured. It must not
            raise; a failure is logged here too.
        """
        self._calibration_trigger = calibration_trigger
        self._observatory = observatory_api
        self._measurement_announcer = measurement_announcer
        self._suggestion_trigger = suggestion_trigger
        self._socket_manager = socket_manager
        self._log_sync_lock = threading.Lock()
        self._log_sync_thread: threading.Thread | None = None

    def start_sync(
        self,
        object_id: str,
        on_finished: Callable[[str, Any], None] | None = None,
    ) -> dict[str, Any]:
        """Start copying one target's (or calibration folder's) new frames.

        Parameters
        ----------
        object_id : `str`
            The target, or ``Bias``, ``Dark`` or ``Flat``.
        on_finished : `Callable` [[`str`, `Any`], `None`], optional
            Called on the sync thread when the sync ends, with the id and the
            sync's result (`None` when the sync failed). It must not raise;
            a failure is logged here. The app's own sync button passes none.

        Returns
        -------
        result : `dict`
            ``started`` and ``target_id``. When a sync of the target is
            already running, nothing new starts and ``started`` is `False`,
            with ``reason`` set to ``"already running"``.
        """

        def run() -> None:
            """Copy the frames in a background log context; top of a thread."""
            with background_log_context("sync", target_id=object_id):
                run_sync()

        def run_sync() -> None:
            """Copy the frames, tell the app and call the finish listener."""
            result = None
            try:

                def announce_copied(frames_added: int) -> None:
                    """Tell the app the new frames are in the library."""
                    self._announce_frames_updated(object_id, frames_added)

                result = self._observatory.remote.sync_frames(object_id, on_copied=announce_copied)
                self._announce_measurements(object_id, result)
                self._trigger_calibration_measurement(object_id, result)
                self._trigger_suggestions(object_id)
            except AstrometricsError as error:
                result = None
                logger.warning("Sync of %s failed (%s): %s", object_id, error.code, error.message)
            except Exception:  # the top of a background thread: log it, never lose it
                result = None
                logger.exception("Sync of %s failed", object_id)
            if on_finished is not None:
                try:
                    on_finished(object_id, result)
                except Exception:  # a listener must never break the sync thread: log it
                    logger.exception("The finish listener of the %s sync failed", object_id)

        # The check and the registration share one lock, so two callers (the
        # button, the RPC and the automatic download) cannot both start.
        with thread_management.syncing_lock:
            if thread_management.is_syncing(object_id):
                return {"started": False, "target_id": object_id, "reason": "already running"}
            sync_thread = threading.Thread(target=run, name=f"sync-{object_id}", daemon=True)
            thread_management._syncing[object_id] = sync_thread
            sync_thread.start()
        return {"started": True, "target_id": object_id}

    def _announce_frames_updated(self, object_id: str, frames_added: int) -> None:
        """Tell the app that frames were added, so its frame lists reload.

        This runs after the copy and re-index, before the frames are
        measured, so a new frame shows up at once. A failure is logged and
        never changes the sync's result.

        Parameters
        ----------
        object_id : `str`
            The target (or calibration folder) that was synced.
        frames_added : `int`
            How many frames the sync added.
        """
        if self._socket_manager is None:
            return
        try:
            self._socket_manager.broadcast_ui_event_sync(
                "library:frames_updated", {"targetId": object_id, "framesAdded": int(frames_added)}
            )
        except Exception:  # an announcement must never fail the sync: log it
            logger.exception("Could not announce the new frames of the %s sync", object_id)

    def _trigger_calibration_measurement(self, object_id: str, sync_result: Any) -> None:
        """Measure the dark and bias frames a calibration folder sync added.

        The sync reports where it filed each new frame (``added_by_folder``,
        with folders such as ``darks/...`` and ``biases/...``). Only a
        calibration folder sync counts, and flats are not measured here. A
        failure is logged and never changes the sync's result.

        Parameters
        ----------
        object_id : `str`
            The target (or calibration folder) that was synced.
        sync_result : `FrameSyncResult`
            What the frame sync returned.
        """
        if self._calibration_trigger is None or object_id.strip().lower() not in CALIBRATION_FOLDER_NAMES:
            return
        added_by_folder = getattr(sync_result, "added_by_folder", None) or {}
        kinds = {
            LIBRARY_FOLDER_KINDS[folder_name]
            for folder in added_by_folder
            if (folder_name := str(folder).replace("\\", "/").split("/", 1)[0]) in LIBRARY_FOLDER_KINDS
        }
        if not kinds:
            return
        try:
            self._calibration_trigger(kinds)
        except Exception:  # a trigger must never fail the sync: log it
            logger.exception("Could not start measuring the new calibration frames of the %s sync", object_id)

    def _trigger_suggestions(self, object_id: str) -> None:
        """Ask for a suggestion refresh after a target's frames arrived.

        Calibration folders (``Bias``, ``Dark``, ``Flat``) are not targets and
        are skipped. A failure is logged and never changes the sync's result.

        Parameters
        ----------
        object_id : `str`
            The target (or calibration folder) that was synced.
        """
        if self._suggestion_trigger is None or object_id in CALIBRATION_FOLDERS:
            return
        try:
            self._suggestion_trigger([object_id])
        except Exception:  # a refresh trigger must never fail the sync: log it
            logger.exception("Could not ask for a suggestion refresh after the %s sync", object_id)

    def _announce_measurements(self, object_id: str, sync_result: Any) -> None:
        """Tell the app which quality rows the sync stored.

        The announcement comes from the summaries of the measurement runs the
        sync started (the sync's ``measurements``), not from a look at the
        database. A failure here is logged and never changes the sync's result.

        Parameters
        ----------
        object_id : `str`
            The target (or calibration folder) that was synced.
        sync_result : `FrameSyncResult`
            What the frame sync returned.
        """
        summaries = getattr(sync_result, "measurements", None)
        if self._measurement_announcer is None or not summaries:
            return
        try:
            self._measurement_announcer.announce_summaries(summaries)
        except Exception:  # an announcement must never fail the sync: log it
            logger.exception("Could not announce the quality rows of the %s sync", object_id)

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

    def is_log_sync_running(self) -> bool:
        """Report whether the guide-log copy thread is running.

        Returns
        -------
        is_running : `bool`
            `True` while the log sync thread is alive. It is not counted
            in `get_all_active_syncs`.
        """
        with self._log_sync_lock:
            thread = self._log_sync_thread
        return thread is not None and thread.is_alive()

    def start_log_sync(self, on_finished: Callable[[Any], None] | None = None) -> dict[str, Any]:
        """Start copying the guide and Ekos logs on a background thread.

        Only one log sync runs at a time. The check and the start share one
        lock, so two callers cannot both start. A failure is logged as one
        warning and passed to `on_finished` as `None`; it never raises.

        Parameters
        ----------
        on_finished : `Callable` [[`Any`], `None`], optional
            Called on the sync thread when the sync ends, with the summary
            `sync_telescope_logs` returned (`None` when the sync failed). It
            must not raise; a failure is logged here.

        Returns
        -------
        result : `dict`
            ``started``. When a log sync already runs, nothing new starts and
            ``started`` is `False`, with ``reason`` set to
            ``"already running"``.
        """

        def run() -> None:
            """Copy the logs in a background log context; top of a thread."""
            with background_log_context("guide-log-sync"):
                run_log_sync()

        def run_log_sync() -> None:
            """Copy the logs, then call the finish listener."""
            summary = None
            try:
                summary = self.sync_telescope_logs()
            except AstrometricsError as error:
                logger.warning("Guide log sync failed (%s): %s", error.code, error.message)
            except Exception as error:  # the top of a background thread: log it, never lose it
                logger.warning("Guide log sync failed: %s", error, exc_info=True)
            if on_finished is not None:
                try:
                    on_finished(summary)
                except Exception:  # a listener must never break the sync thread: log it
                    logger.exception("The finish listener of the guide log sync failed")

        with self._log_sync_lock:
            running = self._log_sync_thread
            if running is not None and running.is_alive():
                return {"started": False, "reason": "already running"}
            self._log_sync_thread = threading.Thread(target=run, name="sync-guide-logs", daemon=True)
            self._log_sync_thread.start()
        return {"started": True}

    def _announce_guiding_updated(self, night_ids: list[str], samples: int) -> None:
        """Tell the app that guiding samples were stored.

        A failure is logged and never changes the sync's result.

        Parameters
        ----------
        night_ids : `list` [`str`]
            The observing nights the stored samples belong to.
        samples : `int`
            How many samples the sync stored.
        """
        if self._socket_manager is None:
            return
        try:
            self._socket_manager.broadcast_ui_event_sync(
                "library:guiding_updated", {"nightIds": night_ids, "samplesStored": int(samples)}
            )
        except Exception:  # an announcement must never fail the sync: log it
            logger.exception("Could not announce the new guiding data")

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
        guide_logs = result.guide_logs
        downloaded = (guide_logs.to_download or 0) if guide_logs and guide_logs.supported else 0
        ingested = result.ingested if isinstance(result.ingested, dict) else {}
        samples = ingested.get("guide_samples_stored", 0)
        if samples:
            nights = [str(night) for night in ingested.get("nights", [])]
            self._announce_guiding_updated(nights, samples)
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
