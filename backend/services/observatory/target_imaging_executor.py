"""Purpose: Serve the sequencer's queue from an observing session.

Description: The app's sequencer queue is the queue of one observing
session in the wayfinding library. Adding a sequence records an
observation package and queues it (`ObservationPlanning.edit_queue`);
removing, reordering and changing entries edit the same queue; and
starting the sequencer runs `ObservationExecution.advance_session` on a
background thread until the queue is done. Each cycle checks safety,
slews to the entry's target and captures its exposures.

The session is the current one: tonight's. The service never keeps a
session id between calls, so a backend that runs past midnight, or restarts,
uses the night's one session and never makes a second. It keeps only the
thread, the stop signal, the id of the session being run, and the change of
shape between the session's entries and the sequences the app shows.
Every queue edit goes through the plan service, so each one sends the
``plan:changed`` event and other open windows read the plan again.
"""

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from astrometricslib import observing_night_id
from backend.services.observatory.plan_service import PlanService
from wayfindinglib import ObservationSession, QueueRequest, StartTimeMode, Wayfinder

logger = logging.getLogger(__name__)

SHOWN_STATUSES = {"PENDING": "queued", "RUNNING": "active"}
"""Entry statuses the queue shows, and the word the app uses for each.
Finished entries leave the queue, as they always have."""


class TargetImagingExecutor:
    """Keep the sequencer's session and run its queue on a thread."""

    def __init__(self, wayfinder: Wayfinder, plan_service: PlanService | None = None) -> None:
        """Keep the Wayfinder whose planning and execution do the work.

        Parameters
        ----------
        wayfinder : `Wayfinder`
            The shared Wayfinder.
        plan_service : `PlanService`, optional
            Makes each queue edit and sends the ``plan:changed`` event. With
            none, a plan service with no event sender is used, so edits work
            but nothing is announced.
        """
        self._wayfinder = wayfinder
        self._plan_service = plan_service or PlanService(wayfinder=wayfinder)
        self._running_session_id: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _session(self) -> ObservationSession:
        """Return tonight's session, creating it if needed.

        The library returns the existing session for a night, so this
        never makes a second one. The night is named by the library each
        time, so the answer follows the night.

        Returns
        -------
        session : `ObservationSession`
            The session whose queue the sequencer shows.
        """
        return self._wayfinder.planning.create_plan("empty_session", night_id=observing_night_id(time.time()))

    def _existing_session(self) -> ObservationSession | None:
        """Return tonight's session if it exists, without creating one.

        Returns
        -------
        session : `ObservationSession` or `None`
            Tonight's session, or `None` when none was made yet.
        """
        return self._wayfinder.planning.get_plan(night_id=observing_night_id(time.time()))

    @staticmethod
    def _request(package_id: str, timing: dict[str, Any] | None) -> QueueRequest:
        """Turn the app's timing choice into a queue request.

        Parameters
        ----------
        package_id : `str`
            The recorded package to queue.
        timing : `dict` [`str`, `Any`] or `None`
            ``{"mode": "soonest"}`` or ``{"mode": "at", "time": ISO time}``.

        Returns
        -------
        request : `QueueRequest`
            A request that may start now, or at the given time.
        """
        timing = timing or {"mode": "soonest"}
        if timing.get("mode") == "at" and timing.get("time"):
            start = datetime.fromisoformat(str(timing["time"]).replace("Z", "+00:00"))
            start = start if start.tzinfo else start.replace(tzinfo=UTC)
            return QueueRequest(
                package_id=package_id,
                start_time_mode=StartTimeMode.FIXED,
                requested_start_time=start,
                computed_start_time=start,
            )
        return QueueRequest(
            package_id=package_id,
            start_time_mode=StartTimeMode.SOONEST,
            computed_start_time=datetime.now(UTC),
        )

    def _queue_sequence(self, sequence: dict[str, Any], timing: dict[str, Any] | None) -> str:
        """Record a sequence as a package and add it to the queue.

        Parameters
        ----------
        sequence : `dict` [`str`, `Any`]
            The app's sequence: ``target_name`` and ``items``.
        timing : `dict` [`str`, `Any`] or `None`
            When it may start.

        Returns
        -------
        entry_id : `str`
            The new queue entry's id.
        """
        package = self._plan_service.create_package(sequence["target_name"], plan_items=sequence["items"])
        change = self._plan_service.edit_plan(
            self._session().id, add=[self._request(package.id, timing).model_dump(mode="json")]
        )
        return change.session.queue[-1].id

    def enqueue_sequence(self, sequence: dict[str, Any], timing: dict[str, Any]) -> dict[str, str]:
        """Add one sequence to the queue.

        Parameters
        ----------
        sequence : `dict` [`str`, `Any`]
            The app's sequence: ``target_name`` and ``items``.
        timing : `dict` [`str`, `Any`]
            ``{"mode": "soonest"}`` or ``{"mode": "at", "time": ISO time}``.

        Returns
        -------
        result : `dict`
            ``{"status": "queued"}``.
        """
        entry_id = self._queue_sequence(sequence, timing)
        logger.info("Queued sequence for %s as entry %s", sequence["target_name"], entry_id)
        return {"status": "queued"}

    def remove_from_queue(self, sequence_id: str) -> bool:
        """Remove one entry from the queue.

        Returns
        -------
        removed : `bool`
            `True` if the entry was in the queue and was removed.
        """
        session = self._existing_session()
        if session is None or sequence_id not in {entry.id for entry in session.queue}:
            return False
        self._plan_service.edit_plan(session.id, remove=[sequence_id])
        return True

    def modify_queue_item(self, sequence_id: str, sequence: dict[str, Any]) -> bool:
        """Replace one entry with a changed sequence, in the same place.

        Returns
        -------
        modified : `bool`
            `True` if the entry was found and replaced. The new entry has
            a new id.
        """
        session = self._existing_session()
        if session is None:
            return False
        queue = session.queue
        ids = [entry.id for entry in queue]
        if sequence_id not in ids:
            return False
        old = queue[ids.index(sequence_id)]
        timing = sequence.get("timing") or (
            {"mode": "at", "time": old.requested_start_time.isoformat()}
            if old.start_time_mode == StartTimeMode.FIXED and old.requested_start_time
            else {"mode": "soonest"}
        )
        new_id = self._queue_sequence(sequence, timing)
        order = [new_id if entry_id == sequence_id else entry_id for entry_id in ids]
        self._plan_service.edit_plan(session.id, remove=[sequence_id], order=order)
        return True

    def reorder(self, sequence_ids: list[str]) -> bool:
        """Put the named entries first, in order; the others follow after.

        Returns
        -------
        reordered : `bool`
            `False` if `sequence_ids` is empty.
        """
        if not sequence_ids:
            return False
        session = self._existing_session()
        if session is None:
            return False
        current = [entry.id for entry in session.queue]
        named = [entry_id for entry_id in sequence_ids if entry_id in current]
        order = named + [entry_id for entry_id in current if entry_id not in named]
        self._plan_service.edit_plan(session.id, order=order)
        return True

    def create_plan(self, target_name: str, items: list[dict[str, Any]]) -> Any:
        """Build an imaging sequence plan for a library target.

        Parameters
        ----------
        target_name : `str`
            The id of the library target the plan is for.
        items : `list` [`dict`]
            The plan items, each with ``count``, ``exposure`` and ``filter``.

        Returns
        -------
        plan : `SequencePlan`
            The sequence plan from the wayfinding library's planning.
        """
        return self._wayfinder.planning.create_plan("sequence", target=target_name, plan_items=items)

    def get_queue(self) -> list[dict[str, Any]]:
        """Return the queue as the sequences the app shows.

        Returns
        -------
        queue : `list` [`dict`]
            One sequence per waiting or running entry: ``id``,
            ``target_name``, ``items``, ``total_duration``, ``timing`` and
            ``status``.
        """
        session = self._existing_session()
        if session is None:
            return []
        sequences = []
        for entry in session.queue:
            if entry.status.value not in SHOWN_STATUSES:
                continue
            items = [
                {
                    "count": request.count,
                    "exposure": request.exposure_sec,
                    "filter": request.filter.value,
                    "duration": request.count * request.exposure_sec,
                }
                for request in entry.exposure_requests
            ]
            fixed = entry.start_time_mode == StartTimeMode.FIXED and entry.requested_start_time is not None
            timing = (
                {"mode": "at", "time": entry.requested_start_time.isoformat()}
                if fixed
                else {"mode": "soonest"}
            )
            sequences.append({
                "id": entry.id,
                "target_name": entry.target_id,
                "items": items,
                "total_duration": sum(item["duration"] for item in items),
                "timing": timing,
                "status": SHOWN_STATUSES[entry.status.value],
            })
        return sequences

    def begin_imaging(self) -> None:
        """Start running the queue on a background thread."""
        if not self.get_queue():
            logger.warning("Attempted to begin imaging with an empty queue.")
            return
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Imaging already in progress.")
            return
        self._stop.clear()
        session_id = self._existing_session().id
        self._running_session_id = session_id

        def run() -> None:
            """Run the queue; the top of a background thread."""
            try:
                session = self._wayfinder.execution.advance_session(session_id, stop=self._stop)
                if session is not None:
                    logger.info("Sequencer stopped: %s %s", session.status, session.status_detail)
            except Exception:  # the top of a background thread: log it, never lose it
                logger.exception("Imaging loop crashed")

        self._thread = threading.Thread(target=run, name="sequencer", daemon=True)
        self._thread.start()
        logger.info("Imaging sequence started.")

    def pause_imaging(self) -> None:
        """Stop running the queue after the current entry; keep the queue."""
        self._stop.set()
        logger.info("Imaging paused.")

    def abort_imaging(self) -> None:
        """Stop running the queue and skip every waiting entry."""
        self._stop.set()
        session_id = self._running_session_id
        if session_id is not None:
            self._wayfinder.execution.abort_session(session_id, "Aborted from the sequencer.")
            self._running_session_id = None
        logger.info("Imaging aborted.")
