"""Purpose: Run the frame measurement job for the app and announce its result.

Description: The science library measures a target's light frames and stores
the numbers in the quality database
(`astrometrics.processing.record_frame_measurements`). The library also
guards that work: only one run measures a target at a time, and only one run
reads frames at a time in the whole process, so this service does not have to.
It does two small things.

- When the app asks for a measurement, it starts the library call on a
  background thread (so the request returns at once) and announces the result.
- When a frame sync or an ingestion has already measured the frames it added,
  it announces the summaries of those runs.

An announcement is the ``quality:measured`` event sent to every connected app
window, so the screens that show quality numbers can read them again. Every
announcement comes from the summary of the run that stored the rows, never
from a second look at the database.
"""

import logging
import threading
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from astrometricslib import AstrometricsError

logger = logging.getLogger(__name__)


class MeasurementAnnouncer(Protocol):
    """The part of the quality measurement service that announces results."""

    def announce_summaries(self, summaries: Iterable[Mapping[str, Any]]) -> None:
        """Announce the summaries of measurement runs that already finished.

        Parameters
        ----------
        summaries : `Iterable` [`Mapping` [`str`, `Any`]]
            The summaries as plain dicts (snake_case keys), as the frame sync
            returns them under ``measurements``.
        """
        ...


class QualityMeasurementPort(MeasurementAnnouncer, Protocol):
    """Everything the ingestion needs from the quality measurement service."""

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
            ``started`` and ``target_id``.
        """
        ...


class QualityMeasurementService:
    """Start the frame measurement job and announce what it stored.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library, which does the measuring and the guarding.
    socket_manager : `SocketManager`, optional
        Sends events to the app windows. Nothing is announced without it.
    """

    def __init__(self, astrometrics: Any, socket_manager: Any = None) -> None:
        self._astrometrics = astrometrics
        self._socket_manager = socket_manager

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
            ``started`` (always `True`: the thread was started) and
            ``target_id``. The library decides whether the run goes ahead. If
            this target is already being measured the run returns at once and
            measures nothing. The result of a run that measured arrives as the
            ``quality:measured`` event.
        """

        def run() -> None:
            """Measure the frames; the top of a background thread."""
            try:
                summary = self._astrometrics.processing.record_frame_measurements(target_id, limit=limit)
            except AstrometricsError as error:
                logger.warning(
                    "Measuring the frames of %s failed (%s): %s", target_id, error.code, error.message
                )
                return
            except Exception:  # the top of a background thread: log it, never lose it
                logger.exception("Measuring the frames of %s failed", target_id)
                return
            if summary.already_running:
                logger.info("Measuring the frames of %s was skipped: %s", target_id, "; ".join(summary.notes))
                return
            self._announce(
                summary.target_id,
                summary.frames_measured,
                summary.rows_written,
                list(summary.observing_night_ids),
            )

        threading.Thread(target=run, name=f"quality-measure-{target_id}", daemon=True).start()
        return {"started": True, "target_id": target_id}

    def announce_summaries(self, summaries: Iterable[Mapping[str, Any]]) -> None:
        """Announce the summaries of measurement runs that already finished.

        A frame sync or an ingestion measures the frames it adds inside the
        library and returns the summary of each run. Each summary that stored
        rows is announced; the others (nothing measured, or the run was
        skipped because another run had the target) send nothing.

        Parameters
        ----------
        summaries : `Iterable` [`Mapping` [`str`, `Any`]]
            The summaries as plain dicts with the keys ``target_id``,
            ``frames_measured``, ``rows_written`` and ``observing_night_ids``.
        """
        for summary in summaries:
            self._announce(
                str(summary["target_id"]),
                int(summary.get("frames_measured", 0)),
                int(summary.get("rows_written", 0)),
                list(summary.get("observing_night_ids", [])),
            )

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
