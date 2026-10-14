"""Purpose: Keep advancing an observing session's queue until it is done.

Description: `advance_session` runs one cycle: at most one entry. A
session's queue is worked through by calling it again and again.
`run_queue` does that until the queue has nothing left to run, the
session ends or is suspended (for example, because the safety gate does
not permit observing), or the caller asks it to stop. An entry whose
start time is still ahead is waited for, a few seconds at a time.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from wayfindinglib.models.session.observation_session import QueueEntryStatus, SessionStatus

if TYPE_CHECKING:
    from wayfindinglib.models.session.observation_session import ObservationSession

__all__ = ["POLL_SECONDS", "run_queue"]

logger = logging.getLogger(__name__)

POLL_SECONDS = 5.0
"""Wait between cycles while the next entry's start time is still ahead."""

_STOPPING_STATUSES = (SessionStatus.COMPLETED, SessionStatus.ABORTED, SessionStatus.SUSPENDED)


def _pending(session: ObservationSession) -> list:
    """Return the entries still waiting to run.

    Parameters
    ----------
    session : `ObservationSession`
        The session.

    Returns
    -------
    entries : `list` [`QueuedObservationPackage`]
        Pending entries that have a start time.
    """
    return [
        entry
        for entry in session.queue
        if entry.status == QueueEntryStatus.PENDING and entry.computed_start_time is not None
    ]


def run_queue(
    advance_once: Callable[[], ObservationSession],
    stop: threading.Event,
    sleep: Callable[[float], None] = time.sleep,
) -> ObservationSession | None:
    """Advance a session cycle after cycle until there is nothing to run.

    Parameters
    ----------
    advance_once : `Callable` [[], `ObservationSession`]
        Runs one cycle and returns the saved session.
    stop : `threading.Event`
        Set it to stop after the current cycle.
    sleep : `Callable` [[`float`], `None`], optional
        Waits between cycles; tests pass one that returns at once.

    Returns
    -------
    session : `ObservationSession` or `None`
        The session after the last cycle, or `None` if `stop` was set
        before the first.
    """
    session = None
    while not stop.is_set():
        before = {entry.id: entry.status for entry in session.queue} if session else {}
        session = advance_once()
        if session.status in _STOPPING_STATUSES:
            logger.info(
                "Queue of session %s stopped: %s %s", session.id, session.status, session.status_detail
            )
            break
        pending = _pending(session)
        if not pending:
            break
        ran = any(before.get(entry.id) != entry.status for entry in session.queue)
        if not ran:
            sleep(POLL_SECONDS)
    return session
