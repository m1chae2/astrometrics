"""Purpose: Run the frame measurement job for the app and announce its result.

Description: The science library measures a target's light frames and stores
the numbers in the quality database
(`astrometrics.processing.record_frame_measurements`). This service starts
that job on a background thread when the app asks for it, and tells every
connected app window with the ``quality:measured`` event when new rows were
stored, so the screens that show quality numbers can read them again.

The job also runs by itself after a frame sync. That run happens inside the
library, so the sync service asks this service to announce what it stored
(`announce_rows_since`).
"""

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from astrometricslib import AstrometricsError

logger = logging.getLogger(__name__)


class QualityMeasurementService:
    """Start the frame measurement job and announce what it stored.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library, which does the measuring.
    socket_manager : `SocketManager`, optional
        Sends events to the app windows. Nothing is announced without it.
    """

    def __init__(self, astrometrics: Any, socket_manager: Any = None) -> None:
        self._astrometrics = astrometrics
        self._socket_manager = socket_manager
        self._running: set[str] = set()
        self._lock = threading.Lock()
        # Runs for different targets take turns: each already measures four
        # frames at once, so more at the same time would only slow the app.
        self._run_slot = threading.Lock()

    def start_frame_measurements(self, target_id: str, limit: int | None = None) -> dict[str, Any]:
        """Start measuring a target's light frames in the background.

        Parameters
        ----------
        target_id : `str`
            The target whose frames to measure.
        limit : `int`, optional
            Measure at most this many frames, newest first.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            ``started`` (`False` when a run for this target is already
            going) and ``target_id``. The result of the run arrives as the
            ``quality:measured`` event.
        """
        with self._lock:
            if target_id in self._running:
                return {"started": False, "target_id": target_id}
            self._running.add(target_id)

        def run() -> None:
            """Measure the frames; the top of a background thread."""
            try:
                with self._run_slot:
                    summary = self._astrometrics.processing.record_frame_measurements(target_id, limit=limit)
                self._announce(
                    summary.target_id,
                    summary.frames_measured,
                    summary.rows_written,
                    list(summary.observing_night_ids),
                )
            except AstrometricsError as error:
                logger.warning(
                    "Measuring the frames of %s failed (%s): %s", target_id, error.code, error.message
                )
            except Exception:  # the top of a background thread: log it, never lose it
                logger.exception("Measuring the frames of %s failed", target_id)
            finally:
                with self._lock:
                    self._running.discard(target_id)

        threading.Thread(target=run, name=f"quality-measure-{target_id}", daemon=True).start()
        return {"started": True, "target_id": target_id}

    def announce_rows_since(self, target_id: str, since: datetime) -> None:
        """Announce the frame rows stored for a target since a moment.

        The sync service calls this after a frame sync, because the job that
        the sync starts runs inside the library and returns no summary to the
        backend.

        Parameters
        ----------
        target_id : `str`
            The target that was synced (a calibration folder name stores no
            rows and announces nothing).
        since : `datetime`
            When the sync started, with a time zone.
        """
        target = self._astrometrics.targets.get(target_id)
        if target is None:
            return
        rows = self._astrometrics.quality.query(
            level="frame", target_id=target.id, since=since.astimezone(UTC), latest_only=False
        )
        if rows:
            night_ids = sorted({
                row.subject.observing_night_id for row in rows if row.subject.observing_night_id
            })
            self._announce(target.id, len({row.subject.subject_id for row in rows}), len(rows), night_ids)

    def _announce(
        self, target_id: str, frames_measured: int, rows_written: int, observing_night_ids: list[str]
    ) -> None:
        """Send the ``quality:measured`` event when rows were stored.

        The event is declared in `backend.public_interface.UI_EVENTS`.

        Parameters
        ----------
        target_id : `str`
            The target whose frames were measured.
        frames_measured : `int`
            How many frames the rows belong to.
        rows_written : `int`
            How many rows were stored. Nothing is sent when this is 0.
        observing_night_ids : `list` [`str`]
            The observing nights of the frames measured, sorted.
        """
        if rows_written <= 0 or self._socket_manager is None:
            return
        self._socket_manager.broadcast_ui_event_sync(
            "quality:measured",
            {
                "targetId": target_id,
                "framesMeasured": frames_measured,
                "rowsWritten": rows_written,
                "observingNightIds": observing_night_ids,
            },
        )
